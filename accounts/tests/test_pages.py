from django.test import TestCase
from django.urls import reverse

from laundry.tests.utils import make_resident


class PageSmokeTests(TestCase):
    def test_root_redirects_anonymous_visitors_to_login_via_schedule(self):
        # "/" -> schedule -> login for unauthenticated visitors.
        response = self.client.get("/")
        self.assertRedirects(response, reverse("schedule"), target_status_code=302)
        response = self.client.get("/", follow=True)
        self.assertEqual(response.redirect_chain[-1][0], f"{reverse('login')}?next={reverse('schedule')}")

    def test_root_shows_schedule_for_authenticated_users(self):
        self.client.force_login(make_resident("root@example.com"))
        response = self.client.get("/", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Laundry schedule")

    def test_admin_requires_login(self):
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])
