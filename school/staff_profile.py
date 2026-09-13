"""Staff/user profile details: biodata, today's tasks, recommended permissions."""

from django.utils import timezone

from accounts.models import UserCategory

from .attendance_auth import get_unique_active_mod, sessions_user_may_mark
from .models import (
    AcademicYear,
    ActivitySession,
    ClassGradeStaffAssignment,
    HouseMasterAssignment,
    StaffDutyAssignment,
    TeacherProfile,
    TeachingAssignment,
)


def build_user_profile(user, today=None):
    today = today or timezone.localdate()
    year = AcademicYear.objects.filter(is_current=True).first()
    try:
        teacher_profile = user.teacher_profile
    except TeacherProfile.DoesNotExist:
        teacher_profile = None

    class_roles = []
    house_roles = []
    teaching = []
    duties_today = []
    if year:
        class_roles = list(
            ClassGradeStaffAssignment.objects.filter(
                teacher=teacher_profile,
                academic_year=year,
            ).select_related("class_section", "teacher__user")
            if teacher_profile
            else []
        )
        house_roles = list(
            HouseMasterAssignment.objects.filter(
                staff=user,
                academic_year=year,
            ).select_related("house")
        )
        teaching = list(
            TeachingAssignment.objects.filter(
                teacher=teacher_profile,
                academic_year=year,
            ).select_related("subject", "class_section")
            if teacher_profile
            else []
        )
        duties_today = list(
            StaffDutyAssignment.objects.filter(
                staff=user,
                date=today,
                academic_year=year,
            ).select_related("duty_type")
        )

    mod = get_unique_active_mod(today, year) if year else None
    is_mod_today = mod is not None and mod.pk == user.pk

    sessions_today = list(
        sessions_user_may_mark(user)
        .filter(date=today)
        .select_related(
            "activity_type",
            "class_section",
            "house",
            "student_group",
            "responsible_staff",
        )
        .order_by("start_time", "name")
    )
    responsible_today = list(
        ActivitySession.objects.filter(date=today, responsible_staff=user)
        .select_related("activity_type", "class_section", "house")
        .order_by("start_time", "name")
    )

    tasks = []
    if not user.is_active:
        tasks.append("Account is inactive — no school tasks.")
    elif user.category == UserCategory.PARENT:
        tasks.append("View linked children's attendance in the parent portal.")
    else:
        if is_mod_today:
            tasks.append("MOD (unique-per-day duty) for today.")
        for duty in duties_today:
            tasks.append(f"Duty today: {duty.duty_type}.")
        for item in class_roles:
            tasks.append(f"{item.get_role_display()} for {item.class_section}.")
        for item in house_roles:
            role = item.get_role_display()
            tasks.append(f"{role} for {item.house}.")
        for item in teaching:
            tasks.append(
                f"Teach {item.subject} to {item.class_section}."
            )
        if user.category == UserCategory.ADMINISTRATION:
            n = len(sessions_today)
            if n:
                tasks.append(
                    f"May mark attendance for any of today's {n} attendance session(s)."
                )
        else:
            if responsible_today:
                for session in responsible_today:
                    tasks.append(
                        f"Responsible staff: {session.start_time}–{session.end_time} "
                        f"{session.name}."
                    )
            if sessions_today:
                for session in sessions_today:
                    tasks.append(
                        f"May mark attendance: {session.start_time}–{session.end_time} "
                        f"{session.name}."
                    )
        if not tasks:
            tasks.append("No assigned tasks for today.")

    permissions = recommended_permissions(
        user,
        year=year,
        is_mod_today=is_mod_today,
        class_roles=class_roles,
        house_roles=house_roles,
        has_teaching=bool(teaching),
    )
    granted = sorted(user.get_all_permissions())
    return {
        "today": today,
        "year": year,
        "teacher_profile": teacher_profile,
        "class_roles": class_roles,
        "house_roles": house_roles,
        "teaching": teaching,
        "duties_today": duties_today,
        "is_mod_today": is_mod_today,
        "sessions_today": sessions_today,
        "tasks": tasks,
        "permissions": permissions,
        "granted_django_permissions": granted,
    }


def recommended_permissions(user, year, is_mod_today, class_roles, house_roles, has_teaching):
    items = []
    if not user.is_active:
        return ["No permissions while the account is inactive."]

    if user.category == UserCategory.ADMINISTRATION:
        items.extend(
            [
                "Log in to Django Admin and the school portal.",
                "View all attendance reports, coverage, and correction audit.",
                "Mark and correct attendance for any attendance-taking session.",
                "Manage users, classes, houses, routines, and students.",
                "Generate daily sessions from the calendar.",
                "Assign class teachers, house teachers, and duties.",
            ]
        )
    elif user.category == UserCategory.STAFF:
        items.extend(
            [
                "Log in to the staff portal.",
                "Mark attendance only for sessions this person is authorized for "
                "(responsible staff, house assignment, or unique-per-day MOD).",
                "Correct attendance only for those same authorized sessions.",
            ]
        )
        if is_mod_today:
            items.append(
                "Today only: mark attendance for attendance sessions as unique-per-day MOD."
            )
        if house_roles:
            houses = ", ".join(str(item.house) for item in house_roles)
            items.append(f"Mark attendance for house sessions of {houses}.")
        if class_roles:
            classes = ", ".join(str(item.class_section) for item in class_roles)
            items.append(
                f"Listed as class staff for {classes} "
                "(this listing does not by itself grant attendance marking)."
            )
        if has_teaching:
            items.append("Teach assigned subjects according to the timetable.")
        items.append("Must not access parent records or other staff's unauthorized sessions.")
    elif user.category == UserCategory.PARENT:
        items.extend(
            [
                "Log in to the parent portal only.",
                "View attendance of linked children (read-only).",
                "Must not mark attendance or use staff Admin tools.",
            ]
        )

    if user.is_superuser:
        items.append("Superuser: all Django model permissions.")
    elif user.is_staff:
        items.append("Django Admin login (staff flag).")

    if user.designation_id and user.designation.group_id:
        items.append(
            f"Django group from designation {user.designation.name}: "
            f"{user.designation.group.name}."
        )
    return items
