
from django.test import TestCase
from django.utils import timezone

from laundry import services
from laundry.models import MachineSet, Reservation

from .utils import (
    AFTER_HORIZON,
    FRI,
    MON,
    NEXT_MON,
    NEXT_SUN,
    SAT,
    THU,
    WED,
    freeze_now,
    make_resident,
)


class BookingRuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.set1, cls.set2, cls.set3 = MachineSet.objects.order_by("name")
        cls.ana = make_resident("ana@example.com")

    def book(self, user=None, machine_set=None, booking_date=FRI, slot_number=1):
        return services.book_slot(
            user=user or self.ana,
            machine_set=machine_set or self.set1,
            booking_date=booking_date,
            slot_number=slot_number,
        )

    def test_successful_booking(self):
        with freeze_now():
            reservation = self.book()
        self.assertEqual(reservation.status, Reservation.Status.ACTIVE)
        self.assertTrue(reservation.counts_toward_quota)
        self.assertEqual(reservation.booking_date, FRI)

    def test_all_five_slots_are_bookable(self):
        users = [make_resident(f"user{i}@example.com") for i in range(1, 6)]
        with freeze_now():
            for slot, user in enumerate(users, start=1):
                with self.subTest(slot=slot):
                    self.book(user=user, slot_number=slot)
        self.assertEqual(Reservation.objects.count(), 5)

    def test_weekly_limit_of_two(self):
        with freeze_now():
            self.book(booking_date=THU, slot_number=1)
            self.book(booking_date=SAT, slot_number=1)
            with self.assertRaisesMessage(services.BookingError, "лимита"):
                self.book(booking_date=FRI, slot_number=3)

    def test_weekly_limit_is_per_week(self):
        with freeze_now():
            self.book(booking_date=THU, slot_number=1)
            self.book(booking_date=SAT, slot_number=1)
            # Next week is a fresh quota.
            self.book(booking_date=NEXT_MON, slot_number=1)
            self.book(booking_date=NEXT_SUN, slot_number=1)
        self.assertEqual(Reservation.objects.filter(user=self.ana).count(), 4)

    def test_consecutive_slots_same_day_rejected(self):
        with freeze_now():
            self.book(booking_date=FRI, slot_number=1)
            with self.assertRaisesMessage(services.BookingError, "последователни"):
                self.book(booking_date=FRI, slot_number=2, machine_set=self.set2)

    def test_slots_4_and_5_are_consecutive(self):
        with freeze_now():
            self.book(booking_date=FRI, slot_number=4)
            with self.assertRaisesMessage(services.BookingError, "последователни"):
                self.book(booking_date=FRI, slot_number=5)

    def test_nonconsecutive_slots_same_day_allowed(self):
        with freeze_now():
            self.book(booking_date=FRI, slot_number=1)
            self.book(booking_date=FRI, slot_number=3, machine_set=self.set2)
        self.assertEqual(Reservation.objects.filter(user=self.ana).count(), 2)

    def test_adjacent_slot_numbers_on_different_days_allowed(self):
        # Thursday slot 5 crosses midnight into Friday, but Friday slot 1 is
        # a different booking date, so it is allowed (AC-23).
        with freeze_now():
            self.book(booking_date=THU, slot_number=5)
            self.book(booking_date=FRI, slot_number=1)
        self.assertEqual(Reservation.objects.filter(user=self.ana).count(), 2)

    def test_same_slot_on_two_machines_rejected(self):
        with freeze_now():
            self.book(booking_date=FRI, slot_number=1, machine_set=self.set1)
            with self.assertRaisesMessage(services.BookingError, "на друг комплект"):
                self.book(booking_date=FRI, slot_number=1, machine_set=self.set2)

    def test_machine_double_booking_rejected(self):
        boris = make_resident("boris@example.com", "Boris", "Iliev")
        with freeze_now():
            self.book(booking_date=FRI, slot_number=1)
            with self.assertRaisesMessage(services.BookingError, "вече е зает"):
                self.book(user=boris, booking_date=FRI, slot_number=1)

    def test_booking_horizon(self):
        with freeze_now():
            # Both current-week and next-week edges are fine.
            self.book(booking_date=NEXT_SUN, slot_number=1)
            with self.assertRaisesMessage(services.BookingError, "текущата или следващата седмица"):
                self.book(booking_date=AFTER_HORIZON, slot_number=1, machine_set=self.set2)

    def test_past_date_rejected(self):
        with freeze_now(), self.assertRaises(services.BookingError):
            self.book(booking_date=MON, slot_number=1)

    def test_started_slot_today_rejected(self):
        # Frozen now is Wednesday 12:00: slots 1 and 2 have started, slot 3 not.
        with freeze_now():
            with self.assertRaisesMessage(services.BookingError, "вече е започнал"):
                self.book(booking_date=WED, slot_number=2)
            self.book(booking_date=WED, slot_number=3)

    def test_slot_starting_exactly_now_rejected(self):
        at_slot_3_start = services.slot_start(WED, 3)
        with (
            freeze_now(at_slot_3_start),
            self.assertRaisesMessage(services.BookingError, "вече е започнал"),
        ):
            self.book(booking_date=WED, slot_number=3)

    def test_inactive_user_cannot_book(self):
        self.ana.is_active = False
        self.ana.save()
        with freeze_now(), self.assertRaisesMessage(services.BookingError, "няма право"):
            self.book()

    def test_deactivated_user_cannot_book(self):
        self.ana.is_active = False
        self.ana.deactivated_at = timezone.now()
        self.ana.save()
        with freeze_now(), self.assertRaisesMessage(services.BookingError, "няма право"):
            self.book()

    def test_inactive_machine_set_cannot_be_booked(self):
        self.set1.is_active = False
        self.set1.save()
        with freeze_now(), self.assertRaisesMessage(services.BookingError, "не е достъпен"):
            self.book()

    def test_invalid_slot_number_rejected(self):
        with freeze_now():
            for slot in (0, 6):
                with self.subTest(slot=slot), self.assertRaises(services.BookingError):
                    self.book(slot_number=slot)

    def test_exactly_three_machine_sets_are_seeded(self):
        names = list(MachineSet.objects.values_list("name", flat=True))
        self.assertEqual(names, ["Комплект 1", "Комплект 2", "Комплект 3"])


class QuotaAfterCancellationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.set1, cls.set2, cls.set3 = MachineSet.objects.order_by("name")
        cls.ana = make_resident("ana@example.com")

    def test_cancelling_before_start_releases_quota(self):
        with freeze_now():
            kept = services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=THU, slot_number=1
            )
            released = services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=SAT, slot_number=1
            )
            services.cancel_reservation(reservation=released, cancelled_by=self.ana)
            # Quota is 1 again, so a new booking succeeds.
            services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=3
            )
        self.assertEqual(services.quota_used(self.ana, THU), 2)
        kept.refresh_from_db()
        self.assertTrue(kept.counts_toward_quota)

    def test_cancelling_after_start_keeps_quota_used(self):
        with freeze_now():
            # Wednesday slot 1 started at 06:00; create it directly as history.
            started = Reservation.objects.create(
                user=self.ana, machine_set=self.set1, booking_date=WED, slot_number=1
            )
            services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=SAT, slot_number=1
            )
            services.cancel_reservation(reservation=started, cancelled_by=self.ana)
            with self.assertRaisesMessage(services.BookingError, "лимита"):
                services.book_slot(
                    user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=3
                )
        started.refresh_from_db()
        self.assertTrue(started.counts_toward_quota)

    def test_cancelled_slot_frees_machine_for_others(self):
        boris = make_resident("boris@example.com", "Boris", "Iliev")
        with freeze_now():
            mine = services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=1
            )
            services.cancel_reservation(reservation=mine, cancelled_by=self.ana)
            services.book_slot(
                user=boris, machine_set=self.set1, booking_date=FRI, slot_number=1
            )
        self.assertEqual(
            Reservation.objects.filter(status=Reservation.Status.ACTIVE).count(), 1
        )

    def test_cancelled_slot_does_not_block_adjacency(self):
        with freeze_now():
            first = services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=1
            )
            services.cancel_reservation(reservation=first, cancelled_by=self.ana)
            services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=2
            )
        self.assertEqual(
            Reservation.objects.filter(
                user=self.ana, status=Reservation.Status.ACTIVE
            ).count(),
            1,
        )
