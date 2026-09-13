from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserCategory

from .jnv_operations import seed_jnv_operations
from .models import (
    AcademicYear,
    ClassSection,
    House,
    LibraryBook,
    ParentProfile,
    Student,
    StudentGuardian,
    StudentHouseMembership,
    Subject,
    VidyalayaEvent,
)

User = get_user_model()


class JnvOperationsTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.section = ClassSection.objects.create(
            grade_name="XI",
            section_name="Science",
            display_name="XI-Science",
        )
        self.house = House.objects.create(name="Aravali")
        self.staff = User.objects.create_user(
            username="ops-staff",
            password="x",
            category=UserCategory.STAFF,
        )
        self.parent = User.objects.create_user(
            username="ops-parent",
            password="x",
            category=UserCategory.PARENT,
        )
        self.student = Student.objects.create(
            admission_number="JNV-OPS-1",
            roll_number=1,
            first_name="Ada",
            last_name="Lovelace",
            date_of_birth=date(2010, 1, 1),
            gender="female",
            class_section=self.section,
            academic_year=self.year,
        )
        StudentHouseMembership.objects.create(
            student=self.student,
            house=self.house,
            academic_year=self.year,
        )
        ParentProfile.objects.create(user=self.parent)
        StudentGuardian.objects.create(
            parent_profile=ParentProfile.objects.get(user=self.parent),
            student=self.student,
        )
        Subject.objects.create(name="English", code="ENG")
        Subject.objects.create(name="Mathematics", code="MAT")
        for i in range(3):
            other = Student.objects.create(
                admission_number=f"JNV-OPS-{i+2}",
                roll_number=i + 2,
                first_name=f"Stu{i}",
                last_name="Test",
                date_of_birth=date(2010, 2, 1),
                gender="male",
                class_section=self.section,
                academic_year=self.year,
            )
            StudentHouseMembership.objects.create(
                student=other,
                house=self.house,
                academic_year=self.year,
            )

    def test_seed_and_staff_pages(self):
        result = seed_jnv_operations(today=date(2026, 9, 12), seed=1)
        self.assertGreater(result["events"], 0)
        self.assertGreater(LibraryBook.objects.count(), 0)
        self.assertTrue(VidyalayaEvent.objects.exists())
        self.client.force_login(self.staff)
        hub = self.client.get(reverse("portal-life"))
        self.assertEqual(hub.status_code, 200)
        self.assertContains(hub, "Vidyalaya life")
        self.assertContains(hub, "VMC")
        self.assertEqual(self.client.get(reverse("portal-events")).status_code, 200)
        self.assertEqual(self.client.get(reverse("portal-house-life")).status_code, 200)
        self.assertContains(self.client.get(reverse("portal-library")), "ES-0001")
        self.assertEqual(self.client.get(reverse("portal-exams")).status_code, 200)
        self.assertContains(self.client.get(reverse("portal-vmc")), "Deputy Commissioner")
        self.assertEqual(self.client.get(reverse("portal-sick-bay")).status_code, 200)
        self.assertEqual(self.client.get(reverse("portal-visitors")).status_code, 200)
        self.assertEqual(self.client.get(reverse("portal-migration")).status_code, 200)
        self.assertEqual(self.client.get(reverse("portal-vvn")).status_code, 200)

    def test_parent_cannot_open_sick_bay(self):
        seed_jnv_operations(today=timezone.localdate(), seed=1)
        self.client.force_login(self.parent)
        self.assertEqual(self.client.get(reverse("portal-sick-bay")).status_code, 403)
        lib = self.client.get(reverse("portal-library"))
        self.assertEqual(lib.status_code, 200)
        self.assertContains(lib, "Ada")
        self.assertContains(self.client.get(reverse("portal-life")), "Vidyalaya life")
        self.assertNotContains(self.client.get(reverse("portal-life")), "Sick bay")
