import datetime as dt

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView, View

from . import services
from .forms import BookingForm
from .models import MachineSet, Reservation


def _schedule_url(week: str) -> str:
    url = reverse("schedule")
    return f"{url}?week=next" if week == "next" else url


class ScheduleView(LoginRequiredMixin, TemplateView):
    """Weekly grid: 7 day cards x 5 slots x 3 machine sets (UC-09)."""

    template_name = "laundry/schedule.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        week = "next" if self.request.GET.get("week") == "next" else "current"
        now = timezone.now()
        today = timezone.localdate(now)
        monday, _ = services.week_bounds(today)
        if week == "next":
            monday += dt.timedelta(days=7)
        days = [monday + dt.timedelta(days=offset) for offset in range(7)]

        machine_sets = list(MachineSet.objects.filter(is_active=True))
        reservations = Reservation.objects.filter(
            status=Reservation.Status.ACTIVE,
            booking_date__range=(days[0], days[-1]),
        ).select_related("user", "machine_set")
        occupied = {
            (r.booking_date, r.slot_number, r.machine_set_id): r for r in reservations
        }

        day_cards = []
        for day in days:
            slots = []
            for slot_number in services.SLOT_TIMES:
                bookable = services.slot_start(day, slot_number) > now
                cells = []
                for machine_set in machine_sets:
                    reservation = occupied.get((day, slot_number, machine_set.pk))
                    cells.append(
                        {
                            "machine_set": machine_set,
                            "reservation": reservation,
                            "is_mine": reservation is not None
                            and reservation.user_id == self.request.user.pk,
                            "bookable": bookable and reservation is None,
                        }
                    )
                slots.append(
                    {
                        "number": slot_number,
                        "label": services.slot_label(slot_number),
                        "cells": cells,
                    }
                )
            day_cards.append({"date": day, "is_today": day == today, "slots": slots})

        context.update(
            {
                "week": week,
                "day_cards": day_cards,
                "machine_sets": machine_sets,
            }
        )
        return context


class BookSlotView(LoginRequiredMixin, View):
    """POST-only booking endpoint; redirects back to the schedule (UC-10)."""

    def post(self, request):
        week = request.POST.get("week", "current")
        form = BookingForm(request.POST)
        if not form.is_valid():
            messages.error(request, "That booking request was not valid. Please try again.")
            return redirect(_schedule_url(week))
        try:
            reservation = services.book_slot(
                user=request.user,
                machine_set=form.cleaned_data["machine_set"],
                booking_date=form.cleaned_data["booking_date"],
                slot_number=form.cleaned_data["slot_number"],
            )
        except services.BookingError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"Booked {reservation.machine_set} on {reservation.booking_date:%A %d %b} "
                f"at {services.slot_label(reservation.slot_number)}.",
            )
        return redirect(_schedule_url(week))


class CancelReservationView(LoginRequiredMixin, View):
    """GET shows a confirmation page; POST cancels (UC-11/UC-12)."""

    def _get_reservation(self, request, pk):
        if request.user.is_admin:
            return get_object_or_404(Reservation, pk=pk)
        return get_object_or_404(Reservation, pk=pk, user=request.user)

    def get(self, request, pk):
        reservation = self._get_reservation(request, pk)
        return render(
            request,
            "laundry/cancel_confirm.html",
            {
                "reservation": reservation,
                "slot_label": services.slot_label(reservation.slot_number),
                "next": request.GET.get("next", ""),
            },
        )

    def post(self, request, pk):
        reservation = self._get_reservation(request, pk)
        try:
            services.cancel_reservation(reservation=reservation, cancelled_by=request.user)
        except services.BookingError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"Cancelled {reservation.machine_set} on "
                f"{reservation.booking_date:%A %d %b} "
                f"at {services.slot_label(reservation.slot_number)}.",
            )
        next_url = request.POST.get("next", "")
        if next_url == "reservations":
            return redirect("my_reservations")
        return redirect(_schedule_url(next_url if next_url == "next" else "current"))


class MyReservationsView(LoginRequiredMixin, TemplateView):
    template_name = "laundry/my_reservations.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        mine = (
            Reservation.objects.filter(user=self.request.user)
            .select_related("machine_set")
            .order_by("booking_date", "slot_number")
        )
        upcoming = mine.filter(status=Reservation.Status.ACTIVE, booking_date__gte=today)
        history = mine.exclude(pk__in=upcoming.values("pk")).order_by(
            "-booking_date", "-slot_number"
        )
        context.update(
            {
                "upcoming": self._annotate(upcoming),
                "history": self._annotate(history),
            }
        )
        return context

    @staticmethod
    def _annotate(reservations):
        return [
            {"reservation": r, "slot_label": services.slot_label(r.slot_number)}
            for r in reservations
        ]
