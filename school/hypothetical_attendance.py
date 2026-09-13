"""Hypothetical weekday timetable, sessions, and random attendance."""

from datetime import date, datetime, time, timedelta
from random import Random

from django.utils import timezone

from accounts.models import UserCategory

from .attendance_roster import students_for_session
from .generation import generate_sessions_for_calendar_days
from .models import (
    AcademicYear,
    ActivitySession,
    ActivityType,
    AttendanceEntry,
    AttendanceStatus,
    AudienceKind,
    ClassGradeOptions,
    ClassGradeStaffAssignment,
    ClassSection,
    ClassTimetableEntry,
    HouseMasterAssignment,
    Routine,
    RoutineSlot,
    SchoolCalendarDay,
    StudentClassMembership,
    StudentHouseMembership,
    Subject,
    TeacherProfile,
    TeachingAssignment,
)

HYPOTHETICAL_YEAR_SPECS = (
    {
        "name": "2025-26",
        "start_date": date(2025, 4, 1),
        "end_date": date(2026, 3, 31),
    },
    {
        "name": "2026-27",
        "start_date": date(2026, 4, 1),
        "end_date": date(2027, 3, 31),
        "is_current": True,
    },
)

SUBJECT_NAMES = ("English", "Hindi", "Mathematics", "Science")
PERIODS = (
    (1, "Period 1", time(8, 0), time(8, 40)),
    (2, "Period 2", time(8, 40), time(9, 20)),
    (3, "Period 3", time(9, 20), time(10, 0)),
    (4, "Period 4", time(10, 20), time(11, 0)),
)
HOUSE_SLOT = (5, "House roll call", time(20, 0), time(20, 30))


def weekdays_ending_on(end_date, count=5):
    days = []
    cursor = end_date
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def ensure_hypothetical_timetable(year):
    period_type, _ = ActivityType.objects.get_or_create(
        name="Period",
        defaults={
            "default_audience_kind": AudienceKind.CLASS,
            "takes_attendance": True,
            "is_active": True,
        },
    )
    house_type, _ = ActivityType.objects.get_or_create(
        name="House roll call",
        defaults={
            "default_audience_kind": AudienceKind.HOUSE,
            "takes_attendance": True,
            "is_active": True,
        },
    )
    subjects = [
        Subject.objects.get_or_create(name=name)[0] for name in SUBJECT_NAMES
    ]
    routine, _ = Routine.objects.get_or_create(
        academic_year=year,
        name="Weekday",
        defaults={"is_active": True},
    )
    slots = []
    for sort_order, name, start, end in PERIODS:
        slot, _ = RoutineSlot.objects.get_or_create(
            routine=routine,
            sort_order=sort_order,
            defaults={
                "activity_type": period_type,
                "name": name,
                "start_time": start,
                "end_time": end,
                "is_active": True,
            },
        )
        slots.append(slot)
    house_slot, _ = RoutineSlot.objects.get_or_create(
        routine=routine,
        sort_order=HOUSE_SLOT[0],
        defaults={
            "activity_type": house_type,
            "name": HOUSE_SLOT[1],
            "start_time": HOUSE_SLOT[2],
            "end_time": HOUSE_SLOT[3],
            "is_active": True,
        },
    )
    teachers = list(
        TeacherProfile.objects.filter(
            user__is_active=True,
            user__category=UserCategory.STAFF,
        ).select_related("user")
    )
    if not teachers:
        raise RuntimeError("No staff teacher profiles to assign to the timetable.")
    classes = list(
        ClassSection.objects.filter(is_active=True).order_by(
            "grade_number",
            "section_name",
        )
    )
    for class_index, section in enumerate(classes):
        for period_index, (slot, subject) in enumerate(zip(slots, subjects)):
            teacher = teachers[(class_index + period_index) % len(teachers)]
            TeachingAssignment.objects.get_or_create(
                teacher=teacher,
                subject=subject,
                class_section=section,
                academic_year=year,
            )
            ClassTimetableEntry.objects.get_or_create(
                academic_year=year,
                class_section=section,
                routine_slot=slot,
                defaults={"subject": subject, "teacher": teacher},
            )
    return routine, house_slot


def ensure_calendar_days(year, routine, dates):
    days = []
    for day in dates:
        calendar_day, _ = SchoolCalendarDay.objects.get_or_create(
            date=day,
            defaults={
                "academic_year": year,
                "routine": routine,
                "note": "Hypothetical weekday",
            },
        )
        days.append(calendar_day)
    return days


def pick_status(rng, student_id):
    roll = rng.random()
    bias = (student_id % 17) / 17
    if roll < 0.04 + 0.05 * bias:
        return AttendanceStatus.ABSENT
    if roll < 0.08 + 0.05 * bias:
        return AttendanceStatus.LATE
    if roll < 0.10 + 0.04 * bias:
        return AttendanceStatus.LEAVE
    return AttendanceStatus.PRESENT


def fill_random_attendance(sessions, *, seed=2026):
    rng = Random(seed)
    created = 0
    skipped = 0
    to_create = []
    for session in sessions:
        if not session.activity_type.takes_attendance:
            continue
        existing = set(
            AttendanceEntry.objects.filter(activity_session=session).values_list(
                "student_id",
                flat=True,
            )
        )
        taken_at = timezone.make_aware(
            datetime.combine(session.date, session.start_time)
        )
        taker = session.responsible_staff
        for student in students_for_session(session):
            if student.pk in existing:
                skipped += 1
                continue
            to_create.append(
                AttendanceEntry(
                    activity_session=session,
                    student=student,
                    status=pick_status(rng, student.pk),
                    taken_by=taker,
                    taken_at=taken_at,
                    notes="Hypothetical seed",
                )
            )
            created += 1
        if len(to_create) >= 500:
            AttendanceEntry.objects.bulk_create(to_create, batch_size=500)
            to_create = []
    if to_create:
        AttendanceEntry.objects.bulk_create(to_create, batch_size=500)
    return created, skipped


def seed_hypothetical_attendance(*, day_count=5, seed=2026, today=None):
    years = ensure_hypothetical_academic_years()
    today = today or timezone.localdate()
    combined = {
        "years": [year.name for year in years],
        "dates": [],
        "generation": [],
        "sessions": 0,
        "created": 0,
        "skipped": 0,
    }
    for index, year in enumerate(years):
        if year.start_date > today:
            continue
        end = today if year.start_date <= today <= year.end_date else year.end_date
        dates = weekdays_ending_on(end, day_count)
        routine, _house_slot = ensure_hypothetical_timetable(year)
        calendar_days = ensure_calendar_days(year, routine, dates)
        generation = generate_sessions_for_calendar_days(calendar_days)
        sessions = list(
            ActivitySession.objects.filter(
                date__in=dates,
                academic_year=year,
                activity_type__takes_attendance=True,
            ).select_related("activity_type", "responsible_staff")
        )
        created, skipped = fill_random_attendance(
            sessions,
            seed=seed + index,
        )
        combined["dates"].extend(dates)
        combined["generation"].extend(generation)
        combined["sessions"] += len(sessions)
        combined["created"] += created
        combined["skipped"] += skipped
    if not combined["dates"]:
        raise RuntimeError("No hypothetical academic years fall on or before today.")
    combined["dates"] = sorted(set(combined["dates"]))
    return combined


def ensure_hypothetical_academic_years():
    created_years = []
    for spec in HYPOTHETICAL_YEAR_SPECS:
        year, _ = AcademicYear.objects.get_or_create(
            name=spec["name"],
            defaults={
                "start_date": spec["start_date"],
                "end_date": spec["end_date"],
                "is_current": False,
            },
        )
        created_years.append(year)
    if not AcademicYear.objects.filter(is_current=True).exists():
        current_spec = next(
            spec for spec in HYPOTHETICAL_YEAR_SPECS if spec.get("is_current")
        )
        current = AcademicYear.objects.get(name=current_spec["name"])
        current.is_current = True
        current.save(update_fields=["is_current"])
    source = AcademicYear.objects.filter(is_current=True).first()
    if source is not None:
        for year in created_years:
            if year.pk != source.pk:
                copy_year_scoped_records(source, year)
    return list(
        AcademicYear.objects.filter(
            name__in=[spec["name"] for spec in HYPOTHETICAL_YEAR_SPECS]
        ).order_by("start_date")
    )


def copy_year_scoped_records(source, dest):
    _copy_class_memberships(source, dest)
    _copy_house_memberships(source, dest)
    _copy_house_staff(source, dest)
    _copy_class_staff(source, dest)
    _copy_class_options(source, dest)
    _copy_teaching_assignments(source, dest)


def _copy_class_memberships(source, dest):
    existing = set(
        StudentClassMembership.objects.filter(academic_year=dest).values_list(
            "student_id",
            flat=True,
        )
    )
    to_create = [
        StudentClassMembership(
            student_id=row.student_id,
            class_section_id=row.class_section_id,
            academic_year=dest,
        )
        for row in StudentClassMembership.objects.filter(academic_year=source)
        if row.student_id not in existing
    ]
    if to_create:
        StudentClassMembership.objects.bulk_create(to_create, batch_size=500)


def _copy_house_memberships(source, dest):
    existing = set(
        StudentHouseMembership.objects.filter(academic_year=dest).values_list(
            "student_id",
            flat=True,
        )
    )
    to_create = [
        StudentHouseMembership(
            student_id=row.student_id,
            house_id=row.house_id,
            academic_year=dest,
        )
        for row in StudentHouseMembership.objects.filter(academic_year=source)
        if row.student_id not in existing
    ]
    if to_create:
        StudentHouseMembership.objects.bulk_create(to_create, batch_size=500)


def _copy_house_staff(source, dest):
    for row in HouseMasterAssignment.objects.filter(academic_year=source):
        HouseMasterAssignment.objects.get_or_create(
            house_id=row.house_id,
            academic_year=dest,
            role=row.role,
            defaults={"staff_id": row.staff_id},
        )


def _copy_class_staff(source, dest):
    for row in ClassGradeStaffAssignment.objects.filter(academic_year=source):
        ClassGradeStaffAssignment.objects.get_or_create(
            class_section_id=row.class_section_id,
            academic_year=dest,
            role=row.role,
            defaults={"teacher_id": row.teacher_id},
        )


def _copy_class_options(source, dest):
    for row in ClassGradeOptions.objects.filter(academic_year=source):
        ClassGradeOptions.objects.get_or_create(
            class_section_id=row.class_section_id,
            academic_year=dest,
            defaults={
                "show_assistant_class_teacher": row.show_assistant_class_teacher,
            },
        )


def _copy_teaching_assignments(source, dest):
    for row in TeachingAssignment.objects.filter(academic_year=source):
        TeachingAssignment.objects.get_or_create(
            teacher_id=row.teacher_id,
            subject_id=row.subject_id,
            class_section_id=row.class_section_id,
            academic_year=dest,
        )
