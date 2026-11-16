from django.test import TestCase
from django.urls import reverse

from laundry import services
from laundry.models import MachineSet, Reservation

from .utils import FRI, MON, freeze_now, make_admin, make_resident


class ReservationAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.set1 = MachineSet.objects.get(name="Комплект 1")
        cls.admin = make_admin()
        cls.ana = make_resident("ana@example.com")

    def setUp(self):
        self.client.force_login(self.admin)
        with freeze_now():
            self.reservation = services.book_slot(
                user=self.ana, machine_set=self.set1, booking_date=FRI, slot_number=1
            )

    def test_changelist_lists_and_searches_by_resident_name(self):
        url = reverse("admin:laundry_reservation_changelist")
        response = self.client.get(url, {"q": "Petrova"})
        self.assertContains(response, "Ana Petrova")
        response = self.client.get(url, {"q": "Nonexistent"})
        self.assertNotContains(response, "Ana Petrova")

    def test_changelist_filters_by_status_and_machine(self):
        url = reverse("admin:laundry_reservation_changelist")
        response = self.client.get(
            url, {"status__exact": "ACTIVE", "machine_set__id__exact": self.set1.pk}
        )
        self.assertContains(response, "Ana Petrova")

    def test_add_is_disabled(self):
        response = self.client.get(reverse("admin:laundry_reservation_add"))
        self.assertEqual(response.status_code, 403)

    def test_editing_is_disabled(self):
        response = self.client.post(
            reverse("admin:laundry_reservation_change", args=[self.reservation.pk]),
            {"slot_number": 3},
        )
        self.assertEqual(response.status_code, 403)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.slot_number, 1)

    def test_delete_is_disabled(self):
        response = self.client.post(
            reverse("admin:laundry_reservation_delete", args=[self.reservation.pk]),
            {"post": "yes"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Reservation.objects.filter(pk=self.reservation.pk).exists())

    def test_cancel_action_cancels_via_service(self):
        with freeze_now():
            response = self.client.post(
                reverse("admin:laundry_reservation_changelist"),
                {
                    "action": "cancel_reservations",
                    "_selected_action": [str(self.reservation.pk)],
                },
                follow=True,
            )
        self.assertContains(response, "Отказани резервации: 1.")
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.CANCELLED)
        self.assertEqual(self.reservation.cancelled_by, self.admin)

    def test_cancel_action_reports_already_cancelled(self):
        with freeze_now():
            services.cancel_reservation(
                reservation=self.reservation, cancelled_by=self.admin
            )
            response = self.client.post(
                reverse("admin:laundry_reservation_changelist"),
                {
                    "action": "cancel_reservations",
                    "_selected_action": [str(self.reservation.pk)],
                },
                follow=True,
            )
        self.assertContains(response, "вече е отказана")

    def test_timing_filter_separates_upcoming_from_past(self):
        past = Reservation.objects.create(
            user=self.ana, machine_set=self.set1, booking_date=MON, slot_number=2
        )
        url = reverse("admin:laundry_reservation_changelist")
        with freeze_now():
            upcoming = self.client.get(url, {"timing": "upcoming"})
            past_view = self.client.get(url, {"timing": "past"})
        self.assertContains(upcoming, f"/laundry/reservation/{self.reservation.pk}/")
        self.assertNotContains(upcoming, f"/laundry/reservation/{past.pk}/")
        self.assertContains(past_view, f"/laundry/reservation/{past.pk}/")
        self.assertNotContains(past_view, f"/laundry/reservation/{self.reservation.pk}/")

    def test_ordinary_user_denied(self):
        self.client.logout()
        self.client.force_login(self.ana)
        response = self.client.get(reverse("admin:laundry_reservation_changelist"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])


class MachineSetAdminTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)

    def test_lists_three_machine_sets(self):
        response = self.client.get(reverse("admin:laundry_machineset_changelist"))
        for name in ["Комплект 1", "Комплект 2", "Комплект 3"]:
            self.assertContains(response, name)

    def test_delete_is_disabled(self):
        machine_set = MachineSet.objects.get(name="Комплект 1")
        response = self.client.post(
            reverse("admin:laundry_machineset_delete", args=[machine_set.pk]),
            {"post": "yes"},
        )
        self.assertEqual(response.status_code, 403)
