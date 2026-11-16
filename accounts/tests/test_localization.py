"""Phase 5.1 — Bulgarian-only interface and overnight-slot label."""

import datetime as dt
import pathlib
import re

from django.conf import settings
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from access_codes.tests.utils import add_available_pin, make_pending_user, pin_key_settings
from accounts import services as account_services
from accounts.models import ApprovedResident
from laundry import services as laundry_services
from laundry.models import MachineSet
from laundry.tests.utils import FRI, freeze_now, make_admin, make_resident

GOOD_PASSWORD = "fresh-towels-2026"


class LocalizationSettingsTests(TestCase):
    def test_bulgarian_is_the_only_language(self):
        self.assertEqual(settings.LANGUAGE_CODE, "bg")
        self.assertTrue(settings.USE_I18N)
        self.assertEqual(settings.TIME_ZONE, "Europe/Sofia")

    def test_html_declares_bulgarian(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, '<html lang="bg">')

    def test_login_page_is_bulgarian(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "Вход")
        self.assertContains(response, "Забравена парола?")

    def test_registration_page_is_bulgarian(self):
        response = self.client.get(reverse("register"))
        self.assertContains(response, "Регистрация")
        self.assertContains(response, "Фамилия")
        self.assertContains(response, "Потвърдете паролата")


class ScheduleLocalizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = make_resident("ana@example.com")

    def test_schedule_navigation_is_bulgarian(self):
        self.client.force_login(self.ana)
        with freeze_now():
            response = self.client.get(reverse("schedule"))
        self.assertContains(response, "Тази седмица")
        self.assertContains(response, "Следваща седмица")
        self.assertContains(response, "График на пералнята")
        self.assertContains(response, "понеделник")

    def test_slot_5_label_has_no_next_day_suffix(self):
        self.assertEqual(laundry_services.slot_label(5), "22:00–02:00")
        self.client.force_login(self.ana)
        with freeze_now():
            response = self.client.get(reverse("schedule"))
        self.assertContains(response, "22:00–02:00")
        self.assertNotContains(response, "+1")
        self.assertNotContains(response, "(+1 day)")

    def test_slot_5_still_ends_at_2_am_next_day(self):
        """Regression (§2): the label hides the overnight carry but the slot
        itself still ends at 02:00 on the following date."""
        day = dt.date(2026, 11, 20)
        end = laundry_services.slot_end(day, 5)
        self.assertEqual(end.date(), day + dt.timedelta(days=1))
        self.assertEqual(end.hour, 2)
        self.assertEqual(
            end - laundry_services.slot_start(day, 5), dt.timedelta(hours=4)
        )

    def test_booking_validation_message_is_bulgarian(self):
        set1 = MachineSet.objects.get(name="Комплект 1")
        with freeze_now():
            laundry_services.book_slot(
                user=self.ana, machine_set=set1, booking_date=FRI, slot_number=1
            )
            with self.assertRaises(laundry_services.BookingError) as ctx:
                laundry_services.book_slot(
                    user=self.ana, machine_set=set1, booking_date=FRI, slot_number=2
                )
        self.assertIn("последователни", str(ctx.exception))


@pin_key_settings
class EmailLocalizationTests(TestCase):
    def test_verification_email_is_bulgarian(self):
        ApprovedResident.objects.create(first_name="Елена", last_name="Демирова")
        self.client.post(
            reverse("register"),
            {
                "first_name": "Елена",
                "last_name": "Демирова",
                "email": "elena@example.com",
                "password1": GOOD_PASSWORD,
                "password2": GOOD_PASSWORD,
            },
        )
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn("Потвърдете регистрацията си", message.subject)
        self.assertIn("потвърдете имейл адреса си", message.body)
        self.assertIn("Здравейте, Елена", message.body)

    def test_password_reset_email_is_bulgarian(self):
        make_resident("ana@example.com")
        self.client.post(reverse("password_reset"), {"email": "ana@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn("Смяна на парола", message.subject)
        self.assertIn("Задайте нова парола", message.body)

    def test_pin_email_is_bulgarian(self):
        add_available_pin("123456")
        user = make_pending_user()
        token = account_services.make_verification_token(user)
        with self.captureOnCommitCallbacks(execute=True):
            account_services.verify_email_token(token)
        pin_mail = mail.outbox[-1]
        self.assertIn("Вашият ПИН код", pin_mail.subject)
        self.assertIn("личен ПИН код", pin_mail.body)
        self.assertIn("123456", pin_mail.body)


class AdminLocalizationTests(TestCase):
    def test_admin_action_messages_are_bulgarian(self):
        admin = make_admin()
        resident = make_resident("res@example.com")
        self.client.force_login(admin)
        response = self.client.post(
            reverse("admin:accounts_user_changelist"),
            {"action": "deactivate_users", "_selected_action": [str(resident.pk)]},
            follow=True,
        )
        self.assertContains(response, "Деактивирани потребители: 1.")

    def test_admin_site_is_bulgarian(self):
        admin = make_admin()
        self.client.force_login(admin)
        response = self.client.get(reverse("admin:index"))
        # App names come from our AppConfigs; admin chrome from Django's bg catalog.
        self.assertContains(response, "Профили и живущи")
        self.assertContains(response, "График и резервации")
        self.assertContains(response, "ПИН кодове")


class NoEnglishLeftTests(TestCase):
    """§10.12 — no English UI text remains in the custom templates."""

    ENGLISH_UI_WORDS = re.compile(
        r"\b(Login|Logout|Register|Schedule|Reservations?|Account|Password|"
        r"Reveal|Cancel|Book(ed)?|Dorm Laundry|\+1 day|Check your|Forgot|"
        r"Available|Occupied|Washer|Dryer|Machine|Upcoming|History|Welcome|"
        r"Import|Confirm|Inventory|Delivery|Resident|Email|Name|Date|Status)\b"
    )
    TAGS = re.compile(r"{%.*?%}|{{.*?}}|<[^>]*>")

    def test_custom_templates_contain_no_english_ui_text(self):
        templates_dir = pathlib.Path(settings.BASE_DIR) / "templates"
        offenders = {}
        for path in sorted(templates_dir.rglob("*")):
            if path.suffix not in (".html", ".txt"):
                continue
            visible_text = self.TAGS.sub(" ", path.read_text())
            found = sorted({m.group(0) for m in self.ENGLISH_UI_WORDS.finditer(visible_text)})
            if found:
                offenders[str(path.relative_to(templates_dir))] = found
        self.assertEqual(offenders, {})
