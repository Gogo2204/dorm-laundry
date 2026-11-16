import datetime as dt

from django.test import TestCase

from laundry import services

from .utils import MON, SUN, TZ


class SlotTimeTests(TestCase):
    def test_all_five_slots_have_expected_times(self):
        expected = {
            1: (6, 10),
            2: (10, 14),
            3: (14, 18),
            4: (18, 22),
            5: (22, 2),
        }
        day = dt.date(2026, 11, 20)
        for slot, (start_hour, end_hour) in expected.items():
            with self.subTest(slot=slot):
                self.assertEqual(services.slot_start(day, slot).hour, start_hour)
                self.assertEqual(services.slot_end(day, slot).hour, end_hour)

    def test_slot_5_ends_on_the_next_day(self):
        day = dt.date(2026, 11, 20)
        start = services.slot_start(day, 5)
        end = services.slot_end(day, 5)
        self.assertEqual(start.date(), day)
        self.assertEqual(end.date(), day + dt.timedelta(days=1))
        self.assertEqual(end.hour, 2)
        self.assertEqual(end - start, dt.timedelta(hours=4))

    def test_slot_datetimes_are_timezone_aware_local(self):
        start = services.slot_start(dt.date(2026, 11, 20), 1)
        self.assertIsNotNone(start.tzinfo)
        self.assertEqual(start.utcoffset(), dt.timedelta(hours=2))  # EET in November

    def test_dst_transitions_keep_wall_clock_times(self):
        # DST starts 2026-03-29 (EET +2 -> EEST +3) and ends 2026-10-25.
        before_spring = services.slot_start(dt.date(2026, 3, 28), 1)
        after_spring = services.slot_start(dt.date(2026, 3, 29), 1)
        self.assertEqual(before_spring.utcoffset(), dt.timedelta(hours=2))
        self.assertEqual(after_spring.utcoffset(), dt.timedelta(hours=3))
        self.assertEqual(after_spring.hour, 6)  # wall clock is unchanged

        before_autumn = services.slot_start(dt.date(2026, 10, 24), 1)
        after_autumn = services.slot_start(dt.date(2026, 10, 25), 1)
        self.assertEqual(before_autumn.utcoffset(), dt.timedelta(hours=3))
        self.assertEqual(after_autumn.utcoffset(), dt.timedelta(hours=2))

    def test_slot_5_over_autumn_dst_night(self):
        # Slot 5 starting 22:00 on the night clocks go back (change at 04:00):
        # both ends are still EEST, so the slot is the usual four hours.
        start = services.slot_end(dt.date(2026, 10, 24), 5)
        self.assertEqual(start.date(), dt.date(2026, 10, 25))
        self.assertEqual(start.hour, 2)
        self.assertEqual(start.utcoffset(), dt.timedelta(hours=3))

    def test_slot_labels(self):
        self.assertEqual(services.slot_label(1), "06:00–10:00")
        self.assertEqual(services.slot_label(5), "22:00–02:00")


class WeekCalculationTests(TestCase):
    def test_week_bounds_monday_through_sunday(self):
        for day in [MON, dt.date(2026, 11, 18), SUN]:
            with self.subTest(day=day):
                self.assertEqual(services.week_bounds(day), (MON, SUN))

    def test_week_bounds_on_boundary_days(self):
        self.assertEqual(services.week_bounds(MON)[0], MON)
        self.assertEqual(services.week_bounds(SUN)[1], SUN)
        # The Sunday before belongs to the previous week.
        prev_sunday = MON - dt.timedelta(days=1)
        self.assertEqual(services.week_bounds(prev_sunday)[1], prev_sunday)

    def test_booking_horizon_spans_current_and_next_week(self):
        first, last = services.booking_horizon(dt.date(2026, 11, 18))
        self.assertEqual(first, MON)
        self.assertEqual(last, dt.date(2026, 11, 29))

    def test_timezone_setting_is_applied(self):
        start = services.slot_start(dt.date(2026, 11, 20), 1)
        self.assertEqual(start.astimezone(TZ).hour, 6)
