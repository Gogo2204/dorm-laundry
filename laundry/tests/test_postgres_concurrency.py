"""True concurrency tests — PostgreSQL only (Phase 6A, §10).

These use real threads with separate database connections and therefore need
a database with row locking; on SQLite they are skipped automatically. Run
them against a disposable PostgreSQL database:

    DB_ENGINE=postgresql POSTGRES_HOST=127.0.0.1 POSTGRES_USER=postgres \
    POSTGRES_PASSWORD=postgres DJANGO_DEBUG=true \
    python manage.py test laundry.tests.test_postgres_concurrency

CI runs them in the `tests-postgresql` job against a postgres service
container. They only ever touch the disposable test database.
"""

import threading
import unittest

from django.db import connection, connections
from django.test import TransactionTestCase

from access_codes import services as pin_services
from access_codes.models import LockboxPIN
from access_codes.tests.utils import add_available_pin, pin_key_settings
from accounts import services as account_services
from laundry import services
from laundry.models import MachineSet, Reservation

from .utils import FRI, SAT, THU, freeze_now, make_admin, make_resident

postgresql_only = unittest.skipUnless(
    connection.vendor == "postgresql",
    "True concurrency requires PostgreSQL row locking (skipped on SQLite).",
)


def run_in_threads(*callables):
    """Run callables concurrently; return their results or exceptions.

    A barrier maximises overlap; every thread closes its own DB connections
    so the test database can be torn down cleanly.
    """
    barrier = threading.Barrier(len(callables))
    results = [None] * len(callables)

    def runner(index, fn):
        try:
            barrier.wait(timeout=10)
            results[index] = ("ok", fn())
        except Exception as exc:
            results[index] = ("error", exc)
        finally:
            connections.close_all()

    threads = [
        threading.Thread(target=runner, args=(i, fn)) for i, fn in enumerate(callables)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


@postgresql_only
class ReservationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.set1, _ = MachineSet.objects.get_or_create(name="Комплект 1")
        self.ana = make_resident("ana@example.com")
        self.boris = make_resident("boris@example.com", "Boris", "Iliev")

    def test_same_machine_slot_booked_by_exactly_one_of_two_residents(self):
        def book(user):
            def _run():
                return services.book_slot(
                    user=user,
                    machine_set=self.set1,
                    booking_date=FRI,
                    slot_number=1,
                )

            return _run

        # freeze_now patches a module global, which all threads see; entering
        # the patch inside each thread would race the patch/unpatch bookkeeping.
        with freeze_now():
            results = run_in_threads(book(self.ana), book(self.boris))
        statuses = sorted(status for status, _ in results)
        self.assertEqual(statuses, ["error", "ok"])
        errors = [value for status, value in results if status == "error"]
        self.assertIsInstance(errors[0], services.BookingError)
        self.assertEqual(
            Reservation.objects.filter(
                machine_set=self.set1,
                booking_date=FRI,
                slot_number=1,
                status=Reservation.Status.ACTIVE,
            ).count(),
            1,
        )

    def test_concurrent_bookings_cannot_exceed_weekly_quota(self):
        with freeze_now():
            services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=THU, slot_number=1
            )

        def book(slot_number, booking_date):
            def _run():
                return services.book_slot(
                    user=self.ana,
                    machine_set=self.set1,
                    booking_date=booking_date,
                    slot_number=slot_number,
                )

            return _run

        # Two concurrent attempts for the one remaining quota place.
        with freeze_now():
            results = run_in_threads(book(1, FRI), book(1, SAT))
        successes = [value for status, value in results if status == "ok"]
        self.assertLessEqual(len(successes), 1)
        self.assertLessEqual(services.quota_used(self.ana, THU), 2)

    def test_booking_racing_deactivation_leaves_no_active_future_reservation(self):
        admin = make_admin()

        def book():
            return services.book_slot(
                user=self.ana,
                machine_set=self.set1,
                booking_date=FRI,
                slot_number=1,
            )

        def deactivate():
            account_services.deactivate_user(self.ana, actor=admin)
            return True

        with freeze_now():
            run_in_threads(book, deactivate)
        self.ana.refresh_from_db()
        self.assertFalse(self.ana.is_active)
        # Whichever side won the race, a deactivated user must not end up
        # holding an active future reservation.
        self.assertEqual(
            Reservation.objects.filter(
                user=self.ana, status=Reservation.Status.ACTIVE
            ).count(),
            0,
        )


@postgresql_only
@pin_key_settings
class PINConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.admin = make_admin()

    def test_pin_allocation_racing_deactivation_never_strands_an_assigned_pin(self):
        user = make_resident("ana@example.com")
        add_available_pin("910001")

        def allocate():
            return pin_services.allocate_pin_for_user(user, actor=self.admin)

        def deactivate():
            account_services.deactivate_user(user, actor=self.admin)
            return True

        run_in_threads(allocate, deactivate)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        # The user-row lock serialises both sides: either allocation lost and
        # returned None, or it won and deactivation flagged the PIN. Either
        # way no PIN may remain ASSIGNED to a deactivated user.
        self.assertFalse(
            LockboxPIN.objects.filter(
                assigned_user=user, status=LockboxPIN.Status.ASSIGNED
            ).exists()
        )

    def test_last_available_pin_goes_to_exactly_one_of_two_residents(self):
        user1 = make_resident("one@example.com")
        user2 = make_resident("two@example.com", "Boris", "Iliev")
        add_available_pin("910002")

        results = run_in_threads(
            lambda: pin_services.allocate_pin_for_user(user1, actor=self.admin),
            lambda: pin_services.allocate_pin_for_user(user2, actor=self.admin),
        )
        pins = [value for status, value in results if status == "ok" and value]
        self.assertEqual(len(pins), 1)
        self.assertEqual(
            LockboxPIN.objects.filter(status=LockboxPIN.Status.ASSIGNED).count(), 1
        )
