from django import forms

from .models import MachineSet


class BookingForm(forms.Form):
    """Validates the raw POST data for a booking; the rules live in services."""

    machine_set = forms.ModelChoiceField(queryset=MachineSet.objects.filter(is_active=True))
    booking_date = forms.DateField()
    slot_number = forms.IntegerField(min_value=1, max_value=5)
