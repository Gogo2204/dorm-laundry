from django.conf import settings
from django.db import models


class LockboxPIN(models.Model):
    """One preconfigured PIN for the shared offline lockbox.

    Status lifecycle (the application NEVER changes the physical lockbox;
    every hardware step is a manual administrator action):

        UNPROGRAMMED --(admin confirms it is programmed & working)--> AVAILABLE
        AVAILABLE    --(atomic allocation after email verification)--> ASSIGNED
        ASSIGNED     --(resident deactivated)--> REVOKE_PENDING
        REVOKE_PENDING --(admin confirms physical removal)--> REVOKED

    REVOKED is terminal: revoked PINs are never automatically recycled.
    Reusing one would require a new, explicitly audited reprogramming
    workflow; the recommended practice is to retire old codes instead.
    """

    class Status(models.TextChoices):
        UNPROGRAMMED = "UNPROGRAMMED", "Unprogrammed (not yet in lockbox)"
        AVAILABLE = "AVAILABLE", "Available"
        ASSIGNED = "ASSIGNED", "Assigned"
        REVOKE_PENDING = "REVOKE_PENDING", "Revoke pending (remove from lockbox!)"
        REVOKED = "REVOKED", "Revoked (physically removed)"

    encrypted_pin = models.TextField(editable=False)
    pin_fingerprint = models.CharField(max_length=64, unique=True, editable=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.UNPROGRAMMED
    )
    assigned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="lockbox_pins",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    revoke_requested_at = models.DateTimeField(null=True, blank=True)
    revoke_confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Lockbox PIN"
        ordering = ["pk"]
        constraints = [
            # A resident can hold at most one currently-assigned PIN; older
            # REVOKE_PENDING/REVOKED records keep their user link as history.
            models.UniqueConstraint(
                fields=["assigned_user"],
                condition=models.Q(status="ASSIGNED"),
                name="unique_assigned_pin_per_user",
                violation_error_message="This resident already has an assigned PIN.",
            ),
        ]

    def __str__(self) -> str:
        # Never include PIN material.
        return f"PIN #{self.pk} ({self.get_status_display()})"


class PINDelivery(models.Model):
    """Persistent outbox for PIN emails, so failures and restarts never lose
    a delivery request and retries never re-allocate PINs."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    pin = models.ForeignKey(LockboxPIN, on_delete=models.PROTECT, related_name="deliveries")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pin_deliveries"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "PIN delivery"
        verbose_name_plural = "PIN deliveries"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Delivery #{self.pk} for PIN #{self.pin_id} ({self.get_status_display()})"


class PINAuditLog(models.Model):
    """Append-only audit trail for sensitive PIN operations. Never contains
    plaintext PINs or key material."""

    class Action(models.TextChoices):
        IMPORTED = "IMPORTED", "PIN imported"
        PROGRAMMED = "PROGRAMMED", "Programming confirmed"
        ASSIGNED = "ASSIGNED", "Assigned to resident"
        DELIVERY_SENT = "DELIVERY_SENT", "Delivery email sent"
        DELIVERY_FAILED = "DELIVERY_FAILED", "Delivery email failed"
        DELIVERY_CANCELLED = "DELIVERY_CANCELLED", "Delivery cancelled"
        DELIVERY_RETRY = "DELIVERY_RETRY", "Delivery retry requested"
        REVOKE_REQUESTED = "REVOKE_REQUESTED", "Physical revocation requested"
        REVOKE_CONFIRMED = "REVOKE_CONFIRMED", "Physical removal confirmed"

    pin = models.ForeignKey(LockboxPIN, on_delete=models.PROTECT, related_name="audit_log")
    action = models.CharField(max_length=20, choices=Action.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pin_audit_entries",
        help_text="Administrator responsible; empty for automatic system actions.",
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    detail = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "PIN audit entry"
        verbose_name_plural = "PIN audit entries"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_action_display()} — PIN #{self.pin_id}"
