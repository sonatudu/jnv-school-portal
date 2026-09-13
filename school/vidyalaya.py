"""JNV vidyalaya identity, daily board, mess, and circulars."""

from datetime import time, timedelta
from random import Random

from django.utils import timezone

from accounts.models import UserCategory

from .models import (
    AcademicYear,
    ActivitySession,
    AttendanceEntry,
    AttendanceStatus,
    AudienceKind,
    Circular,
    CircularAudience,
    MessMenu,
    OutingPass,
    OutingStatus,
    SchoolCalendarDay,
    Student,
    StudentClassMembership,
    StudentHouseMembership,
    VidyalayaProfile,
)

EAST_SINGHBHUM = {
    "name": "Jawahar Navodaya Vidyalaya",
    "district": "East Singhbhum",
    "state": "Jharkhand",
    "nvs_region": "Patna",
    "campus": "Balikudia, Baharagora",
    "established_year": 2001,
    "motto": "Prajñānam Brahma",
    "about": (
        "Jawahar Navodaya Vidyalaya, East Singhbhum is a residential "
        "co-educational school at Balikudia, Baharagora (PIN 832101), "
        "under Navodaya Vidyalaya Samiti, Patna Region. Classes VI–XII, "
        "CBSE, house system, and the Navodaya daily routine of PT, "
        "assembly, studies, games, and night roll call."
    ),
}


def apply_east_singhbhum_identity(profile=None):
    """Keep the singleton profile pointed at this vidyalaya, not a demo region."""
    profile = profile or VidyalayaProfile.load()
    dirty = False
    for field, value in EAST_SINGHBHUM.items():
        if getattr(profile, field) != value:
            setattr(profile, field, value)
            dirty = True
    if dirty:
        profile.save()
    return profile


STATUS_RANK = {
    AttendanceStatus.SICK: 0,
    AttendanceStatus.LEAVE: 1,
    AttendanceStatus.ON_DUTY: 2,
    AttendanceStatus.ABSENT: 3,
    AttendanceStatus.LATE: 4,
    AttendanceStatus.PRESENT: 5,
}

MESS_ROTATION = (
    {
        "breakfast": "Poha, sprouts, banana, milk/tea",
        "lunch": "Roti, dal tadka, seasonal sabzi, rice, salad",
        "evening_snacks": "Namkeen and tea; banana",
        "dinner": "Roti, dal, mixed vegetable, curd",
    },
    {
        "breakfast": "Upma, boiled egg/sprouts, milk",
        "lunch": "Rice, sambar/dal, cabbage sabzi, roti, pickle",
        "evening_snacks": "Biscuit and milk",
        "dinner": "Roti, chole, rice, salad",
    },
    {
        "breakfast": "Idli/sambar or paratha, curd, milk",
        "lunch": "Roti, rajma, rice, seasonal sabzi, salad",
        "evening_snacks": "Roasted chana and tea",
        "dinner": "Roti, dal fry, aloo gobhi, kheer (small)",
    },
    {
        "breakfast": "Bread-butter/jam, boiled gram, milk",
        "lunch": "Roti, dal, bhindi/seasonal, rice, salad",
        "evening_snacks": "Poha leftover or murmura, tea",
        "dinner": "Roti, palak dal, rice, raita",
    },
    {
        "breakfast": "Vegetable daliya, banana, milk",
        "lunch": "Roti, kadhi, rice, aloo sabzi, salad",
        "evening_snacks": "Sprouts chaat, lemon water",
        "dinner": "Roti, dal, seasonal sabzi, rice",
    },
    {
        "breakfast": "Aloo paratha, curd, pickle, tea",
        "lunch": "Roti, dal, baingan/seasonal, rice, salad",
        "evening_snacks": "Boiled sweet corn / fruit",
        "dinner": "Roti, mixed dal, vegetable pulao",
    },
    {
        "breakfast": "Cornflakes/daliya, milk, banana (Sunday)",
        "lunch": "Special: roti, paneer/seasonal, dal, rice, sweet",
        "evening_snacks": "Samosa/poha and tea (Sunday)",
        "dinner": "Roti, dal, sabzi, rice, curd",
    },
)


def circulars_for_user(user, *, limit=8):
    if not user.is_authenticated:
        return Circular.objects.none()
    qs = Circular.objects.filter(is_published=True)
    if user.category == UserCategory.PARENT:
        qs = qs.filter(
            audience__in=(CircularAudience.ALL, CircularAudience.PARENTS)
        )
    elif user.category in (UserCategory.STAFF, UserCategory.ADMINISTRATION):
        qs = qs.filter(
            audience__in=(CircularAudience.ALL, CircularAudience.STAFF)
        )
    else:
        qs = qs.filter(audience=CircularAudience.ALL)
    return qs[:limit]


def current_house(student, year):
    if student is None or year is None:
        return None
    row = (
        StudentHouseMembership.objects.filter(
            student=student,
            academic_year=year,
        )
        .select_related("house")
        .first()
    )
    return row.house if row else None


def _snapshot_sessions(selected_date, year):
    sessions = ActivitySession.objects.filter(
        date=selected_date,
        academic_year=year,
        activity_type__takes_attendance=True,
    )
    house = sessions.filter(audience_kind=AudienceKind.HOUSE)
    if house.exists():
        return house, "House roll call"
    school = sessions.filter(audience_kind=AudienceKind.SCHOOL)
    if school.exists():
        return school, "School assembly"
    return sessions.filter(audience_kind=AudienceKind.CLASS), "Class periods"


def daily_strength_snapshot(selected_date, year):
    strength = 0
    if year is not None:
        strength = StudentClassMembership.objects.filter(
            academic_year=year,
            student__is_active=True,
        ).count()
    counts = {
        "strength": strength,
        "present": 0,
        "absent": 0,
        "on_duty": 0,
        "sick": 0,
        "leave": 0,
        "late": 0,
        "unmarked": 0,
        "marked": 0,
        "source": "",
    }
    if year is None:
        return counts, []
    sessions, source = _snapshot_sessions(selected_date, year)
    counts["source"] = source
    session_ids = list(sessions.values_list("pk", flat=True))
    by_student = {}
    if session_ids:
        for entry in AttendanceEntry.objects.filter(
            activity_session_id__in=session_ids
        ).only("student_id", "status"):
            rank = STATUS_RANK.get(entry.status, 9)
            prev = by_student.get(entry.student_id)
            if prev is None or rank < STATUS_RANK.get(prev, 9):
                by_student[entry.student_id] = entry.status
    for status in by_student.values():
        if status in counts:
            counts[status] += 1
            counts["marked"] += 1
    counts["unmarked"] = max(strength - counts["marked"], 0)
    return counts, list(sessions)


def exception_rows(selected_date, year):
    """Sick, leave, OD, and absent students from the day's snapshot sessions."""
    if year is None:
        return []
    sessions, _source = _snapshot_sessions(selected_date, year)
    session_ids = list(sessions.values_list("pk", flat=True))
    if not session_ids:
        return []
    interesting = {
        AttendanceStatus.SICK,
        AttendanceStatus.LEAVE,
        AttendanceStatus.ON_DUTY,
        AttendanceStatus.ABSENT,
    }
    best = {}
    entries = (
        AttendanceEntry.objects.filter(activity_session_id__in=session_ids)
        .select_related("student", "student__class_section", "activity_session")
        .order_by("student_id")
    )
    for entry in entries:
        if entry.status not in interesting and entry.student_id not in best:
            continue
        current = best.get(entry.student_id)
        if current is None or STATUS_RANK.get(entry.status, 9) < STATUS_RANK.get(
            current.status, 9
        ):
            best[entry.student_id] = entry
    rows = []
    year_houses = {
        row.student_id: row.house
        for row in StudentHouseMembership.objects.filter(
            academic_year=year,
            student_id__in=best.keys(),
        ).select_related("house")
    }
    for entry in best.values():
        if entry.status not in interesting:
            continue
        rows.append(
            {
                "student": entry.student,
                "status": entry.status,
                "status_display": entry.get_status_display(),
                "house": year_houses.get(entry.student_id),
                "session": entry.activity_session,
            }
        )
    rows.sort(
        key=lambda row: (
            STATUS_RANK.get(row["status"], 9),
            str(row["student"].class_section),
            row["student"].roll_number or 0,
        )
    )
    return rows


def seed_vidyalaya_life(*, today=None, seed=2026):
    today = today or timezone.localdate()
    profile = apply_east_singhbhum_identity()
    changed = ["profile"]
    year = AcademicYear.objects.filter(is_current=True).first()
    dates = {today + timedelta(days=offset) for offset in range(-3, 8)}
    if year:
        dates.update(
            SchoolCalendarDay.objects.filter(academic_year=year).values_list(
                "date", flat=True
            )
        )
    dates.update(
        SchoolCalendarDay.objects.filter(
            date__gte=today - timedelta(days=10),
            date__lte=today + timedelta(days=3),
        ).values_list("date", flat=True)
    )
    if not dates:
        dates = {today}
    created_menus = 0
    for day in sorted(dates):
        meal = MESS_ROTATION[day.weekday() % len(MESS_ROTATION)]
        _, was_created = MessMenu.objects.get_or_create(
            date=day,
            defaults={
                **meal,
                "note": "Sunday special" if day.weekday() == 6 else "",
            },
        )
        if was_created:
            created_menus += 1
    if not Circular.objects.exists():
        Circular.objects.create(
            title="Morning PT and assembly — punctuality",
            body=(
                "All houses will fall in for PT at 05:30 hrs. Assembly follows "
                "immediately after. House Teachers will ensure strength, uniform, "
                "and silence in the assembly ground. MOD will report exceptions "
                "to the Principal."
            ),
            audience=CircularAudience.STAFF,
            published_on=today,
            is_published=True,
        )
        Circular.objects.create(
            title="Visiting hours and Sunday meeting",
            body=(
                "Parents may meet their ward on the second Sunday of the month "
                "during notified hours at the visitors’ room. Gate pass and "
                "identity will be checked. Eatables from outside the mess are "
                "not permitted in the hostel."
            ),
            audience=CircularAudience.PARENTS,
            published_on=today,
            is_published=True,
        )
        Circular.objects.create(
            title="Safety, sick bay, and night roll call",
            body=(
                "Any student reporting sick must be sent to the Staff Nurse "
                "before the first period. Night roll call is compulsory in every "
                "house. OD for sports/NVS events will be marked only with a "
                "written note from the teacher in-charge."
            ),
            audience=CircularAudience.ALL,
            published_on=today,
            is_published=True,
        )
        changed.append("circulars")
    rng = Random(seed)
    if year and not OutingPass.objects.filter(date=today).exists():
        sickish = list(
            Student.objects.filter(
                academic_year=year,
                is_active=True,
            ).order_by("pk")[:8]
        )
        from django.contrib.auth import get_user_model

        User = get_user_model()
        issuer = (
            User.objects.filter(
                category=UserCategory.ADMINISTRATION,
                is_active=True,
            ).first()
            or User.objects.filter(category=UserCategory.STAFF, is_active=True).first()
        )
        if issuer and sickish:
            sample = rng.sample(sickish, k=min(3, len(sickish)))
            purposes = (
                ("CHC / hospital", "CHC Baharagora"),
                ("Sports meet", "Jamshedpur stadium"),
                ("Bank / documents", "SBI Baharagora"),
            )
            for student, (purpose, dest) in zip(sample, purposes):
                OutingPass.objects.get_or_create(
                    student=student,
                    date=today,
                    purpose=purpose,
                    defaults={
                        "departure_time": time(9, 0),
                        "expected_return": time(16, 0),
                        "destination": dest,
                        "escort_name": "House Teacher / PET",
                        "status": OutingStatus.APPROVED,
                        "issued_by": issuer,
                    },
                )
            changed.append("outing")
    return {
        "profile": profile.display_name,
        "menus": created_menus,
        "circulars": Circular.objects.count(),
        "notes": changed,
    }