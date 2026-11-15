from unittest import mock

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from access_codes import services
from access_codes.models import LockboxPIN, PINAuditLog, PINDelivery
from accounts import services as account_services

from .utils import add_available_pin, make_admin, make_pending_user, pin_key_settings


@pin_key_settings
class DeliveryTests(TestCase):
    def _verified_user_with_delivery(self):
        add_available_pin("123456")
        user = make_pending_user()
        token = account_services.make_verification_token(user)
        with self.captureOnCommitCallbacks(execute=True):
            account_services.verify_email_token(token)
        user.refresh_from_db()
        return user, PINDelivery.objects.get(user=user)

    def test_successful_delivery_after_verification(self):
        user, delivery = self._verified_user_with_delivery()
        self.assertEqual(delivery.status, PINDelivery.Status.SENT)
        self.assertEqual(delivery.attempts, 1)
        self.assertIsNotNone(delivery.sent_at)
        self.assertEqual(len(mail.outbox), 1)  # only the PIN email; the
        # verification email is sent at registration, not in this flow.
        pin_mail = mail.outbox[0]
        self.assertEqual(pin_mail.to, [user.email])
        self.assertIn("123456", pin_mail.body)
        self.assertIn("personal", pin_mail.body)
        self.assertTrue(
            PINAuditLog.objects.filter(
                action=PINAuditLog.Action.DELIVERY_SENT, target_user=user
            ).exists()
        )

    def test_email_failure_keeps_assignment_and_records_error(self):
        add_available_pin("123456")
        user = make_pending_user()
        token = account_services.make_verification_token(user)
        with mock.patch(
            "access_codes.services.send_mail", side_effect=OSError("smtp down")
        ), self.captureOnCommitCallbacks(execute=True):
            account_services.verify_email_token(token)

        delivery = PINDelivery.objects.get(user=user)
        self.assertEqual(delivery.status, PINDelivery.Status.FAILED)
        self.assertEqual(delivery.attempts, 1)
        self.assertIn("OSError", delivery.last_error)
        self.assertNotIn("123456", delivery.last_error)
        # The assignment survives the failure.
        self.assertIsNotNone(services.user_assigned_pin(user))

    def test_retry_after_failure_sends_without_reallocating(self):
        admin = make_admin()
        add_available_pin("123456")
        user = make_pending_user()
        token = account_services.make_verification_token(user)
        with mock.patch(
            "access_codes.services.send_mail", side_effect=OSError("smtp down")
        ), self.captureOnCommitCallbacks(execute=True):
            account_services.verify_email_token(token)

        mail.outbox.clear()
        sent, failed = services.retry_deliveries(PINDelivery.objects.all(), actor=admin)
        self.assertEqual((sent, failed), (1, 0))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("123456", mail.outbox[0].body)
        self.assertEqual(LockboxPIN.objects.filter(assigned_user=user).count(), 1)
        self.assertTrue(
            PINAuditLog.objects.filter(
                action=PINAuditLog.Action.DELIVERY_RETRY, actor=admin
            ).exists()
        )

    def test_sent_delivery_is_never_resent(self):
        user, delivery = self._verified_user_with_delivery()
        mail.outbox.clear()
        result = services.deliver_pin_email(delivery.pk)
        self.assertEqual(result.status, PINDelivery.Status.SENT)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_no_delivery_to_deactivated_user(self):
        admin = make_admin()
        add_available_pin("123456")
        user = make_pending_user()
        token = account_services.make_verification_token(user)
        # Verify but DON'T run on_commit callbacks yet (simulates the window
        # between allocation and email sending).
        with self.captureOnCommitCallbacks() as callbacks:
            account_services.verify_email_token(token)
        user.refresh_from_db()
        account_services.deactivate_user(user, actor=admin)

        mail.outbox.clear()
        for callback in callbacks:
            callback()

        delivery = PINDelivery.objects.get(user=user)
        self.assertEqual(delivery.status, PINDelivery.Status.CANCELLED)
        self.assertEqual(len(mail.outbox), 0)

    def test_no_delivery_to_unverified_user(self):
        pin = add_available_pin("123456")
        user = make_pending_user()
        # Construct an inconsistent state directly: delivery for an unverified user.
        LockboxPIN.objects.filter(pk=pin.pk).update(
            status=LockboxPIN.Status.ASSIGNED, assigned_user=user, assigned_at=timezone.now()
        )
        delivery = PINDelivery.objects.create(pin=pin, user=user)
        result = services.deliver_pin_email(delivery.pk)
        self.assertEqual(result.status, PINDelivery.Status.CANCELLED)
        self.assertEqual(len(mail.outbox), 0)

    def test_delivery_record_persists_after_failure(self):
        add_available_pin("123456")
        user = make_pending_user()
        token = account_services.make_verification_token(user)
        with mock.patch(
            "access_codes.services.send_mail", side_effect=OSError("smtp down")
        ), self.captureOnCommitCallbacks(execute=True):
            account_services.verify_email_token(token)
        # The persistent outbox row is what an admin retries later.
        self.assertTrue(
            PINDelivery.objects.filter(
                user=user, status=PINDelivery.Status.FAILED
            ).exists()
        )

    def test_admin_retry_action(self):
        admin = make_admin()
        add_available_pin("123456")
        user = make_pending_user()
        token = account_services.make_verification_token(user)
        with mock.patch(
            "access_codes.services.send_mail", side_effect=OSError("smtp down")
        ), self.captureOnCommitCallbacks(execute=True):
            account_services.verify_email_token(token)
        delivery = PINDelivery.objects.get(user=user)

        self.client.force_login(admin)
        mail.outbox.clear()
        response = self.client.post(
            reverse("admin:access_codes_pindelivery_changelist"),
            {"action": "retry_action", "_selected_action": [str(delivery.pk)]},
            follow=True,
        )
        self.assertContains(response, "Sent 1 PIN email(s).")
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, PINDelivery.Status.SENT)
        self.assertEqual(delivery.attempts, 2)
