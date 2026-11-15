from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from accounts import services
from accounts.models import ApprovedResident

User = get_user_model()

GOOD_PASSWORD = "laundry-rocks-2026"


def form_data(**overrides):
    data = {
        "first_name": "Ana",
        "last_name": "Petrova",
        "email": "ana@example.com",
        "password1": GOOD_PASSWORD,
        "password2": GOOD_PASSWORD,
    }
    data.update(overrides)
    return data


class RegistrationTests(TestCase):
    url = reverse("register")

    def setUp(self):
        self.entry = ApprovedResident.objects.create(first_name="Ana", last_name="Petrova")

    def test_successful_registration(self):
        response = self.client.post(self.url, form_data())
        self.assertRedirects(response, reverse("registration_pending"))

        user = User.objects.get(email="ana@example.com")
        self.assertFalse(user.is_active)
        self.assertFalse(user.email_verified)
        self.assertIsNone(user.deactivated_at)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.registered_user, user)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("verify", mail.outbox[0].body.lower())
        self.assertEqual(mail.outbox[0].to, ["ana@example.com"])

    def test_registration_grants_no_admin_privileges(self):
        self.client.post(self.url, form_data())
        user = User.objects.get(email="ana@example.com")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_unapproved_name_rejected(self):
        response = self.client.post(self.url, form_data(first_name="Boris"))
        self.assertContains(response, "approved residents")
        self.assertFalse(User.objects.filter(email="ana@example.com").exists())

    def test_name_matching_is_normalized(self):
        response = self.client.post(
            self.url, form_data(first_name="  aNA ", last_name=" PETROVA  ")
        )
        self.assertRedirects(response, reverse("registration_pending"))
        user = User.objects.get(email="ana@example.com")
        # Canonical approved spelling is stored, not the applicant's input.
        self.assertEqual(user.first_name, "Ana")
        self.assertEqual(user.last_name, "Petrova")

    def test_claimed_entry_cannot_be_claimed_twice(self):
        self.client.post(self.url, form_data())
        response = self.client.post(self.url, form_data(email="other@example.com"))
        self.assertContains(response, "approved residents")
        self.assertFalse(User.objects.filter(email="other@example.com").exists())

    def test_ineligible_entry_rejected(self):
        self.entry.is_eligible = False
        self.entry.save()
        response = self.client.post(self.url, form_data())
        self.assertContains(response, "approved residents")
        self.assertEqual(User.objects.count(), 0)

    def test_duplicate_email_rejected(self):
        User.objects.create_user(email="ana@example.com", password="whatever-pass-1")
        response = self.client.post(self.url, form_data())
        self.assertContains(response, "already exists")
        self.assertIsNone(
            ApprovedResident.objects.get(pk=self.entry.pk).registered_user
        )

    def test_duplicate_email_case_insensitive(self):
        User.objects.create_user(email="ana@example.com", password="whatever-pass-1")
        response = self.client.post(self.url, form_data(email="ANA@example.com"))
        self.assertContains(response, "already exists")

    def test_weak_password_rejected(self):
        response = self.client.post(
            self.url, form_data(password1="short", password2="short")
        )
        self.assertEqual(User.objects.count(), 0)
        self.assertTrue(response.context["form"].errors["password1"])

    def test_mismatched_passwords_rejected(self):
        response = self.client.post(self.url, form_data(password2="different-pass-99"))
        self.assertEqual(User.objects.count(), 0)
        self.assertTrue(response.context["form"].errors["password2"])

    def test_missing_fields_rejected(self):
        response = self.client.post(self.url, {})
        self.assertEqual(User.objects.count(), 0)
        for field in ["first_name", "last_name", "email", "password1", "password2"]:
            self.assertIn(field, response.context["form"].errors)

    def test_authenticated_user_is_redirected_away(self):
        user = User.objects.create_user(
            email="existing@example.com", password=GOOD_PASSWORD, email_verified=True
        )
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("schedule"))


class RegistrationConcurrencyTests(TestCase):
    """The claim is a conditional UPDATE, so a lost race rolls everything back."""

    def setUp(self):
        self.entry = ApprovedResident.objects.create(first_name="Ana", last_name="Petrova")

    def test_second_claim_of_same_entry_fails_and_rolls_back(self):
        first = services.register_resident(
            first_name="Ana", last_name="Petrova", email="first@example.com",
            password=GOOD_PASSWORD,
        )
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.registered_user, first)

        with self.assertRaises(services.RegistrationError):
            services.register_resident(
                first_name="Ana", last_name="Petrova", email="second@example.com",
                password=GOOD_PASSWORD,
            )
        # The losing registration left no orphan user behind.
        self.assertFalse(User.objects.filter(email="second@example.com").exists())
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.registered_user, first)

    def test_conditional_claim_is_atomic(self):
        """Simulates the losing side of a race: entry claimed between the form
        check and the transaction. The conditional UPDATE must not overwrite."""
        winner = User.objects.create_user(email="winner@example.com", password=GOOD_PASSWORD)
        ApprovedResident.objects.filter(pk=self.entry.pk).update(registered_user=winner)

        with self.assertRaises(services.RegistrationError):
            services.register_resident(
                first_name="Ana", last_name="Petrova", email="loser@example.com",
                password=GOOD_PASSWORD,
            )
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.registered_user, winner)
        self.assertFalse(User.objects.filter(email="loser@example.com").exists())
