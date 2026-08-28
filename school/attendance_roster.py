"""Student roster for session-level attendance marking.

Class, house, and school rosters are live. Group and selected-student
rosters use the ActivitySessionParticipant snapshot only.
"""

from collections import defaultdict

from django.db.models import Q

from .models import (
    ActivitySessionParticipant,
    AttendanceEntry,
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
