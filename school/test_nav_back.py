from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse

from accounts.models import UserCategory

from .nav_back import resolve_nav_back

User = get_user_model()


class NavBackTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.staff = User.objects.create_user(
            username="navstaff",
            password="x",
            category=UserCategory.STAFF,
            is_staff=True,
        )
        self.parent = User.objects.create_user(
            username="navparent",
            password="x",
            category=UserCategory.PARENT,
        )

    def _resolve(self, path, user=None, data=None, referer=None):
        request = self.factory.get(path, data or {})
        request.user = user or self.staff
        request.resolver_match = resolve(path)
        if referer is not None:
            request.META["HTTP_REFERER"] = referer
        return resolve_nav_back(request)

    def test_home_has_no_back_control(self):
        response = self.client.get(reverse("portal-home"))
        self.assertNotContains(response, 'class="back-link"')

    def test_staff_board_has_no_back(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("portal-staff"))
        self.assertNotContains(response, 'class="back-link"')

    def test_life_hub_has_no_back(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("portal-life"))
        self.assertNotContains(response, 'class="back-link"')

    def test_mess_has_no_back(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("portal-mess"))
        self.assertNotContains(response, 'class="back-link"')

    def test_login_backs_to_home_with_icon(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, 'class="back-link"')
        self.assertContains(response, 'class="back-link-icon"')
        self.assertContains(response, reverse("portal-home"))
        self.assertContains(response, 'aria-label="Back to Home"')
        html = response.content.decode()
        box = html.find("opened-box")
        back = html.find('class="back-link"')
        self.assertTrue(0 <= box < back)
        self.assertEqual(html.count('class="back-link"'), 1)

    def test_life_section_backs_to_hub(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("portal-events"))
        self.assertContains(response, reverse("portal-life"))
        self.assertContains(response, 'aria-label="Back to Vidyalaya life"')
        self.assertContains(response, 'class="back-link-icon"')
        self.assertNotContains(response, 'class="back-link-label"')
        html = response.content.decode()
        box = html.find("opened-box")
        back = html.find('class="back-link"')
        self.assertTrue(0 <= box < back)
        self.assertEqual(html.count('class="back-link"'), 1)
        self.assertNotIn("jnv-back-bar", html)

    def test_parent_history_backs_to_ward(self):
        back = self._resolve("/parent/students/1/attendance/", user=self.parent)
        self.assertEqual(back["href"], reverse("portal-parent"))
        self.assertEqual(back["label"], "My ward")

    def test_circulars_has_no_back(self):
        self.client.force_login(self.parent)
        response = self.client.get(reverse("portal-circulars"))
        self.assertNotContains(response, 'class="back-link"')

    def test_staff_mark_backs_to_today(self):
        back = self._resolve("/staff/sessions/1/attendance/")
        self.assertEqual(back["href"], reverse("portal-staff"))
        self.assertEqual(back["label"], "Today")

    def test_admin_class_list_has_no_back(self):
        self.assertIsNone(self._resolve("/admin/school/classsection/"))

    def test_class_hub_backs_to_classes(self):
        back = self._resolve("/admin/school/classsection/class/12/")
        self.assertEqual(back["href"], reverse("admin:school_classsection_changelist"))
        self.assertEqual(back["label"], "Classes")

    def test_house_hub_backs_to_houses(self):
        back = self._resolve("/admin/school/house/house/4/")
        self.assertEqual(back["href"], reverse("admin:school_house_changelist"))
        self.assertEqual(back["label"], "Houses")

    def test_biodata_from_class_query(self):
        back = self._resolve("/admin/school/student/9/biodata/", data={"from_class": "3"})
        self.assertEqual(
            back["href"],
            reverse("admin:school_classsection_class_students", args=[3]),
        )
        self.assertEqual(back["label"], "Class")

    def test_referer_does_not_override_parent(self):
        previous = "/admin/school/house/house/4/students/"
        back = self._resolve(
            "/staff/sessions/1/attendance/",
            referer=f"http://testserver{previous}",
        )
        self.assertEqual(back["href"], reverse("portal-staff"))
        self.assertEqual(back["label"], "Today")

    def test_rendered_back_link_uses_mapped_parent(self):
        self.client.force_login(self.staff)
        previous = reverse("portal-library")
        response = self.client.get(
            reverse("portal-events"),
            HTTP_REFERER=f"http://testserver{previous}",
        )
        self.assertContains(response, f'class="back-link" href="{reverse("portal-life")}"')
        self.assertContains(response, 'class="back-link-icon"')
        self.assertNotContains(response, f'class="back-link" href="{previous}"')

    def test_landing_has_no_back_even_with_referer(self):
        self.assertIsNone(
            self._resolve(
                reverse("portal-staff"),
                referer="http://testserver/life/events/",
            )
        )
