from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import SimpleTestCase, override_settings

from access_codes import crypto

from .utils import TEST_PIN_KEY, pin_key_settings


@pin_key_settings
class CryptoTests(SimpleTestCase):
    def test_encrypt_decrypt_roundtrip(self):
        token = crypto.encrypt_pin("123456")
        self.assertNotIn("123456", token)
        self.assertEqual(crypto.decrypt_pin(token), "123456")

    def test_fingerprint_is_deterministic_and_distinct(self):
        self.assertEqual(crypto.pin_fingerprint("1234"), crypto.pin_fingerprint("1234"))
        self.assertNotEqual(crypto.pin_fingerprint("1234"), crypto.pin_fingerprint("1235"))
        self.assertNotIn("1234", crypto.pin_fingerprint("1234"))

    def test_missing_key_fails_safely(self):
        with override_settings(PIN_ENCRYPTION_KEY=""):
            with self.assertRaises(ImproperlyConfigured):
                crypto.encrypt_pin("1234")
            with self.assertRaises(ImproperlyConfigured):
                crypto.pin_fingerprint("1234")

    def test_invalid_key_fails_safely(self):
        with (
            override_settings(PIN_ENCRYPTION_KEY="not-a-valid-fernet-key"),
            self.assertRaises(ImproperlyConfigured),
        ):
            crypto.encrypt_pin("1234")

    def test_wrong_key_cannot_decrypt(self):
        from cryptography.fernet import Fernet

        token = crypto.encrypt_pin("123456")
        with (
            override_settings(PIN_ENCRYPTION_KEY=Fernet.generate_key().decode()),
            self.assertRaises(ImproperlyConfigured),
        ):
            crypto.decrypt_pin(token)

    def test_format_validator(self):
        for good in ["1234", "12345678"]:
            crypto.validate_pin_format(good)  # no exception
        for bad in ["123", "123456789", "12ab", "12 34", ""]:
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                crypto.validate_pin_format(bad)

    @override_settings(PIN_FORMAT_REGEX=r"^\d{6}$")
    def test_format_is_configurable(self):
        crypto.validate_pin_format("123456")
        with self.assertRaises(ValidationError):
            crypto.validate_pin_format("1234")

    def test_key_constant_is_valid_for_tests(self):
        self.assertEqual(len(TEST_PIN_KEY), 44)
