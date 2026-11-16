"""Account business rules: registration, email verification, deactivation,
and the last-active-administrator safeguard.
"""

import enum
import logging

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

logger = logging.getLogger(__name__)

EMAIL_VERIFICATION_SALT = "accounts.email-verification"

NAME_NOT_APPROVED_MESSAGE = (
    "Не открихме свободен запис в списъка с одобрени живущи, който да съвпада с "
    "това име. Проверете изписването или се свържете с администратор, ако "
    "смятате, че имате право на достъп."
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
        raise RegistrationError("Вече съществува профил с този имейл адрес.")
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


def send_verification_email(user: User, request: HttpRequest) -> bool:
    """Send the verification link; return False instead of raising on failure.

    A mail outage must not turn an already-saved registration into a server
    error. The failure is logged (without the token) and the caller tells the
    user; an administrator can resend the link from the admin.
    """
    url = request.build_absolute_uri(
        reverse("verify_email", args=[make_verification_token(user)])
    )
    body = render_to_string(
        "accounts/emails/verify_email.txt",
        {"user": user, "verification_url": url, "timeout_hours": settings.EMAIL_VERIFICATION_TIMEOUT // 3600},
    )
    try:
        send_mail(
            "Потвърдете регистрацията си — Пералня на общежитието",
            body,
            None,
            [user.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Verification email could not be sent (user id=%s).", user.pk)
        return False
    return True


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

    with transaction.atomic():
        user = (
            User.objects.select_for_update()
            .filter(pk=data.get("uid"), email=data.get("email"))
            .first()
        )
        if user is None:
            return VerificationResult.INVALID, None
        if user.deactivated_at is not None:
            return VerificationResult.DEACTIVATED, None
        if user.email_verified:
            return VerificationResult.ALREADY_VERIFIED, user

        user.email_verified = True
        user.is_active = True
        user.save(update_fields=["email_verified", "is_active"])

        # Allocate a lockbox PIN atomically with activation (UC-06). Returns
        # None when the inventory is empty; the user is told their PIN is
        # awaiting an administrator. Imported here to avoid an app cycle.
        from access_codes.services import allocate_pin_for_user

        allocate_pin_for_user(user)
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
            "Това е последният активен администратор. Дайте администраторски "
            "права на друг профил, преди да деактивирате, понижите или изтриете този."
        )


def check_admin_delete(user: User) -> None:
    """Raise if deleting `user` would remove the last active administrator."""
    check_admin_change(user, is_active=False, is_staff=False, is_superuser=False)


def finalize_deactivation(user: User, *, actor: User | None = None) -> None:
    """Side effects every deactivation path must apply, exactly once each:

    - the assigned lockbox PIN becomes REVOKE_PENDING (physical removal is
      still a manual admin task on the offline lockbox);
    - pending PIN deliveries are cancelled;
    - future reservations are cancelled and release their weekly quota;
      already-started reservations are untouched.

    Each step is a no-op when already done, so repeated calls are safe. Used
    by `deactivate_user` and by the Django Admin change-form path.
    """
    from access_codes.services import mark_revoke_pending_for_user
    from laundry.services import cancel_future_reservations_for_user

    with transaction.atomic():
        mark_revoke_pending_for_user(user, actor=actor)
        cancel_future_reservations_for_user(user, cancelled_by=actor)


def deactivate_user(user: User, *, actor: User | None = None) -> None:
    """Administratively deactivate an account (login is blocked immediately).

    Atomic and idempotent: marks `deactivated_at` (kept from the first
    deactivation on repeats) so email verification and password reset can
    never reactivate the account, records the responsible administrator,
    flags the assigned lockbox PIN REVOKE_PENDING, cancels pending PIN
    deliveries, and cancels future reservations. The physical PIN keeps
    working until an administrator removes it from the offline lockbox and
    confirms removal in Django Admin.
    """
    check_admin_change(user, is_active=False, is_staff=user.is_staff, is_superuser=user.is_superuser)

    with transaction.atomic():
        user.is_active = False
        if user.deactivated_at is None:
            user.deactivated_at = timezone.now()
            user.deactivated_by = actor
        user.save(update_fields=["is_active", "deactivated_at", "deactivated_by"])
        finalize_deactivation(user, actor=actor)


def revoke_eligibility(entry: ApprovedResident, *, actor: User | None = None) -> None:
    """Mark an approved resident ineligible and safely retire their account.

    If the entry has a registered account, the full deactivation workflow runs
    atomically with the eligibility change: access blocked, future
    reservations cancelled, PIN flagged REVOKE_PENDING, pending deliveries
    cancelled. Historical records are preserved and the entry stays linked to
    the account, so the name cannot be re-claimed by someone else and no
    duplicate account can appear if eligibility is later restored.

    Raises ValidationError if the linked account is the last active
    administrator.
    """
    with transaction.atomic():
        if entry.registered_user is not None:
            deactivate_user(entry.registered_user, actor=actor)
        entry.is_eligible = False
        entry.save()


def restore_eligibility(entry: ApprovedResident) -> None:
    """Re-allow an approved resident. Deliberately does NOT reactivate a
    linked account — that is a separate, explicit administrator step
    (`reactivate_user`), after which a fresh PIN can be allocated."""
    entry.is_eligible = True
    entry.save()


def check_can_reactivate(user: User) -> None:
    """Raise if reactivating `user` would bypass approved-resident eligibility."""
    entry = getattr(user, "approved_resident", None)
    if entry is not None and not entry.is_eligible:
        raise ValidationError(
            "Записът на този живущ в списъка с одобрени е отбелязан като "
            "недопустим. Възстановете допустимостта в списъка с одобрени живущи, "
            "преди да активирате профила отново."
        )


def reactivate_user(user: User) -> None:
    """Clear an administrative deactivation (administrator workflow only).

    - Access returns only if the email was already verified; an unverified
      account stays inactive until the resident completes verification.
    - Refuses to reactivate a resident whose approved-list entry is no longer
      eligible.
    - Deliberately does NOT restore cancelled reservations or the previous
      lockbox PIN: once a PIN enters the revocation process it stays there.
      To give a reactivated resident lockbox access again, an administrator
      assigns a fresh PIN via the "Allocate lockbox PINs" action (audited).
    """
    check_can_reactivate(user)
    user.is_active = user.email_verified
    user.deactivated_at = None
    user.deactivated_by = None
    user.save(update_fields=["is_active", "deactivated_at", "deactivated_by"])
