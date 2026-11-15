from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from access_codes import services
from access_codes.models import LockboxPIN, PINAuditLog, PINDelivery
from accounts import services as account_services
from accounts.services import VerificationResult

from .utils import add_available_pin, make_admin, make_pending_user, pin_key_settings


@pin_key_settings
class AllocationTests(TestCase):
    def test_verification_allocates_a_pin(self):
        pin = add_available_pin("111111")
        user = make_pending_user()
        token = account_services.make_verification_token(user)

        result, _ = account_services.verify_email_token(token)

        self.assertEqual(result, VerificationResult.VERIFIED)
        pin.refresh_from_db()
        self.assertEqual(pin.status, LockboxPIN.Status.ASSIGNED)
        self.assertEqual(pin.assigned_user, user)
        self.assertIsNotNone(pin.assigned_at)
        self.assertTrue(
            PINAuditLog.objects.filter(
                pin=pin, action=PINAuditLog.Action.ASSIGNED, target_user=user
            ).exists()
        )
        self.assertTrue(PINDelivery.objects.filter(pin=pin, user=user).exists())

    def test_repeated_verification_does_not_allocate_twice(self):
        add_available_pin("111111")
        add_available_pin("222222")
        user = make_pending_user()
        token = account_services.make_verification_token(user)

        account_services.verify_email_token(token)
        account_services.verify_email_token(token)
        account_services.verify_email_token(token)

        self.assertEqual(
            LockboxPIN.objects.filter(assigned_user=user).count(), 1
        )
        self.assertEqual(PINDelivery.objects.filter(user=user).count(), 1)

    def test_allocation_is_idempotent(self):
        add_available_pin("111111")
        add_available_pin("222222")
        user = make_pending_user()
        user.is_active = True
        user.email_verified = True
        user.save()

        first = services.allocate_pin_for_user(user)
        second = services.allocate_pin_for_user(user)
        self.assertEqual(first.pk, second.pk)

    def test_empty_inventory_still_verifies(self):
        user = make_pending_user()
        token = account_services.make_verification_token(user)
        result, _ = account_services.verify_email_token(token)
        self.assertEqual(result, VerificationResult.VERIFIED)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIsNone(services.user_assigned_pin(user))

    def test_verification_page_mentions_waiting_pin(self):
        user = make_pending_user()
        token = account_services.make_verification_token(user)
        response = self.client.get(reverse("verify_email", args=[token]))
        self.assertContains(response, "awaiting assignment by an administrator")

    def test_verification_page_mentions_emailed_pin(self):
        add_available_pin("111111")
        user = make_pending_user()
        token = account_services.make_verification_token(user)
        response = self.client.get(reverse("verify_email", args=[token]))
        self.assertContains(response, "has been emailed to you")

    def test_unprogrammed_pins_are_never_allocated(self):
        services.add_pin("111111", actor=None)  # UNPROGRAMMED
        user = make_pending_user()
        user.is_active = True
        user.email_verified = True
        user.save()
        self.assertIsNone(services.allocate_pin_for_user(user))

    def test_ineligible_users_get_nothing(self):
        add_available_pin("111111")
        user = make_pending_user()
        user.is_active = True
        user.email_verified = True
        user.deactivated_at = timezone.now()
        user.save()
        self.assertIsNone(services.allocate_pin_for_user(user))
        self.assertEqual(
            LockboxPIN.objects.get().status, LockboxPIN.Status.AVAILABLE
        )

    def test_last_pin_goes_to_exactly_one_user(self):
        add_available_pin("111111")
        user1 = make_pending_user("one@example.com")
        user2 = make_pending_user("two@example.com")
        for user in (user1, user2):
            user.is_active = True
            user.email_verified = True
            user.save()

        self.assertIsNotNone(services.allocate_pin_for_user(user1))
        self.assertIsNone(services.allocate_pin_for_user(user2))
        self.assertEqual(LockboxPIN.objects.filter(assigned_user=user2).count(), 0)

    def test_lost_claim_race_falls_through_safely(self):
        """If the candidate is claimed between the lock query and the
        conditional UPDATE, the claim affects zero rows and no assignment
        happens for the loser."""
        pin = add_available_pin("111111")
        winner = make_pending_user("winner@example.com")
        loser = make_pending_user("loser@example.com")
        for user in (winner, loser):
            user.is_active = True
            user.email_verified = True
            user.save()

        # Simulate the winner committing first.
        LockboxPIN.objects.filter(pk=pin.pk).update(
            status=LockboxPIN.Status.ASSIGNED,
            assigned_user=winner,
            assigned_at=timezone.now(),
        )
        self.assertIsNone(services.allocate_pin_for_user(loser))
        pin.refresh_from_db()
        self.assertEqual(pin.assigned_user, winner)

    def test_database_rejects_two_assigned_pins_for_one_user(self):
        user = make_pending_user()
        pin1 = add_available_pin("111111")
        pin2 = add_available_pin("222222")
        LockboxPIN.objects.filter(pk=pin1.pk).update(
            status=LockboxPIN.Status.ASSIGNED, assigned_user=user
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            # Bypass the service to prove the constraint is the backstop.
            pin2.status = LockboxPIN.Status.ASSIGNED
            pin2.assigned_user = user
            pin2.save()

    def test_admin_allocate_action(self):
        admin = make_admin()
        add_available_pin("111111")
        user = make_pending_user("late@example.com")
        user.is_active = True
        user.email_verified = True
        user.save()

        self.client.force_login(admin)
        response = self.client.post(
            reverse("admin:accounts_user_changelist"),
            {"action": "allocate_pins", "_selected_action": [str(user.pk)]},
            follow=True,
        )
        self.assertContains(response, "Allocated 1 PIN(s)")
        self.assertIsNotNone(services.user_assigned_pin(user))
        self.assertTrue(
            PINAuditLog.objects.filter(
                action=PINAuditLog.Action.ASSIGNED, actor=admin, target_user=user
            ).exists()
        )

    def test_admin_allocate_action_reports_empty_inventory(self):
        admin = make_admin()
        user = make_pending_user("late@example.com")
        user.is_active = True
        user.email_verified = True
        user.save()
        self.client.force_login(admin)
        response = self.client.post(
            reverse("admin:accounts_user_changelist"),
            {"action": "allocate_pins", "_selected_action": [str(user.pk)]},
            follow=True,
        )
        self.assertContains(response, "no AVAILABLE")

    def test_users_awaiting_pin_query(self):
        waiting = make_pending_user("waiting@example.com")
        waiting.is_active = True
        waiting.email_verified = True
        waiting.save()
        assigned_user = make_pending_user("has@example.com")
        assigned_user.is_active = True
        assigned_user.email_verified = True
        assigned_user.save()
        pin = add_available_pin("111111")
        LockboxPIN.objects.filter(pk=pin.pk).update(
            status=LockboxPIN.Status.ASSIGNED, assigned_user=assigned_user
        )
        unverified = make_pending_user("nope@example.com")

        awaiting = services.users_awaiting_pin()
        self.assertIn(waiting, awaiting)
        self.assertNotIn(assigned_user, awaiting)
        self.assertNotIn(unverified, awaiting)
