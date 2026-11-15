"""Shared Phase 4 test helpers.

TEST_PIN_KEY is a throwaway Fernet key used ONLY in automated tests; it is
not a production secret and must never be reused outside the test suite.
"""

from django.test import override_settings

from access_codes import services
from access_codes.models import LockboxPIN
from laundry.tests.utils import PASSWORD, make_admin, make_resident  # noqa: F401 (re-export)

TEST_PIN_KEY = "3SsWyDJ9-YOo2XEX5qR4baiVcZdTFSPDthpZL_uPFqI="

pin_key_settings = override_settings(PIN_ENCRYPTION_KEY=TEST_PIN_KEY)


def add_available_pin(value: str, actor=None) -> LockboxPIN:
    """Import a PIN and confirm it as programmed, making it AVAILABLE."""
    pin = services.add_pin(value, actor=actor)
    pin.status = LockboxPIN.Status.AVAILABLE
    pin.save(update_fields=["status"])
    return pin


def make_pending_user(email="pending@example.com"):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        email=email,
        password=PASSWORD,
        first_name="Pending",
        last_name="Resident",
        is_active=False,
        email_verified=False,
    )
