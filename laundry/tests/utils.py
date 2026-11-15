"""Shared helpers: a frozen clock and user factories for deterministic tests.

The frozen "now" is Wednesday 2026-11-18 12:00 Europe/Sofia, so:
- current week: Mon 2026-11-16 .. Sun 2026-11-22
- next week:    Mon 2026-11-23 .. Sun 2026-11-29
- today's slots 1 (06:00) and 2 (10:00) have already started; slot 3 has not.
"""

import datetime as dt
import zoneinfo
from unittest import mock

from django.contrib.auth import get_user_model

TZ = zoneinfo.ZoneInfo("Europe/Sofia")

FIXED_NOW = dt.datetime(2026, 11, 18, 12, 0, tzinfo=TZ)  # Wednesday noon

MON = dt.date(2026, 11, 16)
TUE = dt.date(2026, 11, 17)
WED = dt.date(2026, 11, 18)  # "today"
THU = dt.date(2026, 11, 19)
FRI = dt.date(2026, 11, 20)
SAT = dt.date(2026, 11, 21)
SUN = dt.date(2026, 11, 22)
NEXT_MON = dt.date(2026, 11, 23)
NEXT_SUN = dt.date(2026, 11, 29)
AFTER_HORIZON = dt.date(2026, 11, 30)

PASSWORD = "laundry-rocks-2026"


def freeze_now(moment: dt.datetime = FIXED_NOW):
    """Patch django.utils.timezone.now (used by services and views)."""
    return mock.patch("django.utils.timezone.now", return_value=moment)


def make_resident(email: str, first_name: str = "Ana", last_name: str = "Petrova"):
    return get_user_model().objects.create_user(
        email=email,
        password=PASSWORD,
        first_name=first_name,
        last_name=last_name,
        is_active=True,
        email_verified=True,
    )


def make_admin(email: str = "admin@example.com"):
    return get_user_model().objects.create_superuser(
        email=email,
        password=PASSWORD,
        first_name="Admin",
        last_name="User",
        email_verified=True,
    )
