from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError
from django.test import TestCase

User = get_user_model()


class UserModelTests(TestCase):
    def test_email_is_the_login_identifier(self):
        self.assertEqual(User.USERNAME_FIELD, "email")
        self.assertFalse(hasattr(User, "username") and User.username)

    def test_create_user(self):
        user = User.objects.create_user(
            email="ana@example.com", password="s3cret-pass-word", first_name="Ana", last_name="Petrova"
        )
        self.assertEqual(user.email, "ana@example.com")
        self.assertTrue(user.check_password("s3cret-pass-word"))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.email_verified)

    def test_create_user_requires_email(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password="s3cret-pass-word")

    def test_email_domain_is_normalized(self):
        user = User.objects.create_user(email="ana@EXAMPLE.COM", password="s3cret-pass-word")
        self.assertEqual(user.email, "ana@example.com")

    def test_duplicate_email_rejected(self):
        User.objects.create_user(email="dup@example.com", password="s3cret-pass-word")
        with self.assertRaises(IntegrityError):
            User.objects.create_user(email="dup@example.com", password="other-pass-word")

    def test_create_superuser(self):
        admin = User.objects.create_superuser(email="admin@example.com", password="s3cret-pass-word")
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)

    def test_create_superuser_rejects_downgraded_flags(self):
        with self.assertRaises(ValueError):
            User.objects.create_superuser(
                email="bad@example.com", password="s3cret-pass-word", is_superuser=False
            )
        with self.assertRaises(ValueError):
            User.objects.create_superuser(
                email="bad@example.com", password="s3cret-pass-word", is_staff=False
            )

    def test_authenticate_with_email(self):
        User.objects.create_user(email="login@example.com", password="s3cret-pass-word")
        self.assertIsNotNone(authenticate(username="login@example.com", password="s3cret-pass-word"))
        self.assertIsNone(authenticate(username="login@example.com", password="wrong"))

    def test_inactive_user_cannot_authenticate(self):
        User.objects.create_user(
            email="inactive@example.com", password="s3cret-pass-word", is_active=False
        )
        self.assertIsNone(authenticate(username="inactive@example.com", password="s3cret-pass-word"))
