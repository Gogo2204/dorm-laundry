"""Initialize the two equal-permission administrator accounts (FR-09).

Credentials are never hardcoded. For each admin slot (1 and 2) the command
reads ``ADMIN<N>_EMAIL``, ``ADMIN<N>_PASSWORD``, ``ADMIN<N>_FIRST_NAME`` and
``ADMIN<N>_LAST_NAME`` from the environment; when run in an interactive
terminal, missing values are prompted for instead (passwords via getpass).

The command is idempotent: an existing active administrator with the same
email is left untouched, so it is safe to re-run after a partial failure.
"""

import os
import sys
from dataclasses import dataclass
from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction

ADMIN_SLOTS = (1, 2)
ENV_FIELDS = ("EMAIL", "PASSWORD", "FIRST_NAME", "LAST_NAME")


@dataclass
class AdminCredentials:
    email: str
    password: str
    first_name: str
    last_name: str


class Command(BaseCommand):
    help = "Create the two initial administrator accounts from environment variables or interactive prompts."

    def handle(self, *args, **options):
        credentials = [self._collect(slot) for slot in ADMIN_SLOTS]

        if credentials[0].email.lower() == credentials[1].email.lower():
            raise CommandError("The two administrators must have different email addresses.")

        with transaction.atomic():
            for slot, creds in zip(ADMIN_SLOTS, credentials):
                self._create_admin(slot, creds)

    def _collect(self, slot: int) -> AdminCredentials:
        values = {}
        for field in ENV_FIELDS:
            env_var = f"ADMIN{slot}_{field}"
            value = os.environ.get(env_var, "").strip()
            if not value:
                value = self._prompt(slot, field, env_var)
            values[field.lower()] = value

        creds = AdminCredentials(**values)
        try:
            validate_email(creds.email)
        except ValidationError:
            raise CommandError(f"Admin {slot}: {creds.email!r} is not a valid email address.")
        return creds

    def _prompt(self, slot: int, field: str, env_var: str) -> str:
        if not sys.stdin.isatty():
            raise CommandError(
                f"Missing environment variable {env_var} and no interactive terminal available. "
                f"Set ADMIN{slot}_EMAIL, ADMIN{slot}_PASSWORD, ADMIN{slot}_FIRST_NAME and "
                f"ADMIN{slot}_LAST_NAME, then re-run the command."
            )
        label = field.replace("_", " ").lower()
        if field == "PASSWORD":
            value = getpass(f"Admin {slot} {label}: ")
        else:
            value = input(f"Admin {slot} {label}: ").strip()
        if not value:
            raise CommandError(f"Admin {slot}: {label} must not be empty.")
        return value

    def _create_admin(self, slot: int, creds: AdminCredentials) -> None:
        user_model = get_user_model()
        existing = user_model.objects.filter(email__iexact=creds.email).first()
        if existing is not None:
            if existing.is_active and existing.is_staff and existing.is_superuser:
                self.stdout.write(
                    self.style.WARNING(
                        f"Admin {slot}: {creds.email} already exists as an active administrator; skipping."
                    )
                )
                return
            raise CommandError(
                f"Admin {slot}: a non-administrator account already exists for {creds.email}. "
                "Resolve the conflict in Django Admin before re-running."
            )

        probe = user_model(
            email=creds.email, first_name=creds.first_name, last_name=creds.last_name
        )
        try:
            validate_password(creds.password, user=probe)
        except ValidationError as exc:
            raise CommandError(f"Admin {slot}: password rejected: {'; '.join(exc.messages)}")

        user_model.objects.create_superuser(
            email=creds.email,
            password=creds.password,
            first_name=creds.first_name,
            last_name=creds.last_name,
            email_verified=True,
        )
        self.stdout.write(self.style.SUCCESS(f"Admin {slot}: created administrator {creds.email}."))
