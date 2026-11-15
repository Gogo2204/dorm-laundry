"""Account business rules: registration, email verification, deactivation,
and the last-active-administrator safeguard.
"""

import enum

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import ApprovedResident, User, normalize_name

EMAIL_VERIFICATION_SALT = "accounts.email-verification"

NAME_NOT_APPROVED_MESSAGE = (
    "We could not match your name to an unclaimed entry on the approved residents "
    "list. Check the spelling, or contact an administrator if you believe you are "
    "eligible."
)


class RegistrationError(Exception):
    """A registration rule was violated; the message is safe to show the user."""


def find_claimable_resident(first_name: str, last_name: str) -> ApprovedResident | None:
    """Return the eligible, unclaimed approved entry matching a normalized name."""
    return ApprovedResident.objects.filter(
        normalized_first_name=normalize_name(first_name),
        normalized_last_name=normalize_name(last_name),
        is_eligible=True,
        registered_user__isnull=True,
    ).first()


def register_resident(*, first_name: str, last_name: str, email: str, password: str) -> User:
    """Create a pending (inactive, unverified) account claiming an approved name.

    The claim is a conditional UPDATE on the approved entry, so two concurrent
    registrations for the same name cannot both succeed: the second one finds
    the entry already claimed, and the whole transaction rolls back.
    """
    entry = find_claimable_resident(first_name, last_name)
    if entry is None:
        raise RegistrationError(NAME_NOT_APPROVED_MESSAGE)

    try:
        with transaction.atomic():
            user = User.objects.create_user(
                email=email,
                password=password,
                # Store the canonical approved spelling, not the applicant's input.
                first_name=entry.first_name,
                last_name=entry.last_name,
                is_active=False,
                email_verified=False,
            )
            claimed = ApprovedResident.objects.filter(
                pk=entry.pk, is_eligible=True, registered_user__isnull=True
            ).update(registered_user=user)
            if not claimed:
                raise RegistrationError(NAME_NOT_APPROVED_MESSAGE)
    except IntegrityError:
        raise RegistrationError("An account with this email address already exists.")
    return user


# --- Email verification -----------------------------------------------------


class VerificationResult(enum.Enum):
    VERIFIED = "verified"
    ALREADY_VERIFIED = "already_verified"
    INVALID = "invalid"
    EXPIRED = "expired"
    DEACTIVATED = "deactivated"


def make_verification_token(user: User) -> str:
    """Signed, timestamped token bound to the user's id and current email."""
    return signing.dumps({"uid": user.pk, "email": user.email}, salt=EMAIL_VERIFICATION_SALT)


def send_verification_email(user: User, request: HttpRequest) -> None:
    url = request.build_absolute_uri(
        reverse("verify_email", args=[make_verification_token(user)])
    )
    body = render_to_string(
        "accounts/emails/verify_email.txt",
        {"user": user, "verification_url": url, "timeout_hours": settings.EMAIL_VERIFICATION_TIMEOUT // 3600},
    )
    send_mail("Confirm your Dorm Laundry registration", body, None, [user.email])


def verify_email_token(token: str) -> tuple[VerificationResult, User | None]:
    """Validate a verification token and activate the account if appropriate.

    Idempotent: a token that already did its work reports ALREADY_VERIFIED and
    changes nothing. Never reactivates an administratively deactivated account.
    """
    try:
        data = signing.loads(
            token, salt=EMAIL_VERIFICATION_SALT, max_age=settings.EMAIL_VERIFICATION_TIMEOUT
        )
    except signing.SignatureExpired:
        return VerificationResult.EXPIRED, None
    except signing.BadSignature:
        return VerificationResult.INVALID, None

    user = User.objects.filter(pk=data.get("uid"), email=data.get("email")).first()
    if user is None:
        return VerificationResult.INVALID, None
    if user.deactivated_at is not None:
        return VerificationResult.DEACTIVATED, None
    if user.email_verified:
        return VerificationResult.ALREADY_VERIFIED, user

    user.email_verified = True
    user.is_active = True
    user.save(update_fields=["email_verified", "is_active"])
    return VerificationResult.VERIFIED, user


# --- Deactivation and the last-admin safeguard ------------------------------


def other_active_admin_exists(user: User) -> bool:
    return (
        get_user_model()
        .objects.filter(is_active=True, is_staff=True, is_superuser=True)
        .exclude(pk=user.pk)
        .exists()
    )


def check_admin_change(user: User, *, is_active: bool, is_staff: bool, is_superuser: bool) -> None:
    """Raise if applying the given flags to `user` would remove the last admin.

    `user` is compared against its current database state, so callers can pass
    a mutated instance safely.
    """
    current = get_user_model().objects.filter(pk=user.pk).first()
    if current is None or not current.is_admin:
        return
    if (is_active and is_staff and is_superuser):
        return
    if not other_active_admin_exists(user):
        raise ValidationError(
            "This is the last active administrator. Grant administrator access to "
            "another account before deactivating, demoting, or deleting this one."
        )


def check_admin_delete(user: User) -> None:
    """Raise if deleting `user` would remove the last active administrator."""
    check_admin_change(user, is_active=False, is_staff=False, is_superuser=False)


def deactivate_user(user: User) -> None:
    """Administratively deactivate an account (login is blocked immediately).

    Marks `deactivated_at` so email verification and password reset can never
    reactivate the account. PIN revocation marking and reservation cancellation
    are added in later phases.
    """
    check_admin_change(user, is_active=False, is_staff=user.is_staff, is_superuser=user.is_superuser)
    user.is_active = False
    user.deactivated_at = timezone.now()
    user.save(update_fields=["is_active", "deactivated_at"])


def reactivate_user(user: User) -> None:
    """Clear an administrative deactivation; verification state is unchanged."""
    user.is_active = user.email_verified
    user.deactivated_at = None
    user.save(update_fields=["is_active", "deactivated_at"])
