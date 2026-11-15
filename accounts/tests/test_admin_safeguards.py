from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts import services

User = get_user_model()

PASSWORD = "admin-pass-word-123"


def make_admin(email):
    return User.objects.create_superuser(
        email=email, password=PASSWORD, first_name="Admin", last_name="User",
        email_verified=True,
    )


def make_resident(email="res@example.com"):
    return User.objects.create_user(
        email=email, password=PASSWORD, first_name="Res", last_name="Ident",
        is_active=True, email_verified=True,
    )


class AdminSafeguardServiceTests(TestCase):
    def test_promotion(self):
        make_admin("admin1@example.com")
        resident = make_resident()
        resident.is_staff = True
        resident.is_superuser = True
        resident.save()
        self.assertTrue(User.objects.get(pk=resident.pk).is_admin)

    def test_demotion_allowed_when_another_admin_exists(self):
        make_admin("admin1@example.com")
        second = make_admin("admin2@example.com")
        services.check_admin_change(
            second, is_active=True, is_staff=False, is_superuser=False
        )  # no exception

    def test_last_admin_cannot_be_demoted(self):
        admin = make_admin("admin1@example.com")
        with self.assertRaises(ValidationError):
            services.check_admin_change(
                admin, is_active=True, is_staff=True, is_superuser=False
            )

    def test_last_admin_cannot_be_deactivated(self):
        admin = make_admin("admin1@example.com")
        with self.assertRaises(ValidationError):
            services.deactivate_user(admin)
        admin.refresh_from_db()
        self.assertTrue(admin.is_active)

    def test_last_admin_cannot_be_deleted(self):
        admin = make_admin("admin1@example.com")
        with self.assertRaises(ValidationError):
            services.check_admin_delete(admin)

    def test_deactivated_admin_does_not_count_as_backup(self):
        admin = make_admin("admin1@example.com")
        other = make_admin("admin2@example.com")
        services.deactivate_user(other)
        with self.assertRaises(ValidationError):
            services.check_admin_change(
                admin, is_active=False, is_staff=True, is_superuser=True
            )

    def test_non_admin_changes_are_unrestricted(self):
        resident = make_resident()
        services.check_admin_change(
            resident, is_active=False, is_staff=False, is_superuser=False
        )  # no exception


class AdminSafeguardAdminSiteTests(TestCase):
    def setUp(self):
        self.admin = make_admin("admin1@example.com")
        self.client.force_login(self.admin)

    def test_last_admin_delete_is_forbidden_in_admin(self):
        response = self.client.post(
            reverse("admin:accounts_user_delete", args=[self.admin.pk]), {"post": "yes"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_admin_can_be_deleted_when_backup_exists(self):
        second = make_admin("admin2@example.com")
        response = self.client.post(
            reverse("admin:accounts_user_delete", args=[second.pk]), {"post": "yes"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(pk=second.pk).exists())

    def test_deactivate_action_skips_last_admin(self):
        resident = make_resident()
        response = self.client.post(
            reverse("admin:accounts_user_changelist"),
            {
                "action": "deactivate_users",
                "_selected_action": [str(self.admin.pk), str(resident.pk)],
            },
            follow=True,
        )
        self.assertContains(response, "last active administrator")
        self.admin.refresh_from_db()
        resident.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertFalse(resident.is_active)
        self.assertIsNotNone(resident.deactivated_at)

    def test_reactivating_user_clears_deactivated_at(self):
        resident = make_resident()
        services.deactivate_user(resident)
        services.reactivate_user(resident)
        resident.refresh_from_db()
        self.assertTrue(resident.is_active)
        self.assertIsNone(resident.deactivated_at)

    def test_ordinary_user_denied_admin_site(self):
        self.client.logout()
        self.client.force_login(make_resident())
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])
