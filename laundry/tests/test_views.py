from django.test import TestCase
from django.urls import reverse

from laundry import services
from laundry.models import MachineSet, Reservation

from .utils import FRI, MON, NEXT_MON, PASSWORD, freeze_now, make_admin, make_resident


class ScheduleViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.set1 = MachineSet.objects.get(name="Set 1")
        cls.ana = make_resident("ana@example.com")
        cls.boris = make_resident("boris@example.com", "Boris", "Iliev")

    def test_login_required(self):
        response = self.client.get(reverse("schedule"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_renders_week_days_slots_and_sets(self):
        self.client.force_login(self.ana)
        with freeze_now():
            response = self.client.get(reverse("schedule"))
        for day_name in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
            self.assertContains(response, day_name)
        for set_name in ["Set 1", "Set 2", "Set 3"]:
            self.assertContains(response, set_name)
        self.assertContains(response, "06:00–10:00")
        self.assertContains(response, "22:00–02:00 (+1 day)")

    def test_occupied_slot_shows_resident_name_but_never_email(self):
        with freeze_now():
            services.book_slot(
                user=self.boris, machine_set=self.set1, booking_date=FRI, slot_number=1
            )
        self.client.force_login(self.ana)
        with freeze_now():
            response = self.client.get(reverse("schedule"))
        self.assertContains(response, "Boris")
        self.assertContains(response, "Iliev")
        self.assertNotContains(response, "boris@example.com")

    def test_own_reservation_shows_cancel_others_do_not(self):
        with freeze_now():
            mine = services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=1
            )
            theirs = services.book_slot(
                user=self.boris, machine_set=self.set1, booking_date=FRI, slot_number=3
            )
        self.client.force_login(self.ana)
        with freeze_now():
            response = self.client.get(reverse("schedule"))
        self.assertContains(response, reverse("cancel_reservation", args=[mine.pk]))
        self.assertNotContains(response, reverse("cancel_reservation", args=[theirs.pk]))

    def test_next_week_navigation(self):
        self.client.force_login(self.ana)
        with freeze_now():
            current = self.client.get(reverse("schedule"))
            response = self.client.get(reverse("schedule"), {"week": "next"})
        self.assertContains(current, "18 Nov")       # today is in the current week
        self.assertNotContains(current, "23 Nov")
        self.assertContains(response, "23 Nov")      # next week's Monday
        self.assertContains(response, "29 Nov")      # next week's Sunday

    def test_navbar_treats_schedule_as_the_landing_page(self):
        self.client.force_login(self.ana)
        with freeze_now():
            response = self.client.get(reverse("schedule"))
        # Brand links to the schedule; there is no separate Home item.
        self.assertContains(
            response, f'class="navbar-brand" href="{reverse("schedule")}"'
        )
        self.assertNotContains(response, ">Home<")
        self.assertContains(response, ">Schedule<")
        self.assertContains(response, ">My Reservations<")

    def test_root_url_redirects_to_schedule(self):
        self.client.force_login(self.ana)
        response = self.client.get("/")
        self.assertRedirects(response, reverse("schedule"))

    def test_mobile_friendly_layout_markers(self):
        self.client.force_login(self.ana)
        with freeze_now():
            response = self.client.get(reverse("schedule"))
        # Responsive stacking columns and scroll containers, no fixed-width grid.
        self.assertContains(response, "col-12 col-md-6 col-xl-4")
        self.assertContains(response, "table-responsive")
        self.assertContains(response, 'name="viewport" content="width=device-width')


class BookSlotViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.set1 = MachineSet.objects.get(name="Set 1")
        cls.ana = make_resident("ana@example.com")

    def _post(self, **overrides):
        data = {
            "machine_set": self.set1.pk,
            "booking_date": FRI.isoformat(),
            "slot_number": 1,
            "week": "current",
        }
        data.update(overrides)
        return self.client.post(reverse("book_slot"), data, follow=True)

    def test_login_required(self):
        response = self.client.post(reverse("book_slot"), {})
        self.assertIn(reverse("login"), response["Location"])

    def test_successful_booking_shows_confirmation(self):
        self.client.force_login(self.ana)
        with freeze_now():
            response = self._post()
        self.assertContains(response, "Booked Set 1")
        self.assertEqual(Reservation.objects.count(), 1)

    def test_booking_error_is_shown(self):
        boris = make_resident("boris@example.com", "Boris", "Iliev")
        with freeze_now():
            services.book_slot(
                user=boris, machine_set=self.set1, booking_date=FRI, slot_number=1
            )
        self.client.force_login(self.ana)
        with freeze_now():
            response = self._post()
        self.assertContains(response, "already booked")
        self.assertEqual(Reservation.objects.count(), 1)

    def test_invalid_payload_is_handled(self):
        self.client.force_login(self.ana)
        with freeze_now():
            response = self._post(booking_date="not-a-date")
        self.assertContains(response, "not valid")
        self.assertEqual(Reservation.objects.count(), 0)

    def test_get_not_allowed(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse("book_slot")).status_code, 405)


class CancelReservationViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.set1 = MachineSet.objects.get(name="Set 1")
        cls.ana = make_resident("ana@example.com")
        cls.boris = make_resident("boris@example.com", "Boris", "Iliev")
        cls.admin = make_admin()

    def _reservation(self, user=None):
        with freeze_now():
            return services.book_slot(
                user=user or self.ana,
                machine_set=self.set1,
                booking_date=FRI,
                slot_number=1,
            )

    def test_confirmation_page_then_cancel(self):
        reservation = self._reservation()
        self.client.force_login(self.ana)
        url = reverse("cancel_reservation", args=[reservation.pk])
        with freeze_now():
            confirm = self.client.get(url)
            self.assertContains(confirm, "Cancel this reservation?")
            response = self.client.post(url, {"next": "reservations"}, follow=True)
        self.assertContains(response, "Cancelled Set 1")
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.CANCELLED)

    def test_cannot_cancel_someone_elses_reservation(self):
        reservation = self._reservation(user=self.boris)
        self.client.force_login(self.ana)
        url = reverse("cancel_reservation", args=[reservation.pk])
        with freeze_now():
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url, {}).status_code, 404)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.ACTIVE)

    def test_admin_can_cancel_any_reservation_via_view(self):
        reservation = self._reservation(user=self.boris)
        self.client.force_login(self.admin)
        url = reverse("cancel_reservation", args=[reservation.pk])
        with freeze_now():
            self.client.post(url, {})
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.CANCELLED)
        self.assertEqual(reservation.cancelled_by, self.admin)


class MyReservationsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.set1 = MachineSet.objects.get(name="Set 1")
        cls.ana = make_resident("ana@example.com")

    def test_login_required(self):
        response = self.client.get(reverse("my_reservations"))
        self.assertIn(reverse("login"), response["Location"])

    def test_upcoming_and_history_sections(self):
        # A reservation on a previous day belongs to history; today's active
        # reservations stay under Upcoming because they can still be cancelled.
        past = Reservation.objects.create(
            user=self.ana, machine_set=self.set1, booking_date=MON, slot_number=1
        )
        with freeze_now():
            upcoming = services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=3
            )
            cancelled = services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=NEXT_MON, slot_number=1
            )
            services.cancel_reservation(reservation=cancelled, cancelled_by=self.ana)

        self.client.force_login(self.ana)
        with freeze_now():
            response = self.client.get(reverse("my_reservations"))
        self.assertContains(response, "Upcoming")
        self.assertContains(response, "History")
        self.assertContains(response, "Cancelled")
        self.assertContains(response, reverse("cancel_reservation", args=[upcoming.pk]))
        self.assertNotContains(response, reverse("cancel_reservation", args=[past.pk]))

    def test_login_redirects_to_schedule(self):
        make_resident("login@example.com")
        response = self.client.post(
            reverse("login"), {"username": "login@example.com", "password": PASSWORD}
        )
        self.assertRedirects(response, reverse("schedule"))
