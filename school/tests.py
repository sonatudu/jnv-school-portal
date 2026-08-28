from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserCategory

from .attendance_roster import orphan_entries_for_session, students_for_session
from .models import (
    AcademicYear,
    ActivitySession,
    ActivitySessionParticipant,
    ActivityType,
    AttendanceEntry,
    AttendanceRevision,
    AttendanceStatus,
    AudienceKind,
    ClassSection,
    House,
    Student,
    StudentGroup,
    StudentGroupMembership,
    StudentHouseMembership,
)


User = get_user_model()


class AttendanceRosterTests(TestCase):
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
        self.other_section = ClassSection.objects.create(
            grade_name="VI",
            section_name="B",
            display_name="VI-B",
        )
        self.house = House.objects.create(name="Aravali", code="AR")
        self.staff = User.objects.create_user(
            username="teacher1",
            password="x",
            category=UserCategory.STAFF,
        )
        self.activity = ActivityType.objects.create(
            name="Period",
            takes_attendance=True,
            default_audience_kind=AudienceKind.CLASS,
        )
        self.student_a = Student.objects.create(
            admission_number="A1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        self.student_b = Student.objects.create(
            admission_number="B1",
            roll_number=2,
            first_name="Ben",
            last_name="B",
            date_of_birth=date(2014, 1, 2),
            gender="male",
            class_section=self.other_section,
            academic_year=self.year,
        )
        self.inactive = Student.objects.create(
            admission_number="I1",
            roll_number=3,
            first_name="Ina",
            last_name="I",
            date_of_birth=date(2014, 1, 3),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
            is_active=False,
        )

    def _session(self, **kwargs):
        defaults = {
            "date": date(2026, 8, 28),
            "academic_year": self.year,
            "activity_type": self.activity,
            "name": "Period 3",
            "start_time": time(9, 0),
            "end_time": time(9, 40),
            "audience_kind": AudienceKind.CLASS,
            "class_section": self.section,
            "responsible_staff": self.staff,
        }
        defaults.update(kwargs)
        return ActivitySession.objects.create(**defaults)

    def test_class_roster_is_live_active_students(self):
        session = self._session()
        roster = list(students_for_session(session))
        self.assertEqual(roster, [self.student_a])

    def test_house_roster_uses_membership_not_class(self):
        StudentHouseMembership.objects.create(
            student=self.student_b,
            house=self.house,
            academic_year=self.year,
        )
        session = self._session(
            audience_kind=AudienceKind.HOUSE,
            class_section=None,
            house=self.house,
        )
        roster = list(students_for_session(session))
        self.assertEqual(roster, [self.student_b])

    def test_group_roster_uses_snapshot_not_live_membership(self):
        group = StudentGroup.objects.create(name="Band", academic_year=self.year)
        StudentGroupMembership.objects.create(group=group, student=self.student_a)
        session = self._session(
            audience_kind=AudienceKind.STUDENT_GROUP,
            class_section=None,
            student_group=group,
        )
        ActivitySessionParticipant.objects.create(session=session, student=self.student_b)
        roster = list(students_for_session(session))
        self.assertEqual(roster, [self.student_b])

    def test_school_roster_is_live_year_students(self):
        session = self._session(
            audience_kind=AudienceKind.SCHOOL,
            class_section=None,
        )
        roster = list(students_for_session(session))
        self.assertEqual(roster, [self.student_a, self.student_b])

    def test_orphan_entries_are_returned(self):
        session = self._session()
        entry = AttendanceEntry.objects.create(
            activity_session=session,
            student=self.student_b,
            status=AttendanceStatus.PRESENT,
            taken_by=self.staff,
        )
        orphans = list(orphan_entries_for_session(session, [self.student_a.pk]))
        self.assertEqual(orphans, [entry])


class MarkAttendanceAdminTests(TestCase):
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
            username="teacher1",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.other = User.objects.create_user(
            username="teacher2",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.activity = ActivityType.objects.create(
            name="Period",
            takes_attendance=True,
        )
        self.no_att = ActivityType.objects.create(
            name="Assembly no roll",
            takes_attendance=False,
        )
        self.student_a = Student.objects.create(
            admission_number="A1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        self.student_b = Student.objects.create(
            admission_number="A2",
            roll_number=2,
            first_name="Ben",
            last_name="B",
            date_of_birth=date(2014, 1, 2),
            gender="male",
            class_section=self.section,
            academic_year=self.year,
        )
        self.session = ActivitySession.objects.create(
            date=date(2026, 8, 28),
            academic_year=self.year,
            activity_type=self.activity,
            name="Period 3",
            start_time=time(9, 0),
            end_time=time(9, 40),
            audience_kind=AudienceKind.CLASS,
            class_section=self.section,
            responsible_staff=self.staff,
        )
        self.url = reverse(
            "admin:school_activitysession_mark_attendance",
            args=[self.session.pk],
        )

    def test_responsible_partial_save_and_correction(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ada")
        self.assertContains(response, "0 / 2 marked")

        response = self.client.post(
            self.url,
            {
                "action": "save",
                f"status_{self.student_a.pk}": AttendanceStatus.PRESENT,
                f"notes_{self.student_a.pk}": "first",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AttendanceEntry.objects.count(), 1)
        entry = AttendanceEntry.objects.get()
        self.assertEqual(entry.student, self.student_a)
        self.assertEqual(entry.taken_by, self.staff)
        self.assertIsNone(entry.updated_by)
        self.assertEqual(AttendanceRevision.objects.count(), 0)

        taken_at = entry.taken_at
        response = self.client.post(
            self.url,
            {
                "action": "save",
                f"status_{self.student_a.pk}": AttendanceStatus.LATE,
                f"notes_{self.student_a.pk}": "late arrival",
                "change_reason": "came late",
            },
        )
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.status, AttendanceStatus.LATE)
        self.assertEqual(entry.taken_by, self.staff)
        self.assertEqual(entry.taken_at, taken_at)
        self.assertEqual(entry.updated_by, self.staff)
        revision = AttendanceRevision.objects.get()
        self.assertEqual(revision.old_status, AttendanceStatus.PRESENT)
        self.assertEqual(revision.new_status, AttendanceStatus.LATE)
        self.assertEqual(revision.reason, "came late")

        response = self.client.post(
            self.url,
            {
                "action": "save",
                f"status_{self.student_a.pk}": AttendanceStatus.LATE,
                f"notes_{self.student_a.pk}": "notes only",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AttendanceRevision.objects.count(), 1)
        entry.refresh_from_db()
        self.assertEqual(entry.notes, "notes only")
        self.assertEqual(entry.taken_by, self.staff)

        response = self.client.post(
            self.url,
            {"action": "save_unmarked_present"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AttendanceEntry.objects.count(), 2)
        entry.refresh_from_db()
        self.assertEqual(entry.status, AttendanceStatus.LATE)
        other = AttendanceEntry.objects.get(student=self.student_b)
        self.assertEqual(other.status, AttendanceStatus.PRESENT)
        self.assertEqual(other.taken_by, self.staff)

        response = self.client.post(
            self.url,
            {
                "action": "save",
                f"notes_{self.student_a.pk}": "cleared",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be cleared")
        entry.refresh_from_db()
        self.assertEqual(entry.status, AttendanceStatus.LATE)

    def test_unrelated_staff_gets_403(self):
        self.client.force_login(self.other)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response,
            "You are not authorized to mark attendance for this session.",
            status_code=403,
        )

    def test_non_attendance_activity_gets_403(self):
        self.session.activity_type = self.no_att
        self.session.save()
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response,
            "This activity does not take attendance.",
            status_code=403,
        )
