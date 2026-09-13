from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import UserCategory
from school.models import AcademicYear, TeacherProfile
from datetime import date


User = get_user_model()


class UserProfileAdminTests(TestCase):
    def setUp(self):
        AcademicYear.objects.create(
            name="2026-27",
            start_date=date(2026, 4, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        self.admin_user = User.objects.create_user(
            username="profile-admin",
            password="x",
            category=UserCategory.ADMINISTRATION,
            is_staff=True,
            is_superuser=True,
            first_name="Janardan",
            last_name="Singh",
        )
        self.staff = User.objects.create_user(
            username="profile-staff",
            password="x",
            category=UserCategory.STAFF,
            first_name="Rajesh",
            last_name="Varma",
        )
        TeacherProfile.objects.create(user=self.staff)

    def test_user_click_opens_biodata_tasks_and_permissions(self):
        self.client.force_login(self.admin_user)
        change = self.client.get(
            reverse("admin:accounts_user_change", args=[self.staff.pk])
        )
        self.assertEqual(change.status_code, 302)
        self.assertIn("/profile/", change.url)
        response = self.client.get(
            reverse("admin:accounts_user_profile", args=[self.staff.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rajesh Varma")
        self.assertContains(response, "Biodata")
        self.assertContains(response, "Tasks today")
        self.assertContains(response, "Permissions this role should have")
        self.assertContains(response, "staff portal")

    def test_admin_can_add_extra_user_biodata_row(self):
        self.client.force_login(self.admin_user)
        url = reverse("admin:accounts_user_profile", args=[self.staff.pk])
        response = self.client.post(
            url,
            {
                "biodata_action": "add",
                "label": "Employee code",
                "value": "JNV-014",
            },
        )
        self.assertEqual(response.status_code, 302)
        page = self.client.get(url)
        self.assertContains(page, "Employee code")
        self.assertContains(page, "JNV-014")
        self.assertContains(page, "Add row")
