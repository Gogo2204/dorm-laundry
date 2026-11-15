from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from access_codes.models import LockboxPIN

from .utils import PASSWORD, add_available_pin, make_resident, pin_key_settings


@pin_key_settings
class RevealPINTests(TestCase):
    def setUp(self):
        self.user = make_resident("owner@example.com", "Elena", "Demirova")
        self.pin = add_available_pin("654321")
        LockboxPIN.objects.filter(pk=self.pin.pk).update(
            status=LockboxPIN.Status.ASSIGNED,
            assigned_user=self.user,
            assigned_at=timezone.now(),
        )

    def test_account_page_masks_pin_by_default(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("account"))
        self.assertContains(response, "••••••")
        self.assertContains(response, "Reveal PIN")
        self.assertNotContains(response, "654321")

    def test_reveal_requires_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("reveal_pin")).status_code, 405)

    def test_owner_can_reveal_own_pin(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("reveal_pin"))
        self.assertContains(response, "654321")
        self.assertContains(response, "personal")

    def test_reveal_response_is_not_cacheable(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("reveal_pin"))
        self.assertIn("no-store", response["Cache-Control"])

    def test_another_user_cannot_see_someone_elses_pin(self):
        other = make_resident("other@example.com", "Boris", "Iliev")
        self.client.force_login(other)
        account = self.client.get(reverse("account"))
        self.assertNotContains(account, "654321")
        reveal = self.client.post(reverse("reveal_pin"))
        self.assertNotContains(reveal, "654321")
        self.assertContains(reveal, "awaiting assignment")

    def test_unauthenticated_reveal_redirects_to_login(self):
        response = self.client.post(reverse("reveal_pin"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_deactivated_user_cannot_access_account_or_reveal(self):
        self.client.login(username="owner@example.com", password=PASSWORD)
        self.user.is_active = False
        self.user.deactivated_at = timezone.now()
        self.user.save()
        for url in [reverse("account"), reverse("reveal_pin")]:
            response = self.client.post(url) if url == reverse("reveal_pin") else self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("login"), response["Location"])

    def test_waiting_message_when_no_pin(self):
        waiting = make_resident("waiting@example.com")
        self.client.force_login(waiting)
        response = self.client.get(reverse("account"))
        self.assertContains(response, "awaiting assignment by an administrator")
