"""Portal pages for JNV East Singhbhum operations beyond attendance."""

from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone

from accounts.models import UserCategory

from .models import (
    AcademicYear,
    AssessmentMark,
    CommitteeSeat,
    ExamTerm,
    HouseCompetitionResult,
    LibraryIssue,
    MigrationRecord,
    SickBayVisit,
    StudentOffice,
    VidyalayaEvent,
    VisitorPass,
    VvnEntry,
)
from .portal import _staff_categories, students_visible_to_parent


def _current_year():
    return AcademicYear.objects.filter(is_current=True).first()


def _require_staff(request):
    if request.user.category not in _staff_categories():
        return HttpResponseForbidden("Staff or Administration access is required.")
    return None


@login_required
def life_hub(request):
    year = _current_year()
    house_totals = []
    if year:
        house_totals = (
            HouseCompetitionResult.objects.filter(competition__academic_year=year)
            .values("house__name")
            .annotate(total=Sum("points"))
            .order_by("-total", "house__name")
        )
    events = VidyalayaEvent.objects.order_by("-held_on")[:6]
    return render(
        request,
        "portal/life.html",
        {
            "year": year,
            "house_totals": house_totals,
            "events": events,
            "is_staff": request.user.category in _staff_categories(),
        },
    )


@login_required
def events_board(request):
    year = _current_year()
    events = VidyalayaEvent.objects.all()
    if year:
        events = events.filter(academic_year=year)
    return render(
        request,
        "portal/events.html",
        {"year": year, "events": events.order_by("-held_on")},
    )


@login_required
def house_points(request):
    year = _current_year()
    totals = []
    competitions = []
    if year:
        totals = (
            HouseCompetitionResult.objects.filter(competition__academic_year=year)
            .values("house__name")
            .annotate(total=Sum("points"))
            .order_by("-total", "house__name")
        )
        competitions = (
            HouseCompetitionResult.objects.filter(competition__academic_year=year)
            .select_related("competition", "house")
            .order_by("-competition__held_on", "position")
        )
    offices = StudentOffice.objects.none()
    if year:
        offices = (
            StudentOffice.objects.filter(academic_year=year)
            .select_related("student", "house")
            .order_by("role", "house__name")
        )
    return render(
        request,
        "portal/house_life.html",
        {
            "year": year,
            "totals": totals,
            "competitions": competitions,
            "offices": offices,
        },
    )


@login_required
def library_board(request):
    issues = LibraryIssue.objects.select_related("book", "student", "student__class_section")
    if request.user.category == UserCategory.PARENT:
        wards = students_visible_to_parent(request.user)
        issues = issues.filter(student__in=wards)
    issues = issues.order_by("-issued_on")[:80]
    return render(request, "portal/library.html", {"issues": issues})


@login_required
def exams_board(request):
    year = _current_year()
    term = ExamTerm.objects.filter(academic_year=year).order_by("-starts_on").first() if year else None
    marks = AssessmentMark.objects.none()
    grouped = []
    if term:
        marks = AssessmentMark.objects.filter(term=term).select_related(
            "student", "student__class_section", "subject"
        )
        if request.user.category == UserCategory.PARENT:
            marks = marks.filter(student__in=students_visible_to_parent(request.user))
        marks = marks.order_by("student__class_section", "student__roll_number", "subject__name")[:200]
        by_student = defaultdict(list)
        for row in marks:
            by_student[row.student].append(row)
        grouped = list(by_student.items())[:40]
    return render(
        request,
        "portal/exams.html",
        {"year": year, "term": term, "grouped": grouped},
    )


@login_required
def vmc_board(request):
    year = _current_year()
    seats = CommitteeSeat.objects.all()
    if year:
        seats = seats.filter(academic_year=year)
    return render(
        request,
        "portal/vmc.html",
        {"year": year, "seats": seats.order_by("kind", "pk")},
    )


@login_required
def sick_bay_board(request):
    blocked = _require_staff(request)
    if blocked:
        return blocked
    visits = (
        SickBayVisit.objects.select_related("student", "student__class_section", "recorded_by")
        .order_by("-visited_on", "-visited_at")[:80]
    )
    return render(request, "portal/sick_bay.html", {"visits": visits, "today": timezone.localdate()})


@login_required
def visitors_board(request):
    blocked = _require_staff(request)
    if blocked:
        return blocked
    rows = (
        VisitorPass.objects.select_related("student", "student__class_section")
        .order_by("-visited_on")[:80]
    )
    return render(request, "portal/visitors.html", {"rows": rows})


@login_required
def migration_board(request):
    blocked = _require_staff(request)
    if blocked:
        return blocked
    year = _current_year()
    rows = MigrationRecord.objects.select_related("student", "student__class_section")
    if year:
        rows = rows.filter(academic_year=year)
    return render(
        request,
        "portal/migration.html",
        {"year": year, "rows": rows.order_by("direction", "student__last_name")},
    )


@login_required
def vvn_board(request):
    blocked = _require_staff(request)
    if blocked:
        return blocked
    year = _current_year()
    rows = VvnEntry.objects.select_related("student", "student__class_section")
    if year:
        rows = rows.filter(academic_year=year)
    paid = rows.filter(is_paid=True).count()
    due = rows.filter(is_paid=False).count()
    return render(
        request,
        "portal/vvn.html",
        {
            "year": year,
            "rows": rows.order_by("student__class_section", "student__roll_number")[:120],
            "paid": paid,
            "due": due,
        },
    )
