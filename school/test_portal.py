from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserCategory

from .models import (
    AcademicYear,
    ActivitySession,
    ActivityType,
    AttendanceEntry,
    AttendanceStatus,
    AudienceKind,
    Circular,
    CircularAudience,
    ClassSection,
    House,
    MessMenu,
    OutingPass,
    OutingStatus,
    ParentProfile,
    Student,
    StudentGuardian,
    StudentHouseMembership,
    VidyalayaProfile,
)


User = get_user_model()


class PortalAccessTests(TestCase):
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
        )
        self.other_staff = User.objects.create_user(
            username="teacher2",
            password="x",
            category=UserCategory.STAFF,
        )
        self.parent = User.objects.create_user(
            username="parent1",
            password="x",
            category=UserCategory.PARENT,
        )
        self.other_parent = User.objects.create_user(
            username="parent2",
            password="x",
            category=UserCategory.PARENT,
        )
        self.profile = ParentProfile.objects.create(user=self.parent)
        self.other_profile = ParentProfile.objects.create(user=self.other_parent)
        self.activity = ActivityType.objects.create(
            name="Period",
            takes_attendance=True,
            default_audience_kind=AudienceKind.CLASS,
        )
        self.student = Student.objects.create(
            admission_number="A1",
            roll_number=1,
            first_name="Ada",
            last_name="A",
            date_of_birth=date(2014, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        self.other_student = Student.objects.create(
            admission_number="B1",
            roll_number=2,
            first_name="Ben",
            last_name="B",
            date_of_birth=date(2014, 1, 2),
            gender="male",
            class_section=self.section,
            academic_year=self.year,
        )
        StudentGuardian.objects.create(
            parent_profile=self.profile,
            student=self.student,
        )
        StudentGuardian.objects.create(
            parent_profile=self.other_profile,
            student=self.other_student,
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

    def test_guest_home_and_login_required(self):
        response = self.client.get(reverse("portal-home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Log in")
        response = self.client.get(reverse("portal-staff"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_staff_sees_authorized_session_and_can_mark(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("portal-staff"),
            {"date": "2026-08-28"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Period 3")
        url = reverse("portal-staff-mark", args=[self.session.pk])
        response = self.client.post(
            url,
            {
                "action": "save",
                f"status_{self.student.pk}": AttendanceStatus.PRESENT,
            },
        )
        self.assertEqual(response.status_code, 302)
        entry = AttendanceEntry.objects.get()
        self.assertEqual(entry.student, self.student)
        self.assertEqual(entry.taken_by, self.staff)
        self.assertEqual(entry.status, AttendanceStatus.PRESENT)

    def test_unrelated_staff_cannot_mark_on_portal(self):
        self.client.force_login(self.other_staff)
        response = self.client.get(reverse("portal-staff-mark", args=[self.session.pk]))
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            reverse("portal-staff-mark", args=[self.session.pk]),
            {
                "action": "save",
                f"status_{self.student.pk}": AttendanceStatus.PRESENT,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AttendanceEntry.objects.count(), 0)

    def test_parent_sees_only_linked_child(self):
        self.client.force_login(self.parent)
        response = self.client.get(reverse("portal-parent"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ada")
        self.assertNotContains(response, "Ben")
        own = reverse("portal-parent-history", args=[self.student.pk])
        other = reverse("portal-parent-history", args=[self.other_student.pk])
        response = self.client.get(own)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ada")
        response = self.client.get(other)
        self.assertEqual(response.status_code, 404)

    def test_parent_cannot_use_staff_mark_url(self):
        self.client.force_login(self.parent)
        response = self.client.get(reverse("portal-staff"))
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            reverse("portal-staff-mark", args=[self.session.pk]),
            {
                "action": "save",
                f"status_{self.student.pk}": AttendanceStatus.PRESENT,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AttendanceEntry.objects.count(), 0)

    def test_staff_cannot_open_parent_pages(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("portal-parent")).status_code, 403)
        self.assertEqual(
            self.client.get(
                reverse("portal-parent-history", args=[self.student.pk])
            ).status_code,
            403,
        )

    def test_parent_history_is_read_only(self):
        AttendanceEntry.objects.create(
            activity_session=self.session,
            student=self.student,
            status=AttendanceStatus.ABSENT,
            taken_by=self.staff,
        )
        self.client.force_login(self.parent)
        response = self.client.get(
            reverse("portal-parent-history", args=[self.student.pk]),
            {"academic_year": self.year.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Absent")
        self.assertNotContains(response, "Mark attendance")
        self.assertNotContains(response, "Correction audit")

    def test_vidyalaya_board_mess_circulars_and_gate_pass(self):
        house = House.objects.create(name="Aravali")
        StudentHouseMembership.objects.create(
            student=self.student,
            house=house,
            academic_year=self.year,
        )
        MessMenu.objects.create(
            date=date(2026, 8, 28),
            breakfast="Poha",
            lunch="Dal rice",
            evening_snacks="Tea",
            dinner="Roti sabzi",
        )
        Circular.objects.create(
            title="Sunday meeting",
            body="Second Sunday visiting hours.",
            audience=CircularAudience.PARENTS,
            published_on=date(2026, 8, 28),
        )
        Circular.objects.create(
            title="PT punctuality",
            body="Fall in at 05:30.",
            audience=CircularAudience.STAFF,
            published_on=date(2026, 8, 28),
        )
        outing = OutingPass.objects.create(
            student=self.student,
            date=date(2026, 8, 28),
            departure_time=time(9, 0),
            purpose="CHC / hospital",
            destination="CHC",
            status=OutingStatus.APPROVED,
            issued_by=self.staff,
        )
        AttendanceEntry.objects.create(
            activity_session=self.session,
            student=self.student,
            status=AttendanceStatus.SICK,
            taken_by=self.staff,
        )
        self.client.force_login(self.staff)
        board = self.client.get(reverse("portal-staff"), {"date": "2026-08-28"})
        self.assertContains(board, "Day board")
        self.assertContains(board, "Strength")
        self.assertContains(board, "Poha")
        self.assertContains(board, "PT punctuality")
        exceptions = self.client.get(
            reverse("portal-exceptions"), {"date": "2026-08-28"}
        )
        self.assertContains(exceptions, "Ada")
        self.assertContains(exceptions, "Sick")
        outings = self.client.get(reverse("portal-outings"), {"date": "2026-08-28"})
        self.assertContains(outings, "CHC")
        marked_out = self.client.post(
            reverse("portal-outings") + "?date=2026-08-28",
            {"pass_id": outing.pk, "action": "out"},
        )
        self.assertEqual(marked_out.status_code, 302)
        outing.refresh_from_db()
        self.assertEqual(outing.status, OutingStatus.OUT)
        mess = self.client.get(reverse("portal-mess"), {"date": "2026-08-28"})
        self.assertContains(mess, "Dal rice")
        self.client.force_login(self.parent)
        parent = self.client.get(reverse("portal-parent"))
        self.assertContains(parent, "Aravali")
        self.assertContains(parent, "Sunday meeting")
        self.assertNotContains(parent, "PT punctuality")
        self.client.logout()
        self.assertContains(self.client.get(reverse("portal-home")), "East Singhbhum")
        self.assertContains(self.client.get(reverse("portal-home")), "Patna")
        self.assertNotContains(self.client.get(reverse("portal-home")), "Bhopal")
        profile = VidyalayaProfile.load()
        self.assertEqual(profile.district, "East Singhbhum")
        self.assertEqual(profile.nvs_region, "Patna")

