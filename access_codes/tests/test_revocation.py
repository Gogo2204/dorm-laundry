from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from access_codes import services
from access_codes.models import LockboxPIN, PINAuditLog, PINDelivery
from accounts import services as account_services

from .utils import add_available_pin, make_admin, make_resident, pin_key_settings


def assign_pin_to(user, value="111111") -> LockboxPIN:
    pin = add_available_pin(value)
    LockboxPIN.objects.filter(pk=pin.pk).update(
        status=LockboxPIN.Status.ASSIGNED, assigned_user=user, assigned_at=timezone.now()
    )
    pin.refresh_from_db()
    return pin


@pin_key_settings
class RevocationTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.user = make_resident("res@example.com", "Elena", "Demirova")

    def test_deactivation_marks_pin_revoke_pending(self):
        pin = assign_pin_to(self.user)
        account_services.deactivate_user(self.user, actor=self.admin)
        pin.refresh_from_db()
        self.assertEqual(pin.status, LockboxPIN.Status.REVOKE_PENDING)
        self.assertIsNotNone(pin.revoke_requested_at)
        self.assertEqual(pin.assigned_user, self.user)  # history preserved
        self.assertTrue(
            PINAuditLog.objects.filter(
                pin=pin,
                action=PINAuditLog.Action.REVOKE_REQUESTED,
                actor=self.admin,
                target_user=self.user,
            ).exists()
        )

    def test_admin_bulk_deactivation_marks_pins(self):
        pin = assign_pin_to(self.user)
        self.client.force_login(self.admin)
        self.client.post(
            reverse("admin:accounts_user_changelist"),
            {"action": "deactivate_users", "_selected_action": [str(self.user.pk)]},
            follow=True,
        )
        pin.refresh_from_db()
        self.assertEqual(pin.status, LockboxPIN.Status.REVOKE_PENDING)

    def test_admin_change_form_deactivation_marks_pins(self):
        """Unchecking is_active in the user change form (UserAdmin.save_model)
        must flag the PIN exactly like services.deactivate_user does."""
        from django.contrib.admin.sites import site
        from django.test import RequestFactory

        from accounts.models import User

        pin = assign_pin_to(self.user)
        request = RequestFactory().post("/admin/accounts/user/")
        request.user = self.admin

        user_admin = site._registry[User]
        self.user.is_active = False
        user_admin.save_model(request, self.user, form=None, change=True)

        pin.refresh_from_db()
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertIsNotNone(self.user.deactivated_at)
        self.assertEqual(pin.status, LockboxPIN.Status.REVOKE_PENDING)

    def test_pending_delivery_is_cancelled_on_deactivation(self):
        pin = assign_pin_to(self.user)
        delivery = PINDelivery.objects.create(pin=pin, user=self.user)
        account_services.deactivate_user(self.user, actor=self.admin)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, PINDelivery.Status.CANCELLED)

    def test_confirm_physical_removal(self):
        pin = assign_pin_to(self.user)
        account_services.deactivate_user(self.user, actor=self.admin)
        confirmed = services.confirm_physical_removal(
            LockboxPIN.objects.filter(pk=pin.pk), actor=self.admin
        )
        self.assertEqual(confirmed, 1)
        pin.refresh_from_db()
        self.assertEqual(pin.status, LockboxPIN.Status.REVOKED)
        self.assertIsNotNone(pin.revoke_confirmed_at)
        self.assertEqual(pin.assigned_user, self.user)  # audit history kept
        self.assertTrue(
            PINAuditLog.objects.filter(
                pin=pin, action=PINAuditLog.Action.REVOKE_CONFIRMED, actor=self.admin
            ).exists()
        )

    def test_confirm_removal_only_applies_to_revoke_pending(self):
        pin = assign_pin_to(self.user)  # still ASSIGNED
        confirmed = services.confirm_physical_removal(
            LockboxPIN.objects.all(), actor=self.admin
        )
        self.assertEqual(confirmed, 0)
        pin.refresh_from_db()
        self.assertEqual(pin.status, LockboxPIN.Status.ASSIGNED)

    def test_admin_removal_action_requires_confirmation(self):
        pin = assign_pin_to(self.user)
        account_services.deactivate_user(self.user, actor=self.admin)
        self.client.force_login(self.admin)
        changelist = reverse("admin:access_codes_lockboxpin_changelist")
        response = self.client.post(
            changelist,
            {"action": "confirm_removal_action", "_selected_action": [str(pin.pk)]},
        )
        self.assertContains(response, "лично сте изтрили")
        pin.refresh_from_db()
        self.assertEqual(pin.status, LockboxPIN.Status.REVOKE_PENDING)
        self.client.post(
            changelist,
            {
                "action": "confirm_removal_action",
                "_selected_action": [str(pin.pk)],
                "transition_confirmed": "1",
            },
        )
        pin.refresh_from_db()
        self.assertEqual(pin.status, LockboxPIN.Status.REVOKED)

    def test_revoke_pending_pin_is_never_reallocated(self):
        assign_pin_to(self.user)
        account_services.deactivate_user(self.user, actor=self.admin)
        newcomer = make_resident("new@example.com")
        self.assertIsNone(services.allocate_pin_for_user(newcomer))

    def test_revoked_pin_is_never_reallocated(self):
        pin = assign_pin_to(self.user)
        account_services.deactivate_user(self.user, actor=self.admin)
        services.confirm_physical_removal(
            LockboxPIN.objects.filter(pk=pin.pk), actor=self.admin
        )
        newcomer = make_resident("new@example.com")
        self.assertIsNone(services.allocate_pin_for_user(newcomer))

    def test_reactivation_does_not_restore_old_pin(self):
        pin = assign_pin_to(self.user)
        account_services.deactivate_user(self.user, actor=self.admin)
        account_services.reactivate_user(self.user)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        # The old PIN stays in the revocation pipeline...
        pin.refresh_from_db()
        self.assertEqual(pin.status, LockboxPIN.Status.REVOKE_PENDING)
        self.assertIsNone(services.user_assigned_pin(self.user))
        # ...and a fresh allocation hands out a NEW pin, not the old one.
        fresh = add_available_pin("999999")
        allocated = services.allocate_pin_for_user(self.user, actor=self.admin)
        self.assertEqual(allocated.pk, fresh.pk)
        self.assertNotEqual(allocated.pk, pin.pk)

    def test_deactivating_user_without_pin_is_fine(self):
        account_services.deactivate_user(self.user, actor=self.admin)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(LockboxPIN.objects.count(), 0)
