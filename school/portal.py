"""User-facing school portal views. Attendance rules stay in attendance_auth."""

from datetime import date

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import UserCategory

from .attendance_auth import can_take_attendance, get_unique_active_mod, sessions_user_may_mark
from .attendance_roster import (
    build_student_attendance_history,
    overview_rows_for_sessions,
    session_target_label,
)
from .models import (
    AcademicYear,
    ActivitySession,
    MessMenu,
    OutingPass,
    OutingStatus,
    ParentProfile,
    SchoolCalendarDay,
    StaffDutyAssignment,
    Student,
)
from .vidyalaya import (
    circulars_for_user,
    current_house,
    daily_strength_snapshot,
    exception_rows,
)


def _staff_categories():
    return (UserCategory.ADMINISTRATION, UserCategory.STAFF)


def students_visible_to_parent(user):
    if not user.is_authenticated or user.category != UserCategory.PARENT:
        return Student.objects.none()
    profile = ParentProfile.objects.filter(user=user).first()
    if profile is None:
        return Student.objects.none()
    return (
        Student.objects.filter(guardian_links__parent_profile=profile)
        .select_related("class_section", "academic_year")
        .distinct()
        .order_by("last_name", "first_name", "admission_number")
    )


def _selected_date(request):
    raw_date = request.GET.get("date")
    if raw_date:
        try:
            return date.fromisoformat(raw_date)
        except ValueError:
            return timezone.localdate()
    return timezone.localdate()


def _year_for_date(selected_date):
    calendar_day = (
        SchoolCalendarDay.objects.select_related("routine", "academic_year")
        .filter(date=selected_date)
        .first()
    )
    year = calendar_day.academic_year if calendar_day else AcademicYear.objects.filter(is_current=True).first()
    return calendar_day, year


def _grouped_mark_rows(rows):
    from collections import OrderedDict

    groups = OrderedDict()
    for item in rows:
        session = item["session"]
        key = (
            session.start_time,
            session.end_time,
            session.audience_kind,
            session.activity_type_id,
        )
        bucket = groups.get(key)
        if bucket is None:
            names = set()
            bucket = {
                "start": session.start_time,
                "end": session.end_time,
                "kind": session.get_audience_kind_display(),
                "activity": session.activity_type,
                "names": names,
                "items": [],
            }
            groups[key] = bucket
        item = {**item, "target": session_target_label(session)}
        bucket["items"].append(item)
        bucket["names"].add(session.name)
    result = []
    for bucket in groups.values():
        names = bucket["names"]
        bucket["title"] = next(iter(names)) if len(names) == 1 else str(bucket["activity"] or "Session")
        del bucket["names"]
        result.append(bucket)
    return result


def home(request):
    if request.user.is_authenticated:
        return redirect("portal-dashboard")
    return render(request, "portal/home.html")


@login_required
def dashboard(request):
    user = request.user
    if user.category == UserCategory.PARENT:
        return redirect("portal-parent")
    if user.category in _staff_categories():
        return redirect("portal-staff")
    return HttpResponseForbidden("This account cannot use the school portal.")


@login_required
def staff_dashboard(request):
    user = request.user
    if user.category not in _staff_categories():
        return HttpResponseForbidden("Staff or Administration access is required.")

    selected_date = _selected_date(request)
    calendar_day, year = _year_for_date(selected_date)
    mod = get_unique_active_mod(selected_date, year) if year else None
    sessions = (
        sessions_user_may_mark(user)
        .filter(date=selected_date)
        .select_related(
            "activity_type",
            "class_section",
            "house",
            "student_group",
            "responsible_staff",
            "routine_slot",
        )
        .order_by("start_time", "name")
    )
    rows = overview_rows_for_sessions(sessions)
    for item in rows:
        item["mark_url"] = reverse("portal-staff-mark", args=[item["session"].pk])
    session_groups = _grouped_mark_rows(rows)
    snapshot, _sessions = daily_strength_snapshot(selected_date, year)
    duties = []
    if year:
        duties = list(
            StaffDutyAssignment.objects.filter(
                date=selected_date,
                academic_year=year,
            )
            .select_related("duty_type", "staff")
            .order_by("duty_type__name", "staff__last_name")
        )
    return render(
        request,
        "portal/staff_dashboard.html",
        {
            "selected_date": selected_date,
            "calendar_day": calendar_day,
            "rows": rows,
            "session_groups": session_groups,
            "is_mod_today": mod is not None and mod.pk == user.pk,
            "mod": mod,
            "snapshot": snapshot,
            "mess": MessMenu.objects.filter(date=selected_date).first(),
            "circulars": circulars_for_user(user, limit=5),
            "duties": duties,
            "outing_open": OutingPass.objects.filter(
                date=selected_date,
            ).exclude(status__in=(OutingStatus.RETURNED, OutingStatus.CANCELLED)).count(),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def staff_mark_attendance(request, session_id):
    from django.contrib.admin.sites import site

    user = request.user
    if user.category not in _staff_categories():
        return HttpResponseForbidden("Staff or Administration access is required.")

    session = (
        ActivitySession.objects.select_related(
            "activity_type",
            "class_section",
            "house",
            "student_group",
            "responsible_staff",
            "routine_slot",
            "academic_year",
        )
        .filter(pk=session_id)
        .first()
    )
    if session is None:
        raise Http404("Activity session not found.")
    if not session.activity_type_id or not session.activity_type.takes_attendance:
        return HttpResponseForbidden("This activity does not take attendance.")
    if not can_take_attendance(user, session):
        return HttpResponseForbidden("You are not authorized to mark attendance for this session.")

    admin = site._registry[ActivitySession]
    errors = []
    posted = None
    if request.method == "POST":
        try:
            errors = admin._apply_mark_attendance(request, session)
        except PermissionDenied:
            return HttpResponseForbidden(
                "You are not authorized to mark attendance for this session."
            )
        if not errors:
            return redirect("portal-staff-mark", session_id=session.pk)
        posted = request.POST
    context = admin._mark_attendance_context(request, session, errors=errors, posted=posted)
    context["back_url"] = reverse("portal-staff") + f"?date={session.date.isoformat()}"
    return render(request, "portal/mark_attendance.html", context)


@login_required
def parent_dashboard(request):
    if request.user.category != UserCategory.PARENT:
        return HttpResponseForbidden("Parent access is required.")
    students = list(students_visible_to_parent(request.user))
    year = AcademicYear.objects.filter(is_current=True).first()
    today = timezone.localdate()
    child_rows = []
    for student in students:
        house = current_house(student, year or student.academic_year)
        child_rows.append({"student": student, "house": house})
    return render(
        request,
        "portal/parent_dashboard.html",
        {
            "students": students,
            "child_rows": child_rows,
            "mess": MessMenu.objects.filter(date=today).first(),
            "circulars": circulars_for_user(request.user, limit=5),
            "today": today,
        },
    )


@login_required
def parent_student_attendance(request, student_id):
    if request.user.category != UserCategory.PARENT:
        return HttpResponseForbidden("Parent access is required.")
    student = get_object_or_404(
        students_visible_to_parent(request.user),
        pk=student_id,
    )
    years = list(AcademicYear.objects.order_by("-start_date"))
    selected_year = student.academic_year
    raw_year = request.GET.get("academic_year")
    if raw_year:
        try:
            selected_year = AcademicYear.objects.get(pk=int(raw_year))
        except (AcademicYear.DoesNotExist, TypeError, ValueError):
            selected_year = None
    date_from = selected_year.start_date if selected_year else None
    date_to = selected_year.end_date if selected_year else None
    history = build_student_attendance_history(student, [])
    if selected_year and date_from and date_to:
        sessions = (
            ActivitySession.objects.filter(
                academic_year=selected_year,
                date__gte=date_from,
                date__lte=date_to,
                activity_type__takes_attendance=True,
            )
            .select_related(
                "activity_type",
                "class_section",
                "house",
                "student_group",
                "subject",
            )
            .order_by("-date", "-start_time", "name")
        )
        history = build_student_attendance_history(student, sessions)
    percentage_display = "N/A"
    if history["percentage"] is not None:
        percentage_display = f"{history['percentage']:.1f}".rstrip("0").rstrip(".") + "%"
    return render(
        request,
        "portal/parent_history.html",
        {
            "student": student,
            "years": years,
            "selected_year": selected_year,
            "history": history,
            "percentage_display": percentage_display,
        },
    )


@login_required
def mess_menu(request):
    selected_date = _selected_date(request)
    menu = MessMenu.objects.filter(date=selected_date).first()
    upcoming = MessMenu.objects.filter(date__gte=selected_date).order_by("date")[:7]
    return render(
        request,
        "portal/mess.html",
        {
            "selected_date": selected_date,
            "menu": menu,
            "upcoming": upcoming,
        },
    )


@login_required
def circular_list(request):
    return render(
        request,
        "portal/circulars.html",
        {"circulars": circulars_for_user(request.user, limit=30)},
    )


@login_required
def exception_register(request):
    user = request.user
    if user.category not in _staff_categories():
        return HttpResponseForbidden("Staff or Administration access is required.")
    selected_date = _selected_date(request)
    _calendar_day, year = _year_for_date(selected_date)
    rows = exception_rows(selected_date, year)
    return render(
        request,
        "portal/exceptions.html",
        {
            "selected_date": selected_date,
            "rows": rows,
            "snapshot": daily_strength_snapshot(selected_date, year)[0],
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def outing_board(request):
    user = request.user
    if user.category not in _staff_categories():
        return HttpResponseForbidden("Staff or Administration access is required.")
    selected_date = _selected_date(request)
    if request.method == "POST":
        pass_id = request.POST.get("pass_id")
        action = request.POST.get("action")
        outing = OutingPass.objects.filter(pk=pass_id, date=selected_date).first()
        if outing is not None:
            if action == "out":
                outing.status = OutingStatus.OUT
                outing.save(update_fields=["status"])
            elif action == "returned":
                outing.status = OutingStatus.RETURNED
                outing.save(update_fields=["status"])
        return redirect(reverse("portal-outings") + f"?date={selected_date.isoformat()}")
    passes = (
        OutingPass.objects.filter(date=selected_date)
        .select_related("student", "student__class_section", "issued_by")
        .order_by("status", "departure_time")
    )
    return render(
        request,
        "portal/outings.html",
        {"selected_date": selected_date, "passes": passes},
    )
