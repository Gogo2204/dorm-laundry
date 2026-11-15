from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from access_codes.models import LockboxPIN
from access_codes.tests.utils import add_available_pin, pin_key_settings
from accounts import services
from accounts.models import ApprovedResident, User
from laundry import services as laundry_services
from laundry.models import MachineSet, Reservation
from laundry.tests.utils import FRI, PASSWORD, freeze_now, make_admin, make_resident

REGISTER_DATA = {
    "first_name": "Elena",
    "last_name": "Demirova",
    "email": "elena2@example.com",
    "password1": "fresh-towels-2026",
    "password2": "fresh-towels-2026",
}


@pin_key_settings
class EligibilityWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.set1 = MachineSet.objects.get(name="Set 1")
        cls.admin = make_admin()
        cls.ana = make_resident("ana@example.com", "Elena", "Demirova")
        cls.entry = ApprovedResident.objects.create(
            first_name="Elena", last_name="Demirova", registered_user=cls.ana
        )

    def _assign_pin(self):
        pin = add_available_pin("111111")
        LockboxPIN.objects.filter(pk=pin.pk).update(
            status=LockboxPIN.Status.ASSIGNED, assigned_user=self.ana
        )
        return pin

    def test_revoking_eligibility_runs_full_deactivation(self):
        pin = self._assign_pin()
        with freeze_now():
            reservation = laundry_services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=1
            )
            services.revoke_eligibility(self.entry, actor=self.admin)
        self.entry.refresh_from_db()
        self.ana.refresh_from_db()
        pin.refresh_from_db()
        reservation.refresh_from_db()
        self.assertFalse(self.entry.is_eligible)
        self.assertFalse(self.ana.is_active)
        self.assertEqual(self.ana.deactivated_by, self.admin)
        self.assertEqual(pin.status, LockboxPIN.Status.REVOKE_PENDING)
        self.assertEqual(reservation.status, Reservation.Status.CANCELLED)
        # Nothing deleted.
        self.assertTrue(User.objects.filter(pk=self.ana.pk).exists())
        self.assertTrue(Reservation.objects.filter(pk=reservation.pk).exists())

    def test_revoking_eligibility_for_unclaimed_entry_just_toggles(self):
        unclaimed = ApprovedResident.objects.create(first_name="Mira", last_name="Koleva")
        services.revoke_eligibility(unclaimed, actor=self.admin)
        unclaimed.refresh_from_db()
        self.assertFalse(unclaimed.is_eligible)

    def test_ineligible_resident_cannot_register(self):
        entry = ApprovedResident.objects.create(first_name="Boris", last_name="Iliev")
        services.revoke_eligibility(entry, actor=self.admin)
        response = self.client.post(
            reverse("register"),
            {**REGISTER_DATA, "first_name": "Boris", "last_name": "Iliev",
             "email": "boris@example.com"},
        )
        self.assertContains(response, "approved residents")
        self.assertFalse(User.objects.filter(email="boris@example.com").exists())

    def test_claimed_entry_blocks_reregistration_while_ineligible_and_after_restore(self):
        services.revoke_eligibility(self.entry, actor=self.admin)
        response = self.client.post(reverse("register"), REGISTER_DATA)
        self.assertContains(response, "approved residents")

        services.restore_eligibility(self.entry)
        # The entry is still claimed by the original account, so no duplicate
        # account can be created for the same name.
        response = self.client.post(reverse("register"), REGISTER_DATA)
        self.assertContains(response, "approved residents")
        self.assertEqual(User.objects.filter(last_name="Demirova").count(), 1)

    def test_restore_eligibility_does_not_reactivate_account(self):
        services.revoke_eligibility(self.entry, actor=self.admin)
        services.restore_eligibility(self.entry)
        self.entry.refresh_from_db()
        self.ana.refresh_from_db()
        self.assertTrue(self.entry.is_eligible)
        self.assertFalse(self.ana.is_active)

    def test_revoking_eligibility_of_last_admin_is_blocked(self):
        entry = ApprovedResident.objects.create(
            first_name="Admin", last_name="User", registered_user=self.admin
        )
        with self.assertRaises(ValidationError):
            services.revoke_eligibility(entry, actor=self.admin)
        entry.refresh_from_db()
        self.admin.refresh_from_db()
        self.assertTrue(entry.is_eligible)
        self.assertTrue(self.admin.is_active)


@pin_key_settings
class EligibilityAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.set1 = MachineSet.objects.get(name="Set 1")
        cls.admin = make_admin()
        cls.ana = make_resident("ana@example.com", "Elena", "Demirova")
        cls.entry = ApprovedResident.objects.create(
            first_name="Elena", last_name="Demirova", registered_user=cls.ana
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_change_form_unchecking_eligibility_triggers_workflow(self):
        with freeze_now():
            reservation = laundry_services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=1
            )
        with freeze_now():
            response = self.client.post(
                reverse("admin:accounts_approvedresident_change", args=[self.entry.pk]),
                {
                    "first_name": "Elena",
                    "last_name": "Demirova",
                    "registered_user": str(self.ana.pk),
                    # is_eligible omitted -> unchecked
                },
                follow=True,
            )
        self.assertContains(response, "no longer eligible")
        self.entry.refresh_from_db()
        self.ana.refresh_from_db()
        reservation.refresh_from_db()
        self.assertFalse(self.entry.is_eligible)
        self.assertFalse(self.ana.is_active)
        self.assertEqual(reservation.status, Reservation.Status.CANCELLED)

    def test_bulk_mark_ineligible(self):
        response = self.client.post(
            reverse("admin:accounts_approvedresident_changelist"),
            {"action": "mark_ineligible", "_selected_action": [str(self.entry.pk)]},
            follow=True,
        )
        self.assertContains(response, "Marked 1 resident(s) ineligible")
        self.ana.refresh_from_db()
        self.assertFalse(self.ana.is_active)

    def test_bulk_mark_eligible_does_not_reactivate(self):
        services.revoke_eligibility(self.entry, actor=self.admin)
        response = self.client.post(
            reverse("admin:accounts_approvedresident_changelist"),
            {"action": "mark_eligible", "_selected_action": [str(self.entry.pk)]},
            follow=True,
        )
        self.assertContains(response, "remain")
        self.assertContains(response, "deactivated until explicitly reactivated")
        self.entry.refresh_from_db()
        self.ana.refresh_from_db()
        self.assertTrue(self.entry.is_eligible)
        self.assertFalse(self.ana.is_active)

    def test_change_form_blocks_revoking_last_admin_eligibility(self):
        entry = ApprovedResident.objects.create(
            first_name="Admin", last_name="User", registered_user=self.admin
        )
        response = self.client.post(
            reverse("admin:accounts_approvedresident_change", args=[entry.pk]),
            {
                "first_name": "Admin",
                "last_name": "User",
                "registered_user": str(self.admin.pk),
            },
        )
        self.assertEqual(response.status_code, 200)  # form redisplayed with error
        self.assertContains(response, "last active administrator")
        entry.refresh_from_db()
        self.assertTrue(entry.is_eligible)

    def test_claimed_filter(self):
        ApprovedResident.objects.create(first_name="Mira", last_name="Koleva")
        url = reverse("admin:accounts_approvedresident_changelist")
        registered = self.client.get(url, {"claimed": "yes"})
        self.assertContains(registered, "Demirova")
        self.assertNotContains(registered, "Koleva")
        unregistered = self.client.get(url, {"claimed": "no"})
        self.assertContains(unregistered, "Koleva")
        self.assertNotContains(unregistered, "Demirova")


@pin_key_settings
class ReactivationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.set1 = MachineSet.objects.get(name="Set 1")
        cls.admin = make_admin()
        cls.ana = make_resident("ana@example.com", "Elena", "Demirova")
        cls.entry = ApprovedResident.objects.create(
            first_name="Elena", last_name="Demirova", registered_user=cls.ana
        )

    def test_reactivation_restores_access_for_verified_user(self):
        services.deactivate_user(self.ana, actor=self.admin)
        services.reactivate_user(self.ana)
        self.ana.refresh_from_db()
        self.assertTrue(self.ana.is_active)
        self.assertIsNone(self.ana.deactivated_at)
        self.assertIsNone(self.ana.deactivated_by)
        self.assertTrue(
            self.client.login(username="ana@example.com", password=PASSWORD)
        )

    def test_reactivation_does_not_bypass_verification(self):
        self.ana.email_verified = False
        self.ana.save()
        services.deactivate_user(self.ana, actor=self.admin)
        services.reactivate_user(self.ana)
        self.ana.refresh_from_db()
        self.assertFalse(self.ana.is_active)  # must verify email first
        self.assertIsNone(self.ana.deactivated_at)

    def test_reactivation_blocked_while_ineligible(self):
        services.revoke_eligibility(self.entry, actor=self.admin)
        with self.assertRaises(ValidationError):
            services.reactivate_user(self.ana)
        self.ana.refresh_from_db()
        self.assertFalse(self.ana.is_active)

    def test_reactivation_does_not_restore_reservations_or_pin(self):
        pin = add_available_pin("111111")
        LockboxPIN.objects.filter(pk=pin.pk).update(
            status=LockboxPIN.Status.ASSIGNED, assigned_user=self.ana
        )
        with freeze_now():
            reservation = laundry_services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=1
            )
            services.deactivate_user(self.ana, actor=self.admin)
        services.reactivate_user(self.ana)
        reservation.refresh_from_db()
        pin.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.CANCELLED)
        self.assertEqual(pin.status, LockboxPIN.Status.REVOKE_PENDING)
        from access_codes.services import user_assigned_pin

        self.assertIsNone(user_assigned_pin(self.ana))

    def test_admin_reactivate_action(self):
        services.deactivate_user(self.ana, actor=self.admin)
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("admin:accounts_user_changelist"),
            {"action": "reactivate_users", "_selected_action": [str(self.ana.pk)]},
            follow=True,
        )
        self.assertContains(response, "Reactivated 1 user(s)")
        self.ana.refresh_from_db()
        self.assertTrue(self.ana.is_active)

    def test_admin_reactivate_action_blocks_ineligible(self):
        services.revoke_eligibility(self.entry, actor=self.admin)
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("admin:accounts_user_changelist"),
            {"action": "reactivate_users", "_selected_action": [str(self.ana.pk)]},
            follow=True,
        )
        self.assertContains(response, "ineligible")
        self.ana.refresh_from_db()
        self.assertFalse(self.ana.is_active)

    def test_change_form_reactivation_of_ineligible_is_rejected(self):
        from accounts.admin import SafeguardedUserChangeForm

        services.revoke_eligibility(self.entry, actor=self.admin)
        self.ana.refresh_from_db()
        form = SafeguardedUserChangeForm(
            instance=self.ana,
            data={
                "email": self.ana.email,
                "first_name": self.ana.first_name,
                "last_name": self.ana.last_name,
                "is_active": "on",
                "email_verified": "on",
                "date_joined_0": self.ana.date_joined.strftime("%Y-%m-%d"),
                "date_joined_1": self.ana.date_joined.strftime("%H:%M:%S"),
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("ineligible", str(form.errors))
