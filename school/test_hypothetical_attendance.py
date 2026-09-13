from datetime import date

from django.core.management import call_command
from django.test import TestCase

from accounts.models import User, UserCategory
from school.hypothetical_attendance import fill_random_attendance
from school.models import (
    AcademicYear,
    ActivitySession,
    ActivityType,
    AttendanceEntry,
    AttendanceStatus,
    AudienceKind,
    ClassSection,
    Student,
    TeacherProfile,
)


class HypotheticalAttendanceTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.section = ClassSection.objects.create(
            grade_name="VI",
            section_name="A",
            display_name="VI-A",
        )
        self.staff = User.objects.create_user(
            username="hypo-staff",
            password="x",
            category=UserCategory.STAFF,
        )
        TeacherProfile.objects.create(user=self.staff)
        self.student = Student.objects.create(
            admission_number="HY1",
            roll_number=1,
            first_name="Hypothetical",
            last_name="Student",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        self.period = ActivityType.objects.create(
            name="Period",
            takes_attendance=True,
            default_audience_kind=AudienceKind.CLASS,
        )

    def test_fill_marks_every_roster_student(self):
        session = ActivitySession.objects.create(
            date=date(2026, 9, 11),
            academic_year=self.year,
            activity_type=self.period,
            name="Period 1",
            start_time="08:00",
            end_time="08:40",
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        created, skipped = fill_random_attendance([session], seed=1)
        self.assertEqual(created, 1)
        self.assertEqual(skipped, 0)
        entry = AttendanceEntry.objects.get(activity_session=session, student=self.student)
        self.assertIn(entry.status, AttendanceStatus.values)
        created_again, skipped_again = fill_random_attendance([session], seed=1)
        self.assertEqual(created_again, 0)
        self.assertEqual(skipped_again, 1)

    def test_management_command_covers_weekday_sessions(self):
        call_command("seed_hypothetical_attendance", days=1, seed=7)
        self.assertTrue(
            AttendanceEntry.objects.filter(student=self.student).exists()
        )
        self.assertGreaterEqual(
            AttendanceEntry.objects.filter(student=self.student).count(),
            4,
        )

    def test_seeds_at_least_two_academic_years(self):
        from school.models import StudentClassMembership

        call_command("seed_hypothetical_attendance", days=1, seed=7)
        names = list(
            AcademicYear.objects.order_by("start_date").values_list("name", flat=True)
        )
        self.assertGreaterEqual(len(names), 2)
        self.assertIn("2025-26", names)
        self.assertIn("2026-27", names)
        self.assertEqual(
            AcademicYear.objects.filter(is_current=True).count(),
            1,
        )
        previous = AcademicYear.objects.get(name="2025-26")
        self.assertTrue(
            StudentClassMembership.objects.filter(
                academic_year=previous,
                student=self.student,
            ).exists()
        )
        self.assertTrue(
            AttendanceEntry.objects.filter(
                student=self.student,
                activity_session__academic_year=previous,
            ).exists()
        )
