import contextlib
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase

from laundry import services
from laundry.models import MachineSet, Reservation

from .utils import FRI, freeze_now, make_resident


class ConstraintTests(TestCase):
    """The database itself must reject conflicting ACTIVE rows (BR-10)."""

    @classmethod
    def setUpTestData(cls):
        cls.set1 = MachineSet.objects.get(name="Set 1")
        cls.set2 = MachineSet.objects.get(name="Set 2")
        cls.ana = make_resident("ana@example.com")
        cls.boris = make_resident("boris@example.com", "Boris", "Iliev")

    def test_duplicate_active_machine_slot_rejected_by_database(self):
        Reservation.objects.create(
            user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=1
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Reservation.objects.create(
                user=self.boris, machine_set=self.set1, booking_date=FRI, slot_number=1
            )

    def test_duplicate_active_user_slot_rejected_by_database(self):
        Reservation.objects.create(
            user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=1
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Reservation.objects.create(
                user=self.ana, machine_set=self.set2, booking_date=FRI, slot_number=1
            )

    def test_cancelled_rows_do_not_block_new_bookings(self):
        Reservation.objects.create(
            user=self.ana,
            machine_set=self.set1,
            booking_date=FRI,
            slot_number=1,
            status=Reservation.Status.CANCELLED,
        )
        Reservation.objects.create(
            user=self.boris, machine_set=self.set1, booking_date=FRI, slot_number=1
        )
        self.assertEqual(Reservation.objects.count(), 2)

    def test_slot_number_bounds_enforced_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Reservation.objects.create(
                user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=6
            )


class RaceSimulationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.set1 = MachineSet.objects.get(name="Set 1")
        cls.ana = make_resident("ana@example.com")

    def test_lost_race_is_translated_to_a_friendly_error(self):
        """If another request wins between the availability check and the
        INSERT, the unique constraint fires and the user gets a clear error."""
        with freeze_now(), mock.patch.object(
            services.Reservation.objects, "create", side_effect=IntegrityError
        ), self.assertRaisesMessage(services.BookingError, "just taken"):
            services.book_slot(
                user=self.ana,
                machine_set=self.set1,
                booking_date=FRI,
                slot_number=1,
            )

    def test_booking_rules_run_inside_a_transaction(self):
        """A failed claim leaves no partial rows behind."""
        with (
            freeze_now(),
            mock.patch.object(
                services.Reservation.objects, "create", side_effect=IntegrityError
            ),
            contextlib.suppress(services.BookingError),
        ):
            services.book_slot(
                user=self.ana,
                machine_set=self.set1,
                booking_date=FRI,
                slot_number=1,
            )
        self.assertEqual(Reservation.objects.count(), 0)
