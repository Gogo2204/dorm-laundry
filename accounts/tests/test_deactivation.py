from unittest import mock

from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from access_codes.models import LockboxPIN, PINDelivery
from access_codes.tests.utils import add_available_pin, pin_key_settings
from accounts import services
from accounts.models import User
from laundry import services as laundry_services
from laundry.models import MachineSet, Reservation
from laundry.tests.utils import FRI, MON, SAT, WED, freeze_now, make_admin, make_resident


@pin_key_settings
class DeactivationReservationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.set1 = MachineSet.objects.get(name="Set 1")
        cls.admin = make_admin()
        cls.ana = make_resident("ana@example.com")

    def _book(self, booking_date, slot_number):
        with freeze_now():
            return laundry_services.book_slot(
                user=self.ana,
                machine_set=self.set1,
                booking_date=booking_date,
                slot_number=slot_number,
            )

    def test_future_reservations_are_cancelled_with_quota_release(self):
        first = self._book(FRI, 1)
        second = self._book(SAT, 3)
        with freeze_now():
            services.deactivate_user(self.ana, actor=self.admin)
        for reservation in (first, second):
            reservation.refresh_from_db()
            self.assertEqual(reservation.status, Reservation.Status.CANCELLED)
            self.assertFalse(reservation.counts_toward_quota)
            self.assertIsNotNone(reservation.cancelled_at)
            self.assertEqual(reservation.cancelled_by, self.admin)

    def test_started_reservation_is_not_cancelled(self):
        # Wednesday slot 1 started at 06:00; frozen now is Wednesday 12:00.
        started = Reservation.objects.create(
            user=self.ana, machine_set=self.set1, booking_date=WED, slot_number=1
        )
        future_today = self._book(WED, 4)  # 18:00, still in the future
        with freeze_now():
            services.deactivate_user(self.ana, actor=self.admin)
        started.refresh_from_db()
        future_today.refresh_from_db()
        self.assertEqual(started.status, Reservation.Status.ACTIVE)
        self.assertTrue(started.counts_toward_quota)
        self.assertEqual(future_today.status, Reservation.Status.CANCELLED)
        self.assertFalse(future_today.counts_toward_quota)

    def test_historical_records_are_preserved(self):
        past = Reservation.objects.create(
            user=self.ana, machine_set=self.set1, booking_date=MON, slot_number=2
        )
        old_cancelled = Reservation.objects.create(
            user=self.ana,
            machine_set=self.set1,
            booking_date=MON,
            slot_number=4,
            status=Reservation.Status.CANCELLED,
            counts_toward_quota=True,
        )
        with freeze_now():
            services.deactivate_user(self.ana, actor=self.admin)
        past.refresh_from_db()
        old_cancelled.refresh_from_db()
        self.assertEqual(past.status, Reservation.Status.ACTIVE)  # already started
        self.assertEqual(old_cancelled.status, Reservation.Status.CANCELLED)
        self.assertTrue(old_cancelled.counts_toward_quota)  # unchanged
        self.assertEqual(Reservation.objects.count(), 2)

    def test_deactivation_without_reservations_or_pin(self):
        with freeze_now():
            services.deactivate_user(self.ana, actor=self.admin)
        self.ana.refresh_from_db()
        self.assertFalse(self.ana.is_active)
        self.assertEqual(self.ana.deactivated_by, self.admin)

    def test_full_deactivation_is_atomic_with_pin_state(self):
        pin = add_available_pin("111111")
        LockboxPIN.objects.filter(pk=pin.pk).update(
            status=LockboxPIN.Status.ASSIGNED, assigned_user=self.ana
        )
        delivery = PINDelivery.objects.create(pin=pin, user=self.ana)
        reservation = self._book(FRI, 1)
        with freeze_now():
            services.deactivate_user(self.ana, actor=self.admin)
        pin.refresh_from_db()
        delivery.refresh_from_db()
        reservation.refresh_from_db()
        self.assertEqual(pin.status, LockboxPIN.Status.REVOKE_PENDING)
        self.assertEqual(delivery.status, PINDelivery.Status.CANCELLED)
        self.assertEqual(reservation.status, Reservation.Status.CANCELLED)

    def test_repeated_deactivation_is_idempotent(self):
        reservation = self._book(FRI, 1)
        with freeze_now():
            services.deactivate_user(self.ana, actor=self.admin)
        self.ana.refresh_from_db()
        first_stamp = self.ana.deactivated_at
        other_admin = make_admin("admin2@example.com")
        with freeze_now():
            services.deactivate_user(self.ana, actor=other_admin)
        self.ana.refresh_from_db()
        reservation.refresh_from_db()
        # Original timestamp and responsible admin are preserved.
        self.assertEqual(self.ana.deactivated_at, first_stamp)
        self.assertEqual(self.ana.deactivated_by, self.admin)
        self.assertEqual(reservation.cancelled_by, self.admin)

    def test_failure_rolls_back_the_whole_operation(self):
        pin = add_available_pin("111111")
        LockboxPIN.objects.filter(pk=pin.pk).update(
            status=LockboxPIN.Status.ASSIGNED, assigned_user=self.ana
        )
        reservation = self._book(FRI, 1)
        with freeze_now(), mock.patch(
            "laundry.services.cancel_future_reservations_for_user",
            side_effect=RuntimeError("boom"),
        ), self.assertRaises(RuntimeError):
            services.deactivate_user(self.ana, actor=self.admin)
        self.ana.refresh_from_db()
        pin.refresh_from_db()
        reservation.refresh_from_db()
        # Nothing was partially applied.
        self.assertTrue(self.ana.is_active)
        self.assertIsNone(self.ana.deactivated_at)
        self.assertEqual(pin.status, LockboxPIN.Status.ASSIGNED)
        self.assertEqual(reservation.status, Reservation.Status.ACTIVE)

    def test_last_admin_protection_still_applies(self):
        lone_admin = User.objects.get(pk=self.admin.pk)
        with self.assertRaises(ValidationError):
            services.deactivate_user(lone_admin, actor=lone_admin)


@pin_key_settings
class DeactivationAdminPathTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.set1 = MachineSet.objects.get(name="Set 1")
        cls.admin = make_admin()
        cls.ana = make_resident("ana@example.com")

    def test_bulk_action_cancels_future_reservations(self):
        with freeze_now():
            reservation = laundry_services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=1
            )
        self.client.force_login(self.admin)
        with freeze_now():
            self.client.post(
                reverse("admin:accounts_user_changelist"),
                {"action": "deactivate_users", "_selected_action": [str(self.ana.pk)]},
                follow=True,
            )
        reservation.refresh_from_db()
        self.ana.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.CANCELLED)
        self.assertEqual(reservation.cancelled_by, self.admin)
        self.assertEqual(self.ana.deactivated_by, self.admin)

    def test_change_form_path_cancels_future_reservations(self):
        from django.contrib.admin.sites import site

        with freeze_now():
            reservation = laundry_services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=1
            )
        request = RequestFactory().post("/admin/accounts/user/")
        request.user = self.admin
        user_admin = site._registry[User]
        self.ana.is_active = False
        with freeze_now():
            user_admin.save_model(request, self.ana, form=None, change=True)
        reservation.refresh_from_db()
        self.ana.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.CANCELLED)
        self.assertEqual(self.ana.deactivated_by, self.admin)
        self.assertIsNotNone(self.ana.deactivated_at)
