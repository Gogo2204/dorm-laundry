from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts import services

User = get_user_model()

PASSWORD = "laundry-rocks-2026"


def make_active_user(email="ana@example.com"):
    return User.objects.create_user(
        email=email, password=PASSWORD, first_name="Ana", last_name="Petrova",
        is_active=True, email_verified=True,
    )


class LoginTests(TestCase):
    url = reverse("login")

    def test_valid_login_redirects_to_schedule(self):
        make_active_user()
        response = self.client.post(
            self.url, {"username": "ana@example.com", "password": PASSWORD}
        )
        self.assertRedirects(response, reverse("schedule"))

    def test_invalid_password_shows_generic_error(self):
        make_active_user()
        response = self.client.post(
            self.url, {"username": "ana@example.com", "password": "wrong-pass"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["user"].is_authenticated)

    def test_unknown_email_gets_identical_error(self):
        make_active_user()
        wrong_pw = self.client.post(
            self.url, {"username": "ana@example.com", "password": "wrong-pass"}
        )
        unknown = self.client.post(
            self.url, {"username": "nobody@example.com", "password": "wrong-pass"}
        )
        self.assertEqual(
            wrong_pw.context["form"].errors, unknown.context["form"].errors
        )

    def test_unverified_account_cannot_log_in(self):
        User.objects.create_user(
            email="pending@example.com", password=PASSWORD, is_active=False
        )
        response = self.client.post(
            self.url, {"username": "pending@example.com", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["user"].is_authenticated)

    def test_deactivated_account_cannot_log_in(self):
        user = make_active_user()
        services.deactivate_user(user)
        response = self.client.post(
            self.url, {"username": "ana@example.com", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["user"].is_authenticated)


class LogoutTests(TestCase):
    def test_logout_via_post_redirects_to_login(self):
        user = make_active_user()
        self.client.force_login(user)
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_rejects_get(self):
        self.client.force_login(make_active_user())
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)


class ProtectedPageTests(TestCase):
    def test_account_page_requires_login(self):
        response = self.client.get(reverse("account"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_account_page_shows_user_info(self):
        self.client.force_login(make_active_user())
        response = self.client.get(reverse("account"))
        self.assertContains(response, "ana@example.com")
        self.assertContains(response, "Ana")

    def test_deactivation_locks_out_existing_session(self):
        user = make_active_user()
        self.client.force_login(user)
        services.deactivate_user(user)
        response = self.client.get(reverse("account"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_deactivation_preserves_account_data(self):
        user = make_active_user()
        services.deactivate_user(user)
        user.refresh_from_db()
        self.assertIsNotNone(user.deactivated_at)
        self.assertTrue(user.email_verified)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
