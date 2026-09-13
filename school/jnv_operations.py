"""Hypothetical JNV East Singhbhum operational records (CCA, VMC, library, etc.)."""

from datetime import time, timedelta
from random import Random

from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import UserCategory

from .models import (
    AcademicYear,
    AssessmentMark,
    CommitteeKind,
    CommitteeSeat,
    CompetitionKind,
    EventKind,
    ExamTerm,
    House,
    HouseCompetition,
    HouseCompetitionResult,
    LibraryBook,
    LibraryIssue,
    MigrationRecord,
    SickBayVisit,
    Student,
    StudentClassMembership,
    StudentHouseMembership,
    StudentOffice,
    StudentOfficeRole,
    Subject,
    VidyalayaEvent,
    VisitorPass,
    VvnEntry,
)

LIBRARY_TITLES = (
    ("Wings of Fire", "A.P.J. Abdul Kalam", "Biography"),
    ("Gitanjali", "Rabindranath Tagore", "Literature"),
    ("Godan", "Premchand", "Hindi"),
    ("NCERT Science IX", "NCERT", "Science"),
    ("NCERT Maths X", "NCERT", "Mathematics"),
    ("Brief History of Time", "Stephen Hawking", "Science"),
    ("Discovery of India", "Jawaharlal Nehru", "History"),
    ("Panchatantra", "Vishnu Sharma", "Sanskrit"),
    ("Train to Pakistan", "Khushwant Singh", "Literature"),
    ("Jharkhand: Land and People", "District Gazetteer", "Social science"),
    ("Hockey Skills", "SAI", "Physical education"),
    ("Computer Masti", "IIT Bombay", "ICT"),
    ("Rashmirathi", "Ramdhari Singh Dinkar", "Hindi"),
    ("The Guide", "R.K. Narayan", "English"),
    ("Environmental Studies", "NCERT", "Science"),
    ("Atlas of India", "Survey of India", "Geography"),
    ("Navodaya Diary", "NVS", "School"),
    ("Folk Tales of Jharkhand", "Compiled", "Literature"),
    ("Yoga for School", "Ministry of AYUSH", "Physical education"),
    ("Constitution of India (abridged)", "GOI", "Social science"),
)

OTHER_JNVS = (
    "JNV West Singhbhum",
    "JNV Ranchi",
    "JNV Dhanbad",
    "JNV Saraikela-Kharsawan",
    "JNV Hazaribagh",
)


def seed_jnv_operations(*, today=None, seed=2026):
    today = today or timezone.localdate()
    year = AcademicYear.objects.filter(is_current=True).first()
    if year is None:
        return {"skipped": "no current academic year"}
    rng = Random(seed)
    houses = list(House.objects.order_by("name"))
    students = list(
        Student.objects.filter(is_active=True, academic_year=year).order_by("pk")[:80]
    )
    if not students:
        students = list(Student.objects.filter(is_active=True).order_by("pk")[:80])
    staff = (
        get_user_model()
        .objects.filter(
            category__in=(UserCategory.STAFF, UserCategory.ADMINISTRATION),
            is_active=True,
        )
        .first()
    )
    return {
        "offices": _seed_offices(year, houses),
        "competitions": _seed_competitions(year, houses, today, rng),
        "sick_bay": _seed_sick_bay(students, today, staff, rng),
        "visitors": _seed_visitors(students, today, rng),
        "library": _seed_library(students, today, rng),
        "exams": _seed_exams(year, students, today, rng),
        "vmc": _seed_committees(year),
        "events": _seed_events(year, today),
        "vvn": _seed_vvn(year, students, rng),
        "migration": _seed_migration(year),
    }


def _house_for(student, year):
    link = (
        StudentHouseMembership.objects.filter(student=student, academic_year=year)
        .select_related("house")
        .first()
    )
    return link.house if link else None


def _seed_offices(year, houses):
    if StudentOffice.objects.filter(academic_year=year).exists():
        return StudentOffice.objects.filter(academic_year=year).count()
    count = 0
    seniors = list(
        StudentClassMembership.objects.filter(
            academic_year=year,
            class_section__grade_name__in=("XI", "XII"),
        )
        .select_related("student")
        .order_by("pk")
    )
    pool = [m.student for m in seniors] or list(
        Student.objects.filter(academic_year=year, is_active=True).order_by("pk")[:40]
    )
    if len(pool) < 2:
        return 0
    school_roles = (
        StudentOfficeRole.SCHOOL_CAPTAIN,
        StudentOfficeRole.SCHOOL_VICE_CAPTAIN,
        StudentOfficeRole.GAMES_CAPTAIN,
        StudentOfficeRole.CCA_CAPTAIN,
        StudentOfficeRole.MESS_PREFECT,
    )
    used = set()
    for role in school_roles:
        student = next((s for s in pool if s.pk not in used), None)
        if student is None:
            break
        used.add(student.pk)
        StudentOffice.objects.create(
            student=student,
            academic_year=year,
            house=_house_for(student, year),
            role=role,
        )
        count += 1
    for house in houses:
        members = list(
            StudentHouseMembership.objects.filter(academic_year=year, house=house)
            .select_related("student")
            .order_by("pk")[:12]
        )
        if not members:
            continue
        for i, role in enumerate(
            (
                StudentOfficeRole.HOUSE_CAPTAIN,
                StudentOfficeRole.HOUSE_VICE_CAPTAIN,
                StudentOfficeRole.PREFECT,
                StudentOfficeRole.PREFECT,
            )
        ):
            if i >= len(members):
                break
            StudentOffice.objects.get_or_create(
                student=members[i].student,
                academic_year=year,
                house=house,
                role=role,
            )
            count += 1
    return count


def _seed_competitions(year, houses, today, rng):
    if not houses:
        return 0
    specs = (
        ("Inter-house athletics", CompetitionKind.SPORTS, today - timedelta(days=18), "Playground"),
        ("Hindi debate & recitation", CompetitionKind.LITERARY, today - timedelta(days=11), "MP Hall"),
        ("Folk dance (Jharkhand)", CompetitionKind.CULTURAL, today - timedelta(days=6), "MP Hall"),
        ("Science quiz", CompetitionKind.QUIZ, today - timedelta(days=3), "Physics lab"),
        ("Kho-kho & kabaddi", CompetitionKind.SPORTS, today + timedelta(days=4), "Playground"),
    )
    made = 0
    points_sets = ((10, 7, 5, 3), (10, 6, 4, 2), (8, 6, 4, 2))
    for i, (name, kind, held, venue) in enumerate(specs):
        comp, created = HouseCompetition.objects.get_or_create(
            academic_year=year,
            name=name,
            defaults={"kind": kind, "held_on": held, "venue": venue},
        )
        if not created and comp.results.exists():
            continue
        pts = points_sets[i % len(points_sets)]
        ordered = list(houses)
        rng.shuffle(ordered)
        for position, house in enumerate(ordered, start=1):
            HouseCompetitionResult.objects.get_or_create(
                competition=comp,
                house=house,
                defaults={
                    "points": pts[position - 1] if position <= len(pts) else 1,
                    "position": position,
                },
            )
        made += 1
    return made


def _seed_sick_bay(students, today, staff, rng):
    if SickBayVisit.objects.exists() or not students:
        return SickBayVisit.objects.count()
    complaints = (
        ("Fever", "PCM, rest in sick bay"),
        ("Stomach ache", "ORS, light diet"),
        ("Sprain (PT)", "Ice pack, referred PET"),
        ("Headache", "Rest, hydration"),
        ("Allergy", "Cetirizine as advised"),
        ("Cold / cough", "Steam, observation"),
    )
    n = min(10, len(students))
    sample = rng.sample(students, n)
    for i, student in enumerate(sample):
        complaint, treatment = complaints[i % len(complaints)]
        SickBayVisit.objects.create(
            student=student,
            visited_on=today - timedelta(days=i % 6),
            visited_at=time(8 + (i % 6), 15),
            complaint=complaint,
            treatment=treatment,
            referred_out=i % 7 == 0,
            recorded_by=staff,
        )
    return n


def _seed_visitors(students, today, rng):
    if VisitorPass.objects.exists() or not students:
        return VisitorPass.objects.count()
    n = min(6, len(students))
    for i, student in enumerate(rng.sample(students, n)):
        VisitorPass.objects.create(
            student=student,
            visited_on=today - timedelta(days=(i * 7) % 21),
            visitor_name=f"Guardian of {student.last_name}",
            relation="Parent",
            purpose="Sunday meeting",
            in_time=time(10, 0),
            out_time=time(12, 30),
        )
    return n


def _seed_library(students, today, rng):
    books = []
    for i, (title, author, subject) in enumerate(LIBRARY_TITLES, start=1):
        book, _ = LibraryBook.objects.get_or_create(
            accession_no=f"ES-{i:04d}",
            defaults={"title": title, "author": author, "subject": subject},
        )
        books.append(book)
    if LibraryIssue.objects.exists() or not students or not books:
        return LibraryIssue.objects.count()
    n = min(16, len(students), len(books))
    for i in range(n):
        issued = today - timedelta(days=3 + i)
        LibraryIssue.objects.create(
            book=books[i],
            student=students[i],
            issued_on=issued,
            due_on=issued + timedelta(days=14),
            returned_on=issued + timedelta(days=10) if i % 3 == 0 else None,
        )
    return n


def _seed_exams(year, students, today, rng):
    term, _ = ExamTerm.objects.get_or_create(
        academic_year=year,
        name="Periodic Test-1",
        defaults={
            "starts_on": today - timedelta(days=40),
            "ends_on": today - timedelta(days=33),
        },
    )
    subjects = list(Subject.objects.order_by("name")[:6])
    if not subjects or not students:
        return 0
    if AssessmentMark.objects.filter(term=term).exists():
        return AssessmentMark.objects.filter(term=term).count()
    sample = students[:36]
    count = 0
    for student in sample:
        for subject in subjects[:4]:
            AssessmentMark.objects.create(
                term=term,
                student=student,
                subject=subject,
                marks_obtained=rng.randint(18, 40),
                max_marks=40,
            )
            count += 1
    return count


def _seed_committees(year):
    if CommitteeSeat.objects.filter(academic_year=year).exists():
        return CommitteeSeat.objects.filter(academic_year=year).count()
    rows = (
        (CommitteeKind.VMC, "Chairman", "Deputy Commissioner, East Singhbhum", "District Administration"),
        (CommitteeKind.VMC, "Member Secretary", "Principal, JNV East Singhbhum", "NVS"),
        (CommitteeKind.VMC, "Member", "Vice-Principal, JNV East Singhbhum", "NVS"),
        (CommitteeKind.VMC, "Member (Education)", "District Education Officer", "Jharkhand Education"),
        (CommitteeKind.VMC, "Parent member", "Shri R. Mahato", "Parent"),
        (CommitteeKind.VMC, "Parent member", "Smt. S. Soren", "Parent"),
        (CommitteeKind.VMC, "Teacher member", "PGT English", "Faculty"),
        (CommitteeKind.PAC, "Convener", "Vice-Principal", "NVS"),
        (CommitteeKind.PAC, "Parent representative", "Sunday meeting committee", "Parents"),
    )
    for kind, role, name, org in rows:
        CommitteeSeat.objects.create(
            kind=kind,
            academic_year=year,
            role=role,
            member_name=name,
            organisation=org,
        )
    return len(rows)


def _seed_events(year, today):
    specs = (
        ("Morning assembly — Constitution Day briefing", EventKind.ASSEMBLY, today - timedelta(days=2), "Assembly ground"),
        ("Pace-setting: science demo at nearby UMS Balikudia", EventKind.PACE, today - timedelta(days=9), "UMS Balikudia"),
        ("Inter-house folk dance", EventKind.CCA, today - timedelta(days=6), "MP Hall"),
        ("Cluster sports trial, Jamshedpur", EventKind.GAMES, today + timedelta(days=8), "Jamshedpur stadium"),
        ("Class XI migration counselling", EventKind.MIGRATION, today + timedelta(days=21), "Conference room"),
        ("Hindi Pakhwada closing", EventKind.CCA, today - timedelta(days=14), "MP Hall"),
        ("Yoga and PT mass display", EventKind.GAMES, today + timedelta(days=2), "Playground"),
    )
    made = 0
    for title, kind, held, venue in specs:
        _, created = VidyalayaEvent.objects.get_or_create(
            academic_year=year,
            title=title,
            defaults={"kind": kind, "held_on": held, "venue": venue},
        )
        if created:
            made += 1
    return made


def _seed_vvn(year, students, rng):
    if VvnEntry.objects.filter(academic_year=year).exists() or not students:
        return VvnEntry.objects.filter(academic_year=year).count()
    count = 0
    for student in students[:40]:
        VvnEntry.objects.create(
            student=student,
            academic_year=year,
            amount=1500,
            is_paid=rng.random() < 0.65,
            remark="Boys IX–XII (non-exempt) hypothetical VVN",
        )
        count += 1
    return count


def _seed_migration(year):
    if MigrationRecord.objects.filter(academic_year=year).exists():
        return MigrationRecord.objects.filter(academic_year=year).count()
    xi = list(
        StudentClassMembership.objects.filter(
            academic_year=year, class_section__grade_name="XI"
        )
        .select_related("student", "class_section")
        .order_by("pk")[:10]
    )
    if len(xi) < 4:
        return 0
    count = 0
    for i, membership in enumerate(xi[:8]):
        MigrationRecord.objects.create(
            student=membership.student,
            academic_year=year,
            direction="in" if i % 2 == 0 else "out",
            other_jnv=OTHER_JNVS[i % len(OTHER_JNVS)],
            stream=membership.class_section.section_name,
            notes="Hypothetical Navodaya class XI migration",
        )
        count += 1
    return count
