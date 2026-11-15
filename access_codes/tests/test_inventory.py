from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from access_codes import services
from access_codes.models import LockboxPIN, PINAuditLog

from .utils import PASSWORD, add_available_pin, make_admin, make_resident, pin_key_settings


@pin_key_settings
class InventoryServiceTests(TestCase):
    def test_add_pin_stores_encrypted_unprogrammed(self):
        pin = services.add_pin("123456", actor=None)
        self.assertEqual(pin.status, LockboxPIN.Status.UNPROGRAMMED)
        self.assertNotIn("123456", pin.encrypted_pin)
        self.assertNotIn("123456", pin.pin_fingerprint)
        self.assertNotIn("123456", str(pin))
        self.assertTrue(
            PINAuditLog.objects.filter(pin=pin, action=PINAuditLog.Action.IMPORTED).exists()
        )

    def test_duplicate_pin_rejected(self):
        services.add_pin("123456", actor=None)
        with self.assertRaises(services.DuplicatePINError):
            services.add_pin("123456", actor=None)
        self.assertEqual(LockboxPIN.objects.count(), 1)

    def test_invalid_format_rejected(self):
        with self.assertRaises(ValidationError):
            services.add_pin("12", actor=None)
        self.assertEqual(LockboxPIN.objects.count(), 0)

    def test_import_pins(self):
        result = services.import_pins(
            ["pin", "111111", "222222", "111111", "bad-pin", "", "333333"], actor=None
        )
        self.assertEqual(result.created, 3)
        self.assertEqual(result.duplicates, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("Line 5", result.errors[0])
        # Errors reference line numbers, never values.
        self.assertNotIn("bad-pin", result.errors[0])

    def test_confirm_programmed_transitions_only_unprogrammed(self):
        admin = make_admin()
        unprogrammed = services.add_pin("111111", actor=admin)
        available = add_available_pin("222222")
        count = services.confirm_programmed(LockboxPIN.objects.all(), actor=admin)
        self.assertEqual(count, 1)
        unprogrammed.refresh_from_db()
        available.refresh_from_db()
        self.assertEqual(unprogrammed.status, LockboxPIN.Status.AVAILABLE)
        self.assertEqual(available.status, LockboxPIN.Status.AVAILABLE)
        self.assertTrue(
            PINAuditLog.objects.filter(
                pin=unprogrammed, action=PINAuditLog.Action.PROGRAMMED, actor=admin
            ).exists()
        )

    def test_inventory_counts(self):
        services.add_pin("111111", actor=None)
        add_available_pin("222222")
        counts = services.inventory_counts()
        self.assertEqual(counts[LockboxPIN.Status.UNPROGRAMMED], 1)
        self.assertEqual(counts[LockboxPIN.Status.AVAILABLE], 1)
        self.assertEqual(counts[LockboxPIN.Status.REVOKED], 0)


@pin_key_settings
class InventoryAdminTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)

    def test_admin_can_add_single_pin(self):
        response = self.client.post(
            reverse("admin:access_codes_lockboxpin_add"), {"pin_value": "123456"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LockboxPIN.objects.count(), 1)
        self.assertEqual(
            LockboxPIN.objects.get().status, LockboxPIN.Status.UNPROGRAMMED
        )

    def test_add_form_never_redisplays_pin(self):
        # Duplicate triggers a validation error; the value must not be echoed.
        services.add_pin("123456", actor=self.admin)
        response = self.client.post(
            reverse("admin:access_codes_lockboxpin_add"), {"pin_value": "123456"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already in the inventory")
        self.assertNotContains(response, 'value="123456"')

    def test_csv_import_view(self):
        response = self.client.post(
            reverse("admin:access_codes_lockboxpin_import_csv"),
            {"csv_file": SimpleUploadedFile("pins.csv", b"pin\n111111\n222222\n111111\nxx\n")},
            follow=True,
        )
        self.assertContains(response, "Imported 2")
        self.assertContains(response, "Skipped 1 duplicate")
        self.assertContains(response, "invalid PIN format")
        self.assertEqual(LockboxPIN.objects.count(), 2)

    def test_changelist_shows_counts_and_no_pin_material(self):
        pin = add_available_pin("987654")
        response = self.client.get(reverse("admin:access_codes_lockboxpin_changelist"))
        self.assertContains(response, "Inventory:")
        self.assertContains(response, "Available: <strong>1</strong>")
        self.assertNotContains(response, "987654")
        self.assertNotContains(response, pin.encrypted_pin)

    def test_direct_editing_is_disabled(self):
        pin = services.add_pin("123456", actor=self.admin)
        response = self.client.post(
            reverse("admin:access_codes_lockboxpin_change", args=[pin.pk]),
            {"status": LockboxPIN.Status.AVAILABLE},
        )
        self.assertEqual(response.status_code, 403)
        pin.refresh_from_db()
        self.assertEqual(pin.status, LockboxPIN.Status.UNPROGRAMMED)

    def test_delete_is_disabled(self):
        pin = services.add_pin("123456", actor=self.admin)
        response = self.client.post(
            reverse("admin:access_codes_lockboxpin_delete", args=[pin.pk]), {"post": "yes"}
        )
        self.assertEqual(response.status_code, 403)

    def test_confirm_programmed_action_requires_confirmation(self):
        pin = services.add_pin("123456", actor=self.admin)
        changelist = reverse("admin:access_codes_lockboxpin_changelist")
        # First POST: confirmation page, nothing changed yet.
        response = self.client.post(
            changelist,
            {"action": "confirm_programmed_action", "_selected_action": [str(pin.pk)]},
        )
        self.assertContains(response, "Only confirm PINs you have personally programmed")
        pin.refresh_from_db()
        self.assertEqual(pin.status, LockboxPIN.Status.UNPROGRAMMED)
        # Second POST with the confirmation flag applies the transition.
        response = self.client.post(
            changelist,
            {
                "action": "confirm_programmed_action",
                "_selected_action": [str(pin.pk)],
                "transition_confirmed": "1",
            },
            follow=True,
        )
        self.assertContains(response, "marked AVAILABLE")
        pin.refresh_from_db()
        self.assertEqual(pin.status, LockboxPIN.Status.AVAILABLE)

    def test_ordinary_user_denied(self):
        resident = make_resident("res@example.com")
        self.client.logout()
        self.client.force_login(resident)
        for url in [
            reverse("admin:access_codes_lockboxpin_changelist"),
            reverse("admin:access_codes_lockboxpin_import_csv"),
        ]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/admin/login/", response["Location"])

    def test_resident_cannot_login_to_admin_at_all(self):
        make_resident("res2@example.com")
        self.client.logout()
        logged_in = self.client.login(username="res2@example.com", password=PASSWORD)
        self.assertTrue(logged_in)
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)
