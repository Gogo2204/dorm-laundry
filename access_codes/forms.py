from django import forms
from django.core.exceptions import ImproperlyConfigured, ValidationError

from . import crypto
from .models import LockboxPIN


class LockboxPINAddForm(forms.ModelForm):
    """Secure single-PIN entry: password-style input, never redisplayed."""

    pin_value = forms.CharField(
        label="PIN",
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
        help_text="Accepted format: 4–8 digits by default. The value is encrypted "
        "at rest and will not be shown again.",
    )

    class Meta:
        model = LockboxPIN
        fields: list[str] = []

    def clean_pin_value(self) -> str:
        value = self.cleaned_data["pin_value"].strip()
        try:
            crypto.validate_pin_format(value)
            if LockboxPIN.objects.filter(
                pin_fingerprint=crypto.pin_fingerprint(value)
            ).exists():
                raise ValidationError("This PIN is already in the inventory.")
        except ImproperlyConfigured as exc:
            raise ValidationError(str(exc))
        return value


class PINImportForm(forms.Form):
    csv_file = forms.FileField(
        label="CSV file",
        help_text='One PIN per line. An optional "pin" header row is allowed. '
        "Values are encrypted at rest and never echoed back.",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,.txt"}),
    )

    def clean_csv_file(self):
        uploaded = self.cleaned_data["csv_file"]
        try:
            self.cleaned_data["lines"] = uploaded.read().decode("utf-8-sig").splitlines()
        except UnicodeDecodeError:
            raise ValidationError("The file is not valid UTF-8 text.")
        return uploaded
