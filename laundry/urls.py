from django.urls import path

from . import views

urlpatterns = [
    path("schedule/", views.ScheduleView.as_view(), name="schedule"),
    path("schedule/book/", views.BookSlotView.as_view(), name="book_slot"),
    path("reservations/", views.MyReservationsView.as_view(), name="my_reservations"),
    path(
        "reservations/<int:pk>/cancel/",
        views.CancelReservationView.as_view(),
        name="cancel_reservation",
    ),
]
