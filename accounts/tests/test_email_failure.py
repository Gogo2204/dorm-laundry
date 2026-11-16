"""A mail outage must never turn a saved registration into a server error,
and administrators must be able to resend the verification link.
"""

from smtplib import SMTPAuthenticationError
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from accounts.models import ApprovedResident

User = get_user_model()

GOOD_PASSWORD = "laundry-rocks-2026"
FAILED_HEADING = "имейлът не беше изпратен"
SENT_HEADING = "проверете имейла си"


def form_data():
    return {
        "first_name": "Ana",
        "last_name": "Petrova",
        "email": "ana@example.com",
        "password1": GOOD_PASSWORD,
        "password2": GOOD_PASSWORD,
    }


class RegistrationEmailFailureTests(TestCase):
    def setUp(self):
        self.entry = ApprovedResident.objects.create(first_name="Ana", last_name="Petrova")

    def test_mail_failure_keeps_account_and_shows_honest_message(self):
        error = SMTPAuthenticationError(535, b"5.7.8 Authentication failed")
        with mock.patch("accounts.services.send_mail", side_effect=error), self.assertLogs(
            "accounts.services", level="ERROR"
        ):
            response = self.client.post(reverse("register"), form_data(), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, FAILED_HEADING)
        self.assertNotContains(response, SENT_HEADING)

        user = User.objects.get(email="ana@example.com")
        self.assertFalse(user.is_active)
        self.assertFalse(user.email_verified)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.registered_user, user)

    def test_failure_notice_is_shown_only_once(self):
        with mock.patch("accounts.services.send_mail", side_effect=OSError("down")), self.assertLogs(
            "accounts.services", level="ERROR"
        ):
            self.client.post(reverse("register"), form_data())
        self.client.get(reverse("registration_pending"))
        response = self.client.get(reverse("registration_pending"))
        self.assertContains(response, SENT_HEADING)
        self.assertNotContains(response, FAILED_HEADING)

    def test_successful_send_shows_normal_message(self):
        response = self.client.post(reverse("register"), form_data(), follow=True)
        self.assertContains(response, SENT_HEADING)
        self.assertNotContains(response, FAILED_HEADING)
        self.assertEqual(len(mail.outbox), 1)


class ResendVerificationAdminActionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="admin@example.com",
            password=GOOD_PASSWORD,
            first_name="Admin",
            last_name="User",
            email_verified=True,
        )
        self.client.force_login(self.admin)
        self.pending = User.objects.create_user(
            email="pending@example.com",
            password=GOOD_PASSWORD,
            first_name="Pen",
            last_name="Ding",
            is_active=False,
            email_verified=False,
        )
        self.verified = User.objects.create_user(
            email="done@example.com",
            password=GOOD_PASSWORD,
            first_name="Ver",
            last_name="Ified",
            is_active=True,
            email_verified=True,
        )
        self.url = reverse("admin:accounts_user_changelist")

    def run_action(self, *users):
        return self.client.post(
            self.url,
            {
                "action": "resend_verification_emails",
                "_selected_action": [str(u.pk) for u in users],
            },
            follow=True,
        )

    def test_resends_only_to_unverified_users(self):
        response = self.run_action(self.pending, self.verified)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["pending@example.com"])
        self.assertIn("/accounts/verify/", mail.outbox[0].body)
        self.assertContains(response, "Пропуснати потребители: 1")

    def test_resent_link_activates_account(self):
        self.run_action(self.pending)
        link = next(
            line.strip() for line in mail.outbox[0].body.splitlines() if "/accounts/verify/" in line
        )
        self.client.logout()
        self.client.get(link.split("testserver", 1)[1])
        self.pending.refresh_from_db()
        self.assertTrue(self.pending.email_verified)
        self.assertTrue(self.pending.is_active)

    def test_reports_failure_without_crashing(self):
        with mock.patch("accounts.services.send_mail", side_effect=OSError("down")), self.assertLogs(
            "accounts.services", level="ERROR"
        ):
            response = self.run_action(self.pending)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неуспешно изпращане: 1")
