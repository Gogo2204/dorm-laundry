from django.test import TestCase

from laundry import services
from laundry.models import MachineSet, Reservation

from .utils import FRI, MON, WED, freeze_now, make_admin, make_resident


class CancellationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.set1 = MachineSet.objects.get(name="Set 1")
        cls.ana = make_resident("ana@example.com")
        cls.boris = make_resident("boris@example.com", "Boris", "Iliev")
        cls.admin = make_admin()

    def _future_reservation(self):
        with freeze_now():
            return services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=1
            )

    def test_cancel_before_start_releases_quota_flag(self):
        reservation = self._future_reservation()
        with freeze_now():
            cancelled = services.cancel_reservation(
                reservation=reservation, cancelled_by=self.ana
            )
        self.assertEqual(cancelled.status, Reservation.Status.CANCELLED)
        self.assertFalse(cancelled.counts_toward_quota)
        self.assertIsNotNone(cancelled.cancelled_at)
        self.assertEqual(cancelled.cancelled_by, self.ana)

    def test_cancel_after_start_keeps_quota_flag(self):
        # Wednesday slot 1 started 06:00; frozen now is Wednesday 12:00.
        started = Reservation.objects.create(
            user=self.ana, machine_set=self.set1, booking_date=WED, slot_number=1
        )
        with freeze_now():
            cancelled = services.cancel_reservation(
                reservation=started, cancelled_by=self.ana
            )
        self.assertEqual(cancelled.status, Reservation.Status.CANCELLED)
        self.assertTrue(cancelled.counts_toward_quota)

    def test_cancel_long_after_slot_ended_is_allowed(self):
        old = Reservation.objects.create(
            user=self.ana, machine_set=self.set1, booking_date=MON, slot_number=1
        )
        with freeze_now():
            cancelled = services.cancel_reservation(reservation=old, cancelled_by=self.ana)
        self.assertEqual(cancelled.status, Reservation.Status.CANCELLED)
        self.assertTrue(cancelled.counts_toward_quota)

    def test_cancelled_reservation_remains_in_database(self):
        reservation = self._future_reservation()
        with freeze_now():
            services.cancel_reservation(reservation=reservation, cancelled_by=self.ana)
        self.assertTrue(Reservation.objects.filter(pk=reservation.pk).exists())

    def test_double_cancel_rejected(self):
        reservation = self._future_reservation()
        with freeze_now():
            services.cancel_reservation(reservation=reservation, cancelled_by=self.ana)
            with self.assertRaisesMessage(services.BookingError, "already cancelled"):
                services.cancel_reservation(reservation=reservation, cancelled_by=self.ana)

    def test_resident_cannot_cancel_someone_elses_reservation(self):
        reservation = self._future_reservation()
        with freeze_now(), self.assertRaisesMessage(services.BookingError, "own"):
            services.cancel_reservation(reservation=reservation, cancelled_by=self.boris)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.ACTIVE)

    def test_admin_can_cancel_any_reservation(self):
        reservation = self._future_reservation()
        with freeze_now():
            cancelled = services.cancel_reservation(
                reservation=reservation, cancelled_by=self.admin
            )
        self.assertEqual(cancelled.status, Reservation.Status.CANCELLED)
        self.assertEqual(cancelled.cancelled_by, self.admin)

    def test_deactivated_admin_cannot_cancel_others(self):
        reservation = self._future_reservation()
        other_admin = make_admin("admin2@example.com")
        other_admin.is_active = False
        other_admin.save()
        with freeze_now(), self.assertRaises(services.BookingError):
            services.cancel_reservation(reservation=reservation, cancelled_by=other_admin)
