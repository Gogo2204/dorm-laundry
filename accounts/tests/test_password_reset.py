from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts import services

User = get_user_model()

PASSWORD = "laundry-rocks-2026"
NEW_PASSWORD = "fresh-towels-2026"


def make_active_user(email="ana@example.com"):
    return User.objects.create_user(
        email=email, password=PASSWORD, first_name="Ana", last_name="Petrova",
        is_active=True, email_verified=True,
    )


def reset_url_for(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return reverse("password_reset_confirm", args=[uid, token])


class PasswordResetRequestTests(TestCase):
    url = reverse("password_reset")

    def test_reset_email_sent_for_active_user(self):
        make_active_user()
        response = self.client.post(self.url, {"email": "ana@example.com"})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("password-reset", mail.outbox[0].body)

    def test_unknown_email_gets_same_response_and_no_email(self):
        response = self.client.post(self.url, {"email": "nobody@example.com"})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_deactivated_user_gets_no_email(self):
        user = make_active_user()
        services.deactivate_user(user)
        response = self.client.post(self.url, {"email": "ana@example.com"})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)


class PasswordResetConfirmTests(TestCase):
    def _complete_reset(self, url, new_password=NEW_PASSWORD):
        """Follow the confirm redirect (token moves into the session) and post."""
        response = self.client.get(url, follow=True)
        form_url = response.redirect_chain[-1][0]
        return self.client.post(
            form_url, {"new_password1": new_password, "new_password2": new_password}
        )

    def test_full_reset_flow(self):
        user = make_active_user()
        response = self._complete_reset(reset_url_for(user))
        self.assertRedirects(response, reverse("password_reset_complete"))

        user.refresh_from_db()
        self.assertTrue(user.check_password(NEW_PASSWORD))
        # Verification status and activity are untouched.
        self.assertTrue(user.email_verified)
        self.assertTrue(user.is_active)

        login = self.client.post(
            reverse("login"), {"username": "ana@example.com", "password": NEW_PASSWORD}
        )
        self.assertRedirects(login, reverse("schedule"))

    def test_weak_new_password_rejected(self):
        user = make_active_user()
        response = self._complete_reset(reset_url_for(user), new_password="short")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))

    def test_invalid_token_shows_invalid_link_page(self):
        user = make_active_user()
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        url = reverse("password_reset_confirm", args=[uid, "bad-token"])
        response = self.client.get(url)
        self.assertContains(response, "невалиден или е изтекъл")

    def test_used_token_cannot_be_reused(self):
        user = make_active_user()
        url = reset_url_for(user)
        self._complete_reset(url)
        # The token hash changed with the password; the old link is dead.
        response = self.client.get(url, follow=True)
        self.assertContains(response, "невалиден или е изтекъл")

    def test_reset_does_not_reactivate_deactivated_user(self):
        user = make_active_user()
        url = reset_url_for(user)
        services.deactivate_user(user)
        # Even with a pre-deactivation link, completing the flow must not
        # reactivate the account. (Deactivation changes no token inputs, so
        # the link itself may still validate.)
        self._complete_reset(url)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertIsNotNone(user.deactivated_at)
