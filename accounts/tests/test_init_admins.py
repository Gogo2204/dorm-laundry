import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase

User = get_user_model()

VALID_ENV = {
    "ADMIN1_EMAIL": "admin1@example.com",
    "ADMIN1_PASSWORD": "long-unique-pass-1",
    "ADMIN1_FIRST_NAME": "First",
    "ADMIN1_LAST_NAME": "Admin",
    "ADMIN2_EMAIL": "admin2@example.com",
    "ADMIN2_PASSWORD": "long-unique-pass-2",
    "ADMIN2_FIRST_NAME": "Second",
    "ADMIN2_LAST_NAME": "Admin",
}


def env(**overrides):
    values = {**VALID_ENV, **overrides}
    return mock.patch.dict(os.environ, values)


class InitAdminsCommandTests(TestCase):
    def test_creates_two_active_admins(self):
        with env():
            call_command("init_admins")

        admins = User.objects.filter(is_staff=True, is_superuser=True, is_active=True)
        self.assertEqual(admins.count(), 2)
        admin1 = User.objects.get(email="admin1@example.com")
        self.assertTrue(admin1.check_password("long-unique-pass-1"))
        self.assertTrue(admin1.email_verified)
        self.assertEqual(admin1.first_name, "First")
        self.assertEqual(admin1.last_name, "Admin")

    def test_is_idempotent(self):
        with env():
            call_command("init_admins")
            call_command("init_admins")

        self.assertEqual(User.objects.count(), 2)
        # Password is not silently changed on re-run.
        with env(ADMIN1_PASSWORD="some-other-password-9"):
            call_command("init_admins")
        self.assertTrue(
            User.objects.get(email="admin1@example.com").check_password("long-unique-pass-1")
        )

    def test_missing_credentials_fail_without_tty(self):
        with env(ADMIN2_PASSWORD=""), self.assertRaisesMessage(CommandError, "ADMIN2_PASSWORD"):
            call_command("init_admins")
        self.assertEqual(User.objects.count(), 0)

    def test_weak_password_rejected(self):
        with env(ADMIN1_PASSWORD="short"), self.assertRaises(CommandError):
            call_command("init_admins")
        self.assertEqual(User.objects.count(), 0)

    def test_invalid_email_rejected(self):
        with env(ADMIN1_EMAIL="not-an-email"), self.assertRaises(CommandError):
            call_command("init_admins")
        self.assertEqual(User.objects.count(), 0)

    def test_duplicate_admin_emails_rejected(self):
        with env(ADMIN2_EMAIL="ADMIN1@example.com"), self.assertRaises(CommandError):
            call_command("init_admins")
        self.assertEqual(User.objects.count(), 0)

    def test_existing_non_admin_account_is_a_conflict(self):
        User.objects.create_user(email="admin1@example.com", password="resident-pass-word-1")
        with env(), self.assertRaises(CommandError):
            call_command("init_admins")
        # Nothing was created or promoted.
        self.assertEqual(User.objects.filter(is_superuser=True).count(), 0)

    def test_failure_on_second_admin_rolls_back_the_first(self):
        with env(ADMIN2_PASSWORD="short"), self.assertRaises(CommandError):
            call_command("init_admins")
        self.assertEqual(User.objects.count(), 0)
