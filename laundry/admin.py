from django.contrib import admin, messages

from . import services
from .models import MachineSet, Reservation


@admin.register(MachineSet)
class MachineSetAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active"]
    list_filter = ["is_active"]

    def has_delete_permission(self, request, obj=None):
        # The three seeded sets are fixed; deactivate instead of deleting.
        return False


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    """Read-only reservation management: view, search, filter, and cancel.

    Creating or editing reservations here would bypass the booking rules
    (BR-08), so both are disabled; admins book through the normal schedule
    and cancel through the action below.
    """

    list_display = [
        "booking_date",
        "slot_number",
        "machine_set",
        "resident",
        "status",
        "counts_toward_quota",
        "cancelled_by",
    ]
    list_filter = ["status", "machine_set", "booking_date"]
    search_fields = ["user__first_name", "user__last_name", "user__email"]
    date_hierarchy = "booking_date"
    actions = ["cancel_reservations"]

    @admin.display(description="Resident", ordering="user__last_name")
    def resident(self, obj: Reservation) -> str:
        return f"{obj.user.first_name} {obj.user.last_name}"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # Change pages are view-only (all fields readonly); edits are disallowed.
        return False

    def has_delete_permission(self, request, obj=None):
        # Reservations are history; cancel instead of deleting.
        return False

    @admin.action(description="Cancel selected reservations")
    def cancel_reservations(self, request, queryset):
        cancelled = 0
        for reservation in queryset:
            try:
                services.cancel_reservation(
                    reservation=reservation, cancelled_by=request.user
                )
                cancelled += 1
            except services.BookingError as exc:
                self.message_user(
                    request, f"Reservation {reservation.pk}: {exc}", messages.ERROR
                )
        if cancelled:
            self.message_user(
                request, f"Cancelled {cancelled} reservation(s).", messages.SUCCESS
            )
