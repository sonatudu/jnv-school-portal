"""Student roster for session-level attendance marking.

Class, house, and school rosters are live. Group and selected-student
rosters use the ActivitySessionParticipant snapshot only.
"""

from collections import defaultdict

from django.db.models import Q

from .models import (
    ActivitySessionParticipant,
    AttendanceEntry,
    AttendanceStatus,
    AudienceKind,
    Student,
    StudentHouseMembership,
)


_STUDENT_ORDER = (
    "class_section",
    "roll_number",
    "last_name",
    "first_name",
    "admission_number",
)


def students_for_session(session):
    """Return the current attendance roster for this session."""
    if session is None:
        return Student.objects.none()

    kind = session.audience_kind
    if kind == AudienceKind.CLASS:
        if not session.class_section_id or not session.academic_year_id:
            return Student.objects.none()
        return Student.objects.filter(
            class_section_id=session.class_section_id,
            academic_year_id=session.academic_year_id,
            is_active=True,
        ).order_by(*_STUDENT_ORDER)

    if kind == AudienceKind.HOUSE:
        if not session.house_id or not session.academic_year_id:
            return Student.objects.none()
        student_ids = StudentHouseMembership.objects.filter(
            house_id=session.house_id,
            academic_year_id=session.academic_year_id,
        ).values_list("student_id", flat=True)
        return Student.objects.filter(
            pk__in=student_ids,
            is_active=True,
        ).order_by(*_STUDENT_ORDER)

    if kind in (AudienceKind.STUDENT_GROUP, AudienceKind.SELECTED_STUDENTS):
        student_ids = ActivitySessionParticipant.objects.filter(
            session=session,
        ).values_list("student_id", flat=True)
        return Student.objects.filter(pk__in=student_ids).order_by(*_STUDENT_ORDER)

    if kind == AudienceKind.SCHOOL:
        if not session.academic_year_id:
            return Student.objects.none()
        return Student.objects.filter(
            academic_year_id=session.academic_year_id,
            is_active=True,
        ).order_by(*_STUDENT_ORDER)

    return Student.objects.none()


def orphan_entries_for_session(session, roster_student_ids):
    """Attendance rows whose students are no longer on the current roster."""
    return (
        AttendanceEntry.objects.filter(activity_session=session)
        .exclude(student_id__in=roster_student_ids)
        .select_related("student", "taken_by", "updated_by")
        .order_by(
            "student__class_section",
            "student__roll_number",
            "student__last_name",
            "student__first_name",
        )
    )


def completion_status(marked_count, roster_count):
    """Return the overview status label for one session."""
    if roster_count == 0:
        return "Empty roster"
    if marked_count == 0:
        return "Not started"
    if marked_count < roster_count:
        return "Partial"
    return "Complete"


def roster_student_ids_by_session(sessions):
    """Map session pk → current roster student ids (same rules as students_for_session)."""
    sessions = list(sessions)
    result = {session.pk: set() for session in sessions}
    if not sessions:
        return result

    class_sessions = []
    house_sessions = []
    snapshot_sessions = []
    school_sessions = []
    for session in sessions:
        kind = session.audience_kind
        if kind == AudienceKind.CLASS:
            class_sessions.append(session)
        elif kind == AudienceKind.HOUSE:
            house_sessions.append(session)
        elif kind in (AudienceKind.STUDENT_GROUP, AudienceKind.SELECTED_STUDENTS):
            snapshot_sessions.append(session)
        elif kind == AudienceKind.SCHOOL:
            school_sessions.append(session)

    if class_sessions:
        class_q = Q()
        for session in class_sessions:
            if session.class_section_id and session.academic_year_id:
                class_q |= Q(
                    class_section_id=session.class_section_id,
                    academic_year_id=session.academic_year_id,
                )
        by_key = defaultdict(set)
        if class_q:
            for student_id, class_id, year_id in Student.objects.filter(
                class_q,
                is_active=True,
            ).values_list("pk", "class_section_id", "academic_year_id"):
                by_key[(class_id, year_id)].add(student_id)
        for session in class_sessions:
            result[session.pk] = set(
                by_key.get((session.class_section_id, session.academic_year_id), ())
            )

    if house_sessions:
        house_q = Q()
        for session in house_sessions:
            if session.house_id and session.academic_year_id:
                house_q |= Q(
                    house_id=session.house_id,
                    academic_year_id=session.academic_year_id,
                )
        by_key = defaultdict(set)
        if house_q:
            memberships = list(
                StudentHouseMembership.objects.filter(house_q).values_list(
                    "student_id",
                    "house_id",
                    "academic_year_id",
                )
            )
            active_ids = set(
                Student.objects.filter(
                    pk__in={row[0] for row in memberships},
                    is_active=True,
                ).values_list("pk", flat=True)
            )
            for student_id, house_id, year_id in memberships:
                if student_id in active_ids:
                    by_key[(house_id, year_id)].add(student_id)
        for session in house_sessions:
            result[session.pk] = set(
                by_key.get((session.house_id, session.academic_year_id), ())
            )

    if snapshot_sessions:
        for session_id, student_id in ActivitySessionParticipant.objects.filter(
            session_id__in=[session.pk for session in snapshot_sessions],
        ).values_list("session_id", "student_id"):
            result[session_id].add(student_id)

    if school_sessions:
        year_ids = {
            session.academic_year_id
            for session in school_sessions
            if session.academic_year_id
        }
        by_year = defaultdict(set)
        if year_ids:
            for student_id, year_id in Student.objects.filter(
                academic_year_id__in=year_ids,
                is_active=True,
            ).values_list("pk", "academic_year_id"):
                by_year[year_id].add(student_id)
        for session in school_sessions:
            result[session.pk] = set(by_year.get(session.academic_year_id, ()))

    return result


def overview_rows_for_sessions(sessions):
    """Build marked/roster/status rows; marked counts ignore orphan entries."""
    sessions = list(sessions)
    roster_ids = roster_student_ids_by_session(sessions)
    marked_counts = {session.pk: 0 for session in sessions}
    if sessions:
        for session_id, student_id in AttendanceEntry.objects.filter(
            activity_session_id__in=roster_ids.keys(),
        ).values_list("activity_session_id", "student_id"):
            if student_id in roster_ids.get(session_id, ()):
                marked_counts[session_id] += 1

    rows = []
    for session in sessions:
        roster_count = len(roster_ids[session.pk])
        marked_count = marked_counts[session.pk]
        rows.append(
            {
                "session": session,
                "roster_count": roster_count,
                "marked_count": marked_count,
                "status": completion_status(marked_count, roster_count),
            }
        )
    return rows


def session_target_label(session):
    """Human-readable class/house/group/school/selected target for a session."""
    if session.audience_kind == AudienceKind.CLASS:
        return str(session.class_section) if session.class_section_id else "Class"
    if session.audience_kind == AudienceKind.HOUSE:
        return str(session.house) if session.house_id else "House"
    if session.audience_kind == AudienceKind.STUDENT_GROUP:
        return str(session.student_group) if session.student_group_id else "Student group"
    if session.audience_kind == AudienceKind.SCHOOL:
        return "Whole school"
    if session.audience_kind == AudienceKind.SELECTED_STUDENTS:
        return "Selected students"
    return session.get_audience_kind_display()


def build_student_attendance_history(student, sessions):
    """Read-only history for one student over the given authorized sessions."""
    sessions = list(sessions)
    roster_ids = roster_student_ids_by_session(sessions)
    session_by_id = {session.pk: session for session in sessions}
    student_id = student.pk
    on_roster_ids = {
        session.pk
        for session in sessions
        if student_id in roster_ids.get(session.pk, ())
    }

    entries = []
    if sessions:
        entries = list(
            AttendanceEntry.objects.filter(
                student=student,
                activity_session_id__in=session_by_id.keys(),
            )
            .select_related(
                "activity_session__activity_type",
                "activity_session__class_section",
                "activity_session__house",
                "activity_session__student_group",
                "activity_session__subject",
                "activity_session__responsible_staff",
                "taken_by",
                "updated_by",
            )
            .prefetch_related("revisions")
            .order_by(
                "-activity_session__date",
                "-activity_session__start_time",
                "activity_session__name",
            )
        )

    marked_rows = []
    marked_session_ids = set()
    status_counts = {value: 0 for value, _label in AttendanceStatus.choices}
    for entry in entries:
        session = entry.activity_session
        marked_session_ids.add(session.pk)
        status_counts[entry.status] = status_counts.get(entry.status, 0) + 1
        revisions = list(entry.revisions.all())
        marked_rows.append(
            {
                "entry": entry,
                "session": session,
                "is_orphan": session.pk not in on_roster_ids,
                "latest_revision": revisions[0] if revisions else None,
                "target": session_target_label(session),
            }
        )

    unmarked_sessions = [
        session
        for session in sorted(
            sessions,
            key=lambda item: (item.date, item.start_time, item.name),
            reverse=True,
        )
        if session.pk in on_roster_ids and session.pk not in marked_session_ids
    ]
    unmarked_rows = [
        {
            "session": session,
            "target": session_target_label(session),
        }
        for session in unmarked_sessions
    ]

    total_marked = len(marked_rows)
    total_eligible = len(on_roster_ids | marked_session_ids)
    if total_eligible:
        percentage = (100.0 * total_marked) / total_eligible
    else:
        percentage = None

    return {
        "marked_rows": marked_rows,
        "unmarked_rows": unmarked_rows,
        "status_counts": status_counts,
        "total_marked": total_marked,
        "total_eligible": total_eligible,
        "percentage": percentage,
    }


def _empty_status_counts():
    return {value: 0 for value, _label in AttendanceStatus.choices}


def present_rate(present_count, marked_count):
    """Present / Marked × 100, or None when there are no marks."""
    if not marked_count:
        return None
    return (100.0 * present_count) / marked_count


def build_class_attendance_report(class_section, academic_year, sessions):
    """Read-only class report over authorized class sessions."""
    empty = {
        "roster_size": 0,
        "students_with_marks": 0,
        "students_with_no_marks": 0,
        "status_counts": _empty_status_counts(),
        "total_marked": 0,
        "percentage": None,
        "student_rows": [],
    }
    if class_section is None or academic_year is None:
        return empty

    sessions = list(sessions)
    roster_students = list(
        Student.objects.filter(
            class_section=class_section,
            academic_year=academic_year,
            is_active=True,
        ).order_by("roll_number", "last_name", "first_name", "admission_number")
    )
    roster_id_set = {student.pk for student in roster_students}
    roster_by_session = roster_student_ids_by_session(sessions)

    entries = []
    if sessions:
        entries = list(
            AttendanceEntry.objects.filter(
                activity_session_id__in=[session.pk for session in sessions],
            ).select_related("student")
        )

    entries_by_student = defaultdict(list)
    extra_ids = set()
    for entry in entries:
        entries_by_student[entry.student_id].append(entry)
        if entry.student_id not in roster_id_set:
            extra_ids.add(entry.student_id)

    extra_students = []
    if extra_ids:
        extra_students = list(
            Student.objects.filter(pk__in=extra_ids).order_by(
                "last_name",
                "first_name",
                "admission_number",
            )
        )

    student_rows = []
    for student in roster_students + extra_students:
        on_roster_sessions = {
            session_id
            for session_id, student_ids in roster_by_session.items()
            if student.pk in student_ids
        }
        student_entries = entries_by_student.get(student.pk, [])
        marked_session_ids = {entry.activity_session_id for entry in student_entries}
        eligible_ids = on_roster_sessions | marked_session_ids
        counts = _empty_status_counts()
        for entry in student_entries:
            counts[entry.status] = counts.get(entry.status, 0) + 1
        marked = len(student_entries)
        present = counts.get(AttendanceStatus.PRESENT, 0)
        student_rows.append(
            {
                "student": student,
                "on_current_roster": student.pk in roster_id_set,
                "eligible": len(eligible_ids),
                "marked": marked,
                "unmarked": len(eligible_ids) - marked,
                "present": present,
                "absent": counts.get(AttendanceStatus.ABSENT, 0),
                "late": counts.get(AttendanceStatus.LATE, 0),
                "leave": counts.get(AttendanceStatus.LEAVE, 0),
                "percentage": present_rate(present, marked),
            }
        )

    totals = _empty_status_counts()
    for entry in entries:
        totals[entry.status] = totals.get(entry.status, 0) + 1
    total_marked = len(entries)
    total_present = totals.get(AttendanceStatus.PRESENT, 0)

    return {
        "roster_size": len(roster_students),
        "students_with_marks": sum(1 for row in student_rows if row["marked"]),
        "students_with_no_marks": sum(
            1
            for row in student_rows
            if row["on_current_roster"] and row["marked"] == 0
        ),
        "status_counts": totals,
        "total_marked": total_marked,
        "percentage": present_rate(total_present, total_marked),
        "student_rows": student_rows,
    }
