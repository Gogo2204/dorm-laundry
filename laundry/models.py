from django.conf import settings
from django.db import models


class MachineSet(models.Model):
    """One independently bookable washer/dryer pair. Exactly three are seeded."""

    name = models.CharField(max_length=50, unique=True)
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive sets are hidden from the schedule and cannot be booked.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Reservation(models.Model):
    """A booking of one machine set for one fixed slot on one booking date.

    The booking date is the local date the slot starts; slot 5 (22:00-02:00)
    ends on the following day but belongs to its start date.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        CANCELLED = "CANCELLED", "Cancelled"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reservations"
    )
    machine_set = models.ForeignKey(
        MachineSet, on_delete=models.PROTECT, related_name="reservations"
    )
    booking_date = models.DateField()
    slot_number = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    counts_toward_quota = models.BooleanField(
        default=True,
        help_text=(
            "Active reservations and reservations cancelled after their slot "
            "started count toward the two-per-week limit; cancelling before "
            "the start releases the quota."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cancelled_reservations",
    )

    class Meta:
        ordering = ["booking_date", "slot_number", "machine_set_id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(slot_number__gte=1, slot_number__lte=5),
                name="reservation_slot_number_1_to_5",
            ),
            # One active reservation per machine set and slot (BR-04/BR-10).
            models.UniqueConstraint(
                fields=["machine_set", "booking_date", "slot_number"],
                condition=models.Q(status="ACTIVE"),
                name="unique_active_machine_slot",
                violation_error_message="This machine set is already booked for that slot.",
            ),
            # One active reservation per resident and slot across all sets (BR-03).
            models.UniqueConstraint(
                fields=["user", "booking_date", "slot_number"],
                condition=models.Q(status="ACTIVE"),
                name="unique_active_user_slot",
                violation_error_message="You already have a reservation for that slot.",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.machine_set} on {self.booking_date} slot {self.slot_number} "
            f"({self.get_status_display()})"
        )

    @property
    def is_active_status(self) -> bool:
        return self.status == self.Status.ACTIVE
