"""PIN lifecycle services: import, programming confirmation, atomic
allocation, retry-safe email delivery, and revocation tracking (UC-05..UC-08).

The lockbox is offline: nothing here changes the physical device. Status
fields describe what administrators must do (or have confirmed doing) by hand.
"""

from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import QuerySet
from django.template.loader import render_to_string
from django.utils import timezone

from accounts.models import User

from . import crypto
from .models import LockboxPIN, PINAuditLog, PINDelivery


def _audit(
    pin: LockboxPIN,
    action: str,
    *,
    actor: User | None = None,
    target_user: User | None = None,
    detail: str = "",
) -> None:
    PINAuditLog.objects.create(
        pin=pin, action=action, actor=actor, target_user=target_user, detail=detail[:200]
    )


# --- Inventory --------------------------------------------------------------


class DuplicatePINError(ValidationError):
    pass


def add_pin(pin_value: str, *, actor: User | None) -> LockboxPIN:
    """Store one PIN encrypted, as UNPROGRAMMED until hardware is confirmed."""
    pin_value = pin_value.strip()
    crypto.validate_pin_format(pin_value)
    fingerprint = crypto.pin_fingerprint(pin_value)
    if LockboxPIN.objects.filter(pin_fingerprint=fingerprint).exists():
        raise DuplicatePINError("Този ПИН код вече е в наличността.")
    pin = LockboxPIN.objects.create(
        encrypted_pin=crypto.encrypt_pin(pin_value), pin_fingerprint=fingerprint
    )
    _audit(pin, PINAuditLog.Action.IMPORTED, actor=actor)
    return pin


@dataclass
class ImportResult:
    created: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)


def import_pins(lines: list[str], *, actor: User | None) -> ImportResult:
    """Import one PIN per line. Errors reference line numbers, never values."""
    result = ImportResult()
    for line_number, raw in enumerate(lines, start=1):
        value = raw.strip()
        if not value or value.lower() == "pin":  # allow an optional header row
            continue
        try:
            add_pin(value, actor=actor)
            result.created += 1
        except DuplicatePINError:
            result.duplicates += 1
        except ValidationError:
            result.errors.append(f"Ред {line_number}: невалиден формат на ПИН код.")
    return result


def confirm_programmed(pins: QuerySet, *, actor: User) -> int:
    """UNPROGRAMMED -> AVAILABLE after the admin confirms the lockbox accepts
    the code. Only then may it be assigned to a resident."""
    confirmed = 0
    with transaction.atomic():
        for pin in pins.select_for_update().filter(status=LockboxPIN.Status.UNPROGRAMMED):
            pin.status = LockboxPIN.Status.AVAILABLE
            pin.save(update_fields=["status"])
            _audit(pin, PINAuditLog.Action.PROGRAMMED, actor=actor)
            confirmed += 1
    return confirmed


def inventory_counts() -> dict[str, int]:
    counts = dict.fromkeys(LockboxPIN.Status.values, 0)
    for row in LockboxPIN.objects.values("status").order_by():
        counts[row["status"]] += 1
    return counts


# --- Allocation -------------------------------------------------------------


def user_assigned_pin(user: User) -> LockboxPIN | None:
    return LockboxPIN.objects.filter(
        assigned_user=user, status=LockboxPIN.Status.ASSIGNED
    ).first()


def allocate_pin_for_user(user: User, *, actor: User | None = None) -> LockboxPIN | None:
    """Atomically assign one AVAILABLE PIN to an eligible resident.

    Idempotent: an existing assignment is returned unchanged. Concurrency is
    handled by row locks (PostgreSQL), a conditional UPDATE claim, and the
    partial unique constraint on (assigned_user, status=ASSIGNED) as the
    final backstop. Returns None when no PIN is available or the user is not
    eligible; callers surface the "awaiting administrator" message.
    """
    with transaction.atomic():
        # Lock the user row and re-check eligibility inside the transaction so
        # allocation cannot race with a concurrent deactivation: whichever
        # commits first, the loser sees the other's state (PostgreSQL).
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        if (
            not locked_user.is_active
            or locked_user.deactivated_at is not None
            or not locked_user.email_verified
        ):
            return None

        existing = user_assigned_pin(user)
        if existing is not None:
            return existing

        now = timezone.now()
        candidates = LockboxPIN.objects.select_for_update().filter(
            status=LockboxPIN.Status.AVAILABLE
        ).order_by("pk")[:10]
        for candidate in candidates:
            claimed = LockboxPIN.objects.filter(
                pk=candidate.pk,
                status=LockboxPIN.Status.AVAILABLE,
                assigned_user__isnull=True,
            ).update(
                status=LockboxPIN.Status.ASSIGNED, assigned_user=user, assigned_at=now
            )
            if claimed:
                pin = LockboxPIN.objects.get(pk=candidate.pk)
                _audit(pin, PINAuditLog.Action.ASSIGNED, actor=actor, target_user=user)
                delivery, _ = PINDelivery.objects.get_or_create(pin=pin, user=user)
                transaction.on_commit(lambda pk=delivery.pk: deliver_pin_email(pk))
                return pin
        return None


# --- Delivery ---------------------------------------------------------------


def _delivery_allowed(delivery: PINDelivery) -> bool:
    user = delivery.user
    return (
        user.is_active
        and user.email_verified
        and user.deactivated_at is None
        and delivery.pin.status == LockboxPIN.Status.ASSIGNED
        and delivery.pin.assigned_user_id == user.pk
    )


def deliver_pin_email(delivery_id: int, *, actor: User | None = None) -> PINDelivery:
    """Send (or re-send after failure) the PIN email for one delivery record.

    Never sends twice for the same record once SENT, never sends to users who
    are unverified or deactivated, and records every attempt. The email is
    sent outside any caller transaction (allocation schedules this via
    ``transaction.on_commit``).
    """
    delivery = PINDelivery.objects.select_related("pin", "user").get(pk=delivery_id)
    if delivery.status == PINDelivery.Status.SENT:
        return delivery

    if not _delivery_allowed(delivery):
        delivery.status = PINDelivery.Status.CANCELLED
        delivery.last_error = "Получателят или ПИН кодът вече не отговарят на условията за изпращане."
        delivery.save(update_fields=["status", "last_error"])
        _audit(
            delivery.pin,
            PINAuditLog.Action.DELIVERY_CANCELLED,
            actor=actor,
            target_user=delivery.user,
            detail=delivery.last_error,
        )
        return delivery

    delivery.attempts += 1
    delivery.last_attempt_at = timezone.now()
    try:
        body = render_to_string(
            "access_codes/emails/pin_email.txt",
            {"user": delivery.user, "pin": crypto.decrypt_pin(delivery.pin.encrypted_pin)},
        )
        send_mail(
            "Вашият ПИН код за пералнята",
            body,
            None,
            [delivery.user.email],
            fail_silently=False,
        )
    except Exception as exc:  # SMTP/config errors; never contains the PIN.
        delivery.status = PINDelivery.Status.FAILED
        delivery.last_error = f"{type(exc).__name__}: {exc}"[:200]
        delivery.save(update_fields=["status", "attempts", "last_attempt_at", "last_error"])
        _audit(
            delivery.pin,
            PINAuditLog.Action.DELIVERY_FAILED,
            actor=actor,
            target_user=delivery.user,
            detail=type(exc).__name__,
        )
        return delivery

    delivery.status = PINDelivery.Status.SENT
    delivery.sent_at = timezone.now()
    delivery.last_error = ""
    delivery.save(
        update_fields=["status", "attempts", "last_attempt_at", "sent_at", "last_error"]
    )
    _audit(
        delivery.pin,
        PINAuditLog.Action.DELIVERY_SENT,
        actor=actor,
        target_user=delivery.user,
    )
    return delivery


def retry_deliveries(deliveries: QuerySet, *, actor: User) -> tuple[int, int]:
    """Admin-triggered retry for FAILED/PENDING deliveries. Returns (sent, failed)."""
    sent = failed = 0
    for delivery in deliveries.filter(
        status__in=[PINDelivery.Status.FAILED, PINDelivery.Status.PENDING]
    ):
        _audit(
            delivery.pin,
            PINAuditLog.Action.DELIVERY_RETRY,
            actor=actor,
            target_user=delivery.user,
        )
        result = deliver_pin_email(delivery.pk, actor=actor)
        if result.status == PINDelivery.Status.SENT:
            sent += 1
        else:
            failed += 1
    return sent, failed


# --- Revocation -------------------------------------------------------------


def mark_revoke_pending_for_user(user: User, *, actor: User | None = None) -> int:
    """Called when a resident is deactivated: flag their assigned PIN for
    manual removal from the lockbox. The physical code KEEPS WORKING until an
    administrator removes it and confirms below."""
    flagged = 0
    now = timezone.now()
    with transaction.atomic():
        pins = LockboxPIN.objects.select_for_update().filter(
            assigned_user=user, status=LockboxPIN.Status.ASSIGNED
        )
        for pin in pins:
            pin.status = LockboxPIN.Status.REVOKE_PENDING
            pin.revoke_requested_at = now
            pin.save(update_fields=["status", "revoke_requested_at"])
            _audit(pin, PINAuditLog.Action.REVOKE_REQUESTED, actor=actor, target_user=user)
            flagged += 1
        PINDelivery.objects.filter(
            user=user, status=PINDelivery.Status.PENDING
        ).update(status=PINDelivery.Status.CANCELLED, last_error="Потребителят е деактивиран.")
    return flagged


def confirm_physical_removal(pins: QuerySet, *, actor: User) -> int:
    """REVOKE_PENDING -> REVOKED after an administrator has physically deleted
    the code from the lockbox. Terminal: revoked PINs are never auto-reused."""
    confirmed = 0
    now = timezone.now()
    with transaction.atomic():
        for pin in pins.select_for_update().filter(
            status=LockboxPIN.Status.REVOKE_PENDING
        ):
            pin.status = LockboxPIN.Status.REVOKED
            pin.revoke_confirmed_at = now
            pin.save(update_fields=["status", "revoke_confirmed_at"])
            _audit(
                pin,
                PINAuditLog.Action.REVOKE_CONFIRMED,
                actor=actor,
                target_user=pin.assigned_user,
            )
            confirmed += 1
    return confirmed


def users_awaiting_pin() -> QuerySet:
    """Verified, active residents with no currently assigned PIN (for admins)."""
    return User.objects.filter(
        is_active=True, email_verified=True, deactivated_at__isnull=True
    ).exclude(lockbox_pins__status=LockboxPIN.Status.ASSIGNED)


def reveal_pin_for(user: User) -> str | None:
    """Plaintext PIN for the user's own assigned PIN, or None. Callers must
    only ever pass the authenticated request user."""
    pin = user_assigned_pin(user)
    if pin is None:
        return None
    return crypto.decrypt_pin(pin.encrypted_pin)
