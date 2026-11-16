"""Booking business rules (BR-01..BR-10) and slot/week calculations.

All datetime math is timezone-aware in the configured local timezone, so
daylight-saving transitions and the midnight-crossing slot 5 behave correctly.
"""

import datetime as dt

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import User

from .models import MachineSet, Reservation

# Slot number -> (start hour, end hour, end day offset). Slot 5 ends next day.
SLOT_TIMES: dict[int, tuple[int, int, int]] = {
    1: (6, 10, 0),
    2: (10, 14, 0),
    3: (14, 18, 0),
    4: (18, 22, 0),
    5: (22, 2, 1),
}

WEEKLY_QUOTA = 2


class BookingError(Exception):
    """A booking rule was violated; the message is safe to show the user."""


def slot_start(booking_date: dt.date, slot_number: int) -> dt.datetime:
    """Timezone-aware local start of a slot on its booking date."""
    hour = SLOT_TIMES[slot_number][0]
    naive = dt.datetime.combine(booking_date, dt.time(hour=hour))
    return timezone.make_aware(naive, timezone.get_current_timezone())


def slot_end(booking_date: dt.date, slot_number: int) -> dt.datetime:
    """Timezone-aware local end of a slot; slot 5 ends 02:00 the next day."""
    _, end_hour, day_offset = SLOT_TIMES[slot_number]
    end_date = booking_date + dt.timedelta(days=day_offset)
    naive = dt.datetime.combine(end_date, dt.time(hour=end_hour))
    return timezone.make_aware(naive, timezone.get_current_timezone())


def slot_label(slot_number: int) -> str:
    """Visible label only — slot 5 still *ends* at 02:00 on the next day
    (see slot_end); the overnight carry is intentionally not shown."""
    start_hour, end_hour, _ = SLOT_TIMES[slot_number]
    return f"{start_hour:02d}:00–{end_hour:02d}:00"


def week_bounds(day: dt.date) -> tuple[dt.date, dt.date]:
    """Monday..Sunday of the week containing `day`."""
    monday = day - dt.timedelta(days=day.weekday())
    return monday, monday + dt.timedelta(days=6)


def booking_horizon(today: dt.date) -> tuple[dt.date, dt.date]:
    """First and last bookable date: current week Monday to next week Sunday."""
    monday, _ = week_bounds(today)
    return monday, monday + dt.timedelta(days=13)


def quota_used(user: User, booking_date: dt.date) -> int:
    """Counted reservations in the Monday-Sunday week of `booking_date` (BR-01, BR-07)."""
    monday, sunday = week_bounds(booking_date)
    return Reservation.objects.filter(
        user=user,
        booking_date__range=(monday, sunday),
        counts_toward_quota=True,
    ).count()


def book_slot(
    *, user: User, machine_set: MachineSet, booking_date: dt.date, slot_number: int
) -> Reservation:
    """Create a reservation, enforcing every booking rule.

    Concurrency (BR-10): the user's row is locked for the duration of the
    transaction (PostgreSQL), serialising same-user bookings so the weekly
    quota and adjacency checks cannot be raced. Cross-user conflicts on the
    same machine slot are prevented by the conditional unique constraints,
    whose violation is translated into a friendly error.
    """
    if slot_number not in SLOT_TIMES:
        raise BookingError("Изберете валиден времеви интервал.")
    if not machine_set.is_active:
        raise BookingError("Този комплект в момента не е достъпен за резервации.")

    now = timezone.now()
    today = timezone.localdate(now)
    first_day, last_day = booking_horizon(today)
    if not (first_day <= booking_date <= last_day):
        raise BookingError("Можете да резервирате само за текущата или следващата седмица.")
    if slot_start(booking_date, slot_number) <= now:
        raise BookingError("Този интервал вече е започнал и не може да бъде резервиран.")

    try:
        with transaction.atomic():
            # Serialise bookings per user (no-op on SQLite, row lock on PostgreSQL).
            locked_user = User.objects.select_for_update().get(pk=user.pk)
            if not locked_user.is_active or locked_user.deactivated_at is not None:
                raise BookingError("Профилът ви няма право да прави резервации.")

            active_same_day = Reservation.objects.filter(
                user=user,
                booking_date=booking_date,
                status=Reservation.Status.ACTIVE,
            ).values_list("slot_number", flat=True)
            if slot_number in active_same_day:
                raise BookingError(
                    "Вече имате резервация за този интервал на друг комплект."
                )
            if {slot_number - 1, slot_number + 1} & set(active_same_day):
                raise BookingError(
                    "Не можете да резервирате два последователни интервала в един и същи ден. "
                    "Оставете поне един свободен интервал между резервациите си."
                )
            if quota_used(user, booking_date) >= WEEKLY_QUOTA:
                raise BookingError(
                    f"Достигнали сте лимита от {WEEKLY_QUOTA} резервации за тази седмица."
                )
            if Reservation.objects.filter(
                machine_set=machine_set,
                booking_date=booking_date,
                slot_number=slot_number,
                status=Reservation.Status.ACTIVE,
            ).exists():
                raise BookingError("Този комплект вече е зает за избрания интервал.")

            return Reservation.objects.create(
                user=user,
                machine_set=machine_set,
                booking_date=booking_date,
                slot_number=slot_number,
            )
    except IntegrityError:
        # A concurrent request won the race; the unique constraint held.
        raise BookingError("Този интервал току-що беше зает. Моля, изберете друг.")


def cancel_reservation(*, reservation: Reservation, cancelled_by: User) -> Reservation:
    """Cancel a reservation (BR-07/BR-08).

    Residents may cancel their own reservations at any time; administrators
    may cancel any reservation. Cancelling before the slot starts releases
    the weekly quota; cancelling after it started keeps it counted. The row
    is kept for history.
    """
    if reservation.user_id != cancelled_by.pk and not cancelled_by.is_admin:
        raise BookingError("Можете да отказвате само собствените си резервации.")

    with transaction.atomic():
        locked = (
            Reservation.objects.select_for_update()
            .select_related("machine_set")
            .get(pk=reservation.pk)
        )
        if locked.status != Reservation.Status.ACTIVE:
            raise BookingError("Тази резервация вече е отказана.")

        now = timezone.now()
        locked.status = Reservation.Status.CANCELLED
        locked.cancelled_at = now
        locked.cancelled_by = cancelled_by
        locked.counts_toward_quota = slot_start(locked.booking_date, locked.slot_number) <= now
        locked.save(
            update_fields=["status", "cancelled_at", "cancelled_by", "counts_toward_quota"]
        )
        return locked


def cancel_future_reservations_for_user(user: User, *, cancelled_by: User | None = None) -> int:
    """Cancel every ACTIVE reservation of `user` that has not started yet.

    Used by the account-deactivation workflow (UC-08): future reservations are
    cancelled and release their weekly quota (`counts_toward_quota=False`);
    reservations that have already started are left untouched and keep their
    existing quota behaviour. Rows are never deleted. Returns the number of
    reservations cancelled; calling it again is a no-op.
    """
    now = timezone.now()
    today = timezone.localdate(now)
    cancelled = 0
    with transaction.atomic():
        candidates = Reservation.objects.select_for_update().filter(
            user=user,
            status=Reservation.Status.ACTIVE,
            booking_date__gte=today,
        )
        for reservation in candidates:
            if slot_start(reservation.booking_date, reservation.slot_number) <= now:
                continue  # already started; keep it
            reservation.status = Reservation.Status.CANCELLED
            reservation.cancelled_at = now
            reservation.cancelled_by = cancelled_by
            reservation.counts_toward_quota = False
            reservation.save(
                update_fields=[
                    "status",
                    "cancelled_at",
                    "cancelled_by",
                    "counts_toward_quota",
                ]
            )
            cancelled += 1
    return cancelled
