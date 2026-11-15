
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import ApprovedResident, normalize_name

User = get_user_model()

ADMIN_PASSWORD = "admin-pass-word-123"


def make_admin(email="admin@example.com"):
    return User.objects.create_superuser(
        email=email, password=ADMIN_PASSWORD, first_name="Admin", last_name="One",
        email_verified=True,
    )


class NormalizationTests(TestCase):
    def test_normalize_name(self):
        self.assertEqual(normalize_name("  Ana   MARÍA "), "ana maría")
        self.assertEqual(normalize_name("ANA"), normalize_name("ana"))
        # NFKC: fullwidth and composed forms collapse to the same value.
        self.assertEqual(normalize_name("Ａna"), "ana")

    def test_saved_entry_stores_normalized_names(self):
        entry = ApprovedResident.objects.create(first_name="  Иван ", last_name=" ПЕТРОВ  ")
        self.assertEqual(entry.first_name, "Иван")
        self.assertEqual(entry.normalized_first_name, "иван")
        self.assertEqual(entry.normalized_last_name, "петров")


class ApprovedResidentModelTests(TestCase):
    def test_duplicate_normalized_names_rejected(self):
        ApprovedResident.objects.create(first_name="Ana", last_name="Petrova")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ApprovedResident.objects.create(first_name=" ANA ", last_name="petrova")

    def test_distinct_names_allowed(self):
        ApprovedResident.objects.create(first_name="Ana", last_name="Petrova")
        ApprovedResident.objects.create(first_name="Ana", last_name="Ivanova")
        self.assertEqual(ApprovedResident.objects.count(), 2)


class ApprovedResidentAdminTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)

    def test_admin_can_create_approved_resident(self):
        response = self.client.post(
            reverse("admin:accounts_approvedresident_add"),
            {"first_name": "Mira", "last_name": "Koleva", "is_eligible": "on"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ApprovedResident.objects.filter(normalized_last_name="koleva").exists()
        )

    def test_search_by_name(self):
        ApprovedResident.objects.create(first_name="Mira", last_name="Koleva")
        ApprovedResident.objects.create(first_name="Ana", last_name="Petrova")
        response = self.client.get(
            reverse("admin:accounts_approvedresident_changelist"), {"q": "koleva"}
        )
        self.assertContains(response, "Mira")
        self.assertNotContains(response, "Petrova")

    def test_ordinary_user_cannot_manage_approved_residents(self):
        resident = User.objects.create_user(
            email="res@example.com", password="resident-pass-1", email_verified=True
        )
        self.client.force_login(resident)
        response = self.client.get(reverse("admin:accounts_approvedresident_changelist"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_claimed_entry_cannot_be_deleted(self):
        user = User.objects.create_user(email="claimed@example.com", password="resident-pass-1")
        entry = ApprovedResident.objects.create(
            first_name="Ana", last_name="Petrova", registered_user=user
        )
        response = self.client.post(
            reverse("admin:accounts_approvedresident_delete", args=[entry.pk]), {"post": "yes"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(ApprovedResident.objects.filter(pk=entry.pk).exists())


class CSVImportTests(TestCase):
    url = reverse("admin:accounts_approvedresident_import_csv")

    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)

    def _upload(self, content: bytes):
        return self.client.post(
            self.url, {"csv_file": SimpleUploadedFile("residents.csv", content)}, follow=True
        )

    def test_import_creates_residents(self):
        response = self._upload(b"first_name,last_name\nAna,Petrova\nMira,Koleva\n")
        self.assertEqual(ApprovedResident.objects.count(), 2)
        self.assertContains(response, "Imported 2")

    def test_duplicates_are_skipped(self):
        ApprovedResident.objects.create(first_name="Ana", last_name="Petrova")
        response = self._upload(b"ANA,petrova\nMira,Koleva\n")
        self.assertEqual(ApprovedResident.objects.count(), 2)
        self.assertContains(response, "Skipped 1")

    def test_malformed_rows_are_reported(self):
        response = self._upload(b"OnlyOneColumn\nAna,Petrova\n")
        self.assertEqual(ApprovedResident.objects.count(), 1)
        self.assertContains(response, "Line 1")

    def test_non_utf8_file_is_rejected(self):
        response = self._upload("Ана,Петрова".encode("cp1251"))
        self.assertEqual(ApprovedResident.objects.count(), 0)
        self.assertContains(response, "not valid UTF-8")

    def test_ordinary_user_denied(self):
        resident = User.objects.create_user(email="res@example.com", password="resident-pass-1")
        self.client.force_login(resident)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])
