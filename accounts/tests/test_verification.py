import time
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts import services
from accounts.services import VerificationResult

User = get_user_model()

PASSWORD = "laundry-rocks-2026"


def make_pending_user(email="ana@example.com"):
    return User.objects.create_user(
        email=email, password=PASSWORD, first_name="Ana", last_name="Petrova",
        is_active=False, email_verified=False,
    )


class VerifyEmailTokenTests(TestCase):
    def test_valid_token_activates_account(self):
        user = make_pending_user()
        result, verified = services.verify_email_token(services.make_verification_token(user))
        self.assertEqual(result, VerificationResult.VERIFIED)
        verified.refresh_from_db()
        self.assertTrue(verified.is_active)
        self.assertTrue(verified.email_verified)

    def test_invalid_token_rejected(self):
        result, user = services.verify_email_token("garbage:token:value")
        self.assertEqual(result, VerificationResult.INVALID)
        self.assertIsNone(user)

    def test_tampered_token_rejected(self):
        user = make_pending_user()
        token = services.make_verification_token(user)
        result, _ = services.verify_email_token(token[:-5] + "xxxxx")
        self.assertEqual(result, VerificationResult.INVALID)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_token_for_changed_email_rejected(self):
        user = make_pending_user()
        token = services.make_verification_token(user)
        user.email = "new@example.com"
        user.save()
        result, _ = services.verify_email_token(token)
        self.assertEqual(result, VerificationResult.INVALID)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_expired_token_rejected(self):
        user = make_pending_user()
        token = services.make_verification_token(user)
        future = time.time() + settings.EMAIL_VERIFICATION_TIMEOUT + 1
        with mock.patch("django.core.signing.time.time", return_value=future):
            result, _ = services.verify_email_token(token)
        self.assertEqual(result, VerificationResult.EXPIRED)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_reused_token_changes_nothing(self):
        user = make_pending_user()
        token = services.make_verification_token(user)
        services.verify_email_token(token)

        result, _ = services.verify_email_token(token)
        self.assertEqual(result, VerificationResult.ALREADY_VERIFIED)

        # Even if the account is later deactivated, the old link cannot
        # change its state back.
        user.refresh_from_db()
        services.deactivate_user(user)
        result, returned = services.verify_email_token(token)
        self.assertEqual(result, VerificationResult.DEACTIVATED)
        self.assertIsNone(returned)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_verification_does_not_reactivate_deactivated_account(self):
        user = make_pending_user()
        token = services.make_verification_token(user)
        user.deactivated_at = timezone.now()
        user.save()

        result, _ = services.verify_email_token(token)
        self.assertEqual(result, VerificationResult.DEACTIVATED)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertFalse(user.email_verified)


class VerifyEmailViewTests(TestCase):
    def test_valid_token_via_view(self):
        user = make_pending_user()
        url = reverse("verify_email", args=[services.make_verification_token(user)])
        response = self.client.get(url)
        self.assertContains(response, "Имейлът е потвърден")
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_invalid_token_via_view_is_graceful(self):
        response = self.client.get(reverse("verify_email", args=["not-a-real-token"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "не е валиден")
