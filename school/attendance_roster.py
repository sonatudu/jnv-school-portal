"""Student roster for session-level attendance marking.

Class, house, and school rosters are live. Group and selected-student
rosters use the ActivitySessionParticipant snapshot only.
"""

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
