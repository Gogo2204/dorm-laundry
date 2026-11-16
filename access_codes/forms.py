from django import forms
from django.core.exceptions import ImproperlyConfigured, ValidationError

from . import crypto
from .models import LockboxPIN


class LockboxPINAddForm(forms.ModelForm):
    """Secure single-PIN entry: password-style input, never redisplayed."""

    pin_value = forms.CharField(
        label="ПИН код",
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
        help_text="Приет формат: по подразбиране 4–8 цифри. Стойността се "
        "съхранява криптирана и няма да бъде показана отново.",
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
                raise ValidationError("Този ПИН код вече е в наличността.")
        except ImproperlyConfigured as exc:
            raise ValidationError(str(exc))
        return value


class PINImportForm(forms.Form):
    csv_file = forms.FileField(
        label="CSV файл",
        help_text='По един ПИН код на ред. Заглавен ред "pin" е незадължителен. '
        "Стойностите се съхраняват криптирани и никога не се показват обратно.",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,.txt"}),
    )

    def clean_csv_file(self):
        uploaded = self.cleaned_data["csv_file"]
        try:
            self.cleaned_data["lines"] = uploaded.read().decode("utf-8-sig").splitlines()
        except UnicodeDecodeError:
            raise ValidationError("Файлът не е валиден UTF-8 текст.")
        return uploaded
