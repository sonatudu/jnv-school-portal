"""Generate ActivitySession rows for a calendar date.

Idempotent: existing sessions (and their participants) are never updated.
"""

from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from accounts.models import UserCategory

from .models import (
    ActivitySession,
    ActivitySessionParticipant,
    AudienceKind,
    ClassSection,
    ClassTimetableEntry,
    House,
    HouseMasterAssignment,
    HouseStaffRole,
    SchoolCalendarDay,
    StaffDutyAssignment,
    StudentGroup,
    TeachingAssignment,
)


@dataclass
class DayGenerationResult:
    date: object
    created: int = 0
    already_existed: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self):
        parts = [
            f"{self.date}: created {self.created}",
            f"already existed {self.already_existed}",
            f"skipped {self.skipped}",
        ]
        if self.warnings:
            parts.append("warnings: " + "; ".join(self.warnings))
        if self.errors:
            parts.append("errors: " + "; ".join(self.errors))
        return "; ".join(parts)


def generate_sessions_for_date(target_date):
    try:
        calendar_day = SchoolCalendarDay.objects.select_related(
            "academic_year",
            "routine",
        ).get(date=target_date)
    except SchoolCalendarDay.DoesNotExist:
        result = DayGenerationResult(date=target_date)
        result.errors.append("No calendar row for this date.")
        return result
    return generate_sessions_for_calendar_day(calendar_day)


def generate_sessions_for_calendar_days(calendar_days):
    return [generate_sessions_for_calendar_day(day) for day in calendar_days]


def generate_sessions_for_date_range(date_from, date_to):
    """Generate only for existing SchoolCalendarDay rows in the inclusive range."""
    if date_from > date_to:
        result = DayGenerationResult(date=date_from)
        result.errors.append("The start date must be on or before the end date.")
        return [result]
    days = (
        SchoolCalendarDay.objects.filter(date__gte=date_from, date__lte=date_to)
        .select_related("academic_year", "routine")
        .order_by("date")
    )
    return generate_sessions_for_calendar_days(days)


def generate_sessions_for_calendar_day(calendar_day):
    result = DayGenerationResult(date=calendar_day.date)
    year = calendar_day.academic_year
    routine = calendar_day.routine

    if calendar_day.date < year.start_date or calendar_day.date > year.end_date:
        result.errors.append("Calendar date is outside the academic year.")
        return result
    if routine.academic_year_id != year.id:
        result.errors.append("Calendar routine does not belong to the calendar academic year.")
        return result
    if not routine.is_active:
        result.errors.append("Calendar routine is inactive.")
        return result

    slots = routine.slots.select_related("activity_type").order_by(
        "sort_order",
        "start_time",
    )
    for slot in slots:
        if not slot.is_active:
            result.skipped += 1
            continue
        _generate_for_slot(calendar_day, slot, result)
    return result


def _generate_for_slot(calendar_day, slot, result):
    activity_type = slot.activity_type
    if not activity_type.is_active:
        result.skipped += 1
        result.warnings.append(f"{slot.name}: activity type is inactive.")
        return

    kind = activity_type.default_audience_kind
    if kind == AudienceKind.CLASS:
        _generate_class_sessions(calendar_day, slot, result)
    elif kind == AudienceKind.HOUSE:
        _generate_house_sessions(calendar_day, slot, result)
    elif kind == AudienceKind.SCHOOL:
        _generate_school_session(calendar_day, slot, result)
    elif kind == AudienceKind.STUDENT_GROUP:
        _generate_group_sessions(calendar_day, slot, result)
    elif kind == AudienceKind.SELECTED_STUDENTS:
        result.skipped += 1
    elif not kind:
        result.skipped += 1
        result.warnings.append(
            f"{slot.name}: skipped because activity type has no default audience."
        )
    else:
        result.skipped += 1
        result.warnings.append(f"{slot.name}: unknown audience kind {kind!r}.")


def _generate_class_sessions(calendar_day, slot, result):
    entries = list(
        ClassTimetableEntry.objects.filter(
            routine_slot=slot,
            academic_year=calendar_day.academic_year,
        ).select_related("class_section", "subject", "teacher__user")
    )
    scheduled_ids = {entry.class_section_id for entry in entries}

    for section in ClassSection.objects.filter(is_active=True).order_by(
        "grade_number",
        "section_name",
    ):
        if section.id not in scheduled_ids:
            result.skipped += 1
            result.warnings.append(
                f"{slot.name} / {section}: no timetable entry."
            )

    for entry in entries:
        if not entry.class_section.is_active:
            result.skipped += 1
            result.warnings.append(
                f"{slot.name} / {entry.class_section}: class section is inactive."
            )
            continue
        if not entry.subject.is_active:
            result.skipped += 1
            result.warnings.append(
                f"{slot.name} / {entry.class_section}: subject is inactive."
            )
            continue
        teacher_user = entry.teacher.user
        if teacher_user.category != UserCategory.STAFF or not teacher_user.is_active:
            result.skipped += 1
            result.warnings.append(
                f"{slot.name} / {entry.class_section}: teacher is not a usable Staff user."
            )
            continue

        assignment = TeachingAssignment.objects.filter(
            teacher=entry.teacher,
            subject=entry.subject,
            class_section=entry.class_section,
            academic_year=calendar_day.academic_year,
        ).first()

        _get_or_create_session(
            result,
            lookup={
                "date": calendar_day.date,
                "routine_slot": slot,
                "class_section": entry.class_section,
            },
            create_kwargs={
                "date": calendar_day.date,
                "academic_year": calendar_day.academic_year,
                "routine_slot": slot,
                "activity_type": slot.activity_type,
                "name": slot.name,
                "start_time": slot.start_time,
                "end_time": slot.end_time,
                "audience_kind": AudienceKind.CLASS,
                "class_section": entry.class_section,
                "subject": entry.subject,
                "responsible_staff": teacher_user,
                "teaching_assignment": assignment,
            },
            label=f"{slot.name} / {entry.class_section}",
        )


def _generate_house_sessions(calendar_day, slot, result):
    year = calendar_day.academic_year
    for house in House.objects.filter(is_active=True).order_by("name"):
        assignments = list(
            HouseMasterAssignment.objects.filter(
                house=house,
                academic_year=year,
                staff__category=UserCategory.STAFF,
                staff__is_active=True,
            )
            .select_related("staff")
            .order_by("staff_id")
        )
        if not assignments:
            result.skipped += 1
            result.warnings.append(
                f"{slot.name} / {house}: no House Teacher assignment for this year."
            )
            continue

        house_teachers = [
            item
            for item in assignments
            if item.role == HouseStaffRole.HOUSE_TEACHER
        ]
        responsible = (house_teachers[0] if house_teachers else assignments[0]).staff
        if len(assignments) > 1:
            others = ", ".join(
                str(item.staff)
                for item in assignments
                if item.staff_id != responsible.pk
            )
            if others:
                result.warnings.append(
                    f"{slot.name} / {house}: using {responsible}; other house staff: {others}."
                )

        _get_or_create_session(
            result,
            lookup={
                "date": calendar_day.date,
                "routine_slot": slot,
                "house": house,
            },
            create_kwargs={
                "date": calendar_day.date,
                "academic_year": year,
                "routine_slot": slot,
                "activity_type": slot.activity_type,
                "name": slot.name,
                "start_time": slot.start_time,
                "end_time": slot.end_time,
                "audience_kind": AudienceKind.HOUSE,
                "house": house,
                "responsible_staff": responsible,
            },
            label=f"{slot.name} / {house}",
        )


def _generate_school_session(calendar_day, slot, result):
    mods = list(
        StaffDutyAssignment.objects.filter(
            date=calendar_day.date,
            academic_year=calendar_day.academic_year,
            duty_type__unique_per_day=True,
            duty_type__is_active=True,
            staff__category=UserCategory.STAFF,
            staff__is_active=True,
        ).select_related("staff")
    )
    if len(mods) != 1:
        result.skipped += 1
        result.warnings.append(
            f"{slot.name}: skipped school-wide session "
            f"(need exactly one usable unique-per-day MOD, found {len(mods)})."
        )
        return

    _get_or_create_session(
        result,
        lookup={
            "date": calendar_day.date,
            "routine_slot": slot,
            "audience_kind": AudienceKind.SCHOOL,
        },
        create_kwargs={
            "date": calendar_day.date,
            "academic_year": calendar_day.academic_year,
            "routine_slot": slot,
            "activity_type": slot.activity_type,
            "name": slot.name,
            "start_time": slot.start_time,
            "end_time": slot.end_time,
            "audience_kind": AudienceKind.SCHOOL,
            "responsible_staff": mods[0].staff,
        },
        label=slot.name,
    )


def _generate_group_sessions(calendar_day, slot, result):
    mods = list(
        StaffDutyAssignment.objects.filter(
            date=calendar_day.date,
            academic_year=calendar_day.academic_year,
            duty_type__unique_per_day=True,
            duty_type__is_active=True,
            staff__category=UserCategory.STAFF,
            staff__is_active=True,
        ).select_related("staff")
    )
    if len(mods) != 1:
        result.skipped += 1
        result.warnings.append(
            f"{slot.name}: skipped student-group sessions "
            f"(need exactly one usable unique-per-day MOD, found {len(mods)})."
        )
        return

    responsible = mods[0].staff
    groups = StudentGroup.objects.filter(
        academic_year=calendar_day.academic_year,
        is_active=True,
    ).order_by("name")

    for group in groups:
        lookup = {
            "date": calendar_day.date,
            "routine_slot": slot,
            "student_group": group,
        }
        if ActivitySession.objects.filter(**lookup).exists():
            result.already_existed += 1
            continue

        label = f"{slot.name} / {group}"
        try:
            with transaction.atomic():
                session = ActivitySession(
                    date=calendar_day.date,
                    academic_year=calendar_day.academic_year,
                    routine_slot=slot,
                    activity_type=slot.activity_type,
                    name=slot.name,
                    start_time=slot.start_time,
                    end_time=slot.end_time,
                    audience_kind=AudienceKind.STUDENT_GROUP,
                    student_group=group,
                    responsible_staff=responsible,
                )
                session.save()
                _snapshot_group_participants(session, group)
        except IntegrityError:
            if ActivitySession.objects.filter(**lookup).exists():
                result.already_existed += 1
            else:
                result.skipped += 1
                result.warnings.append(
                    f"{label}: could not create session and participant snapshot."
                )
        except ValidationError as exc:
            result.skipped += 1
            result.warnings.append(f"{label}: could not create session ({exc}).")
        else:
            result.created += 1


def _snapshot_group_participants(session, group):
    for membership in group.memberships.select_related("student"):
        student = membership.student
        if not student.is_active:
            continue
        ActivitySessionParticipant.objects.get_or_create(
            session=session,
            student=student,
        )


def _get_or_create_session(result, lookup, create_kwargs, label):
    existing = ActivitySession.objects.filter(**lookup).first()
    if existing:
        result.already_existed += 1
        return False, existing

    try:
        with transaction.atomic():
            session = ActivitySession(**create_kwargs)
            session.save()
    except IntegrityError:
        result.already_existed += 1
        return False, ActivitySession.objects.filter(**lookup).first()
    except ValidationError as exc:
        result.skipped += 1
        result.warnings.append(f"{label}: could not create session ({exc}).")
        return False, None

    result.created += 1
    return True, session
