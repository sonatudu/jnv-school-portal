"""Object-level attendance authorization.

Django model permissions are only a coarse gate. Session access always
goes through can_take_attendance / can_change_attendance_status.
"""

from django.db.models import Count, Q

from accounts.models import UserCategory

from .models import (
    ActivitySession,
    AudienceKind,
    HouseMasterAssignment,
    StaffDutyAssignment,
)


def get_unique_active_mod(date, academic_year):
    """Return the unique usable unique-per-day MOD for date+year, or None."""
    year_id = getattr(academic_year, "pk", academic_year)
    mods = list(
        StaffDutyAssignment.objects.filter(
            date=date,
            academic_year_id=year_id,
            duty_type__unique_per_day=True,
            duty_type__is_active=True,
            staff__category=UserCategory.STAFF,
            staff__is_active=True,
        ).select_related("staff")
    )
    if len(mods) == 1:
        return mods[0].staff
    return None


def _user_blocked(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return True
    if not user.is_active:
        return True
    if getattr(user, "category", None) == UserCategory.PARENT:
        return True
    return False


def _session_takes_attendance(session):
    if session is None or not session.activity_type_id:
        return False
    return bool(session.activity_type.takes_attendance)


def _is_house_master_for_session(user, session):
    if session.audience_kind != AudienceKind.HOUSE or not session.house_id:
        return False
    return HouseMasterAssignment.objects.filter(
        staff=user,
        house_id=session.house_id,
        academic_year_id=session.academic_year_id,
    ).exists()


def _is_unique_mod_for_session(user, session):
    mod = get_unique_active_mod(session.date, session.academic_year_id)
    return mod is not None and mod.pk == user.pk


def _authorized_for_session(user, session):
    """Shared object-level rule for take and status correction."""
    if _user_blocked(user):
        return False
    if not _session_takes_attendance(session):
        return False
    if user.category == UserCategory.ADMINISTRATION:
        return True
    if session.responsible_staff_id == user.pk:
        return True
    if _is_unique_mod_for_session(user, session):
        return True
    if _is_house_master_for_session(user, session):
        return True
    return False


def can_take_attendance(user, session):
    return _authorized_for_session(user, session)


def can_change_attendance_status(user, session):
    return _authorized_for_session(user, session)


def sessions_user_may_mark(user):
    """ActivitySessions the user may take or correct attendance for."""
    if _user_blocked(user):
        return ActivitySession.objects.none()
    base = ActivitySession.objects.filter(activity_type__takes_attendance=True)
    if user.category == UserCategory.ADMINISTRATION:
        return base

    q = Q(responsible_staff=user)

    house_pairs = HouseMasterAssignment.objects.filter(staff=user).values_list(
        "house_id",
        "academic_year_id",
    )
    house_q = Q()
    for house_id, year_id in house_pairs:
        house_q |= Q(
            audience_kind=AudienceKind.HOUSE,
            house_id=house_id,
            academic_year_id=year_id,
        )
    if house_q:
        q |= house_q

    unique_days = (
        StaffDutyAssignment.objects.filter(
            duty_type__unique_per_day=True,
            duty_type__is_active=True,
            staff__category=UserCategory.STAFF,
            staff__is_active=True,
        )
        .values("date", "academic_year_id")
        .annotate(n=Count("id"))
        .filter(n=1)
    )
    user_mod_q = Q()
    for row in unique_days:
        if StaffDutyAssignment.objects.filter(
            date=row["date"],
            academic_year_id=row["academic_year_id"],
            staff=user,
            duty_type__unique_per_day=True,
            duty_type__is_active=True,
        ).exists():
            user_mod_q |= Q(date=row["date"], academic_year_id=row["academic_year_id"])
    if user_mod_q:
        q |= user_mod_q

    return base.filter(q)
