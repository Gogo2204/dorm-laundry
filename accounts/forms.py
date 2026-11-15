from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from . import services


class BootstrapFormMixin:
    """Apply Bootstrap classes to all widgets so templates stay simple."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = "form-check-input" if isinstance(field.widget, forms.CheckboxInput) else "form-control"
            field.widget.attrs.setdefault("class", css)


class RegistrationForm(BootstrapFormMixin, forms.Form):
    first_name = forms.CharField(label="First name", max_length=150)
    last_name = forms.CharField(label="Last name", max_length=150)
    email = forms.EmailField(label="Email address")
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput)

    def clean_email(self) -> str:
        email = self.cleaned_data["email"].lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email address already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        password1, password2 = cleaned.get("password1"), cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The two password fields do not match.")
        elif password1:
            probe = get_user_model()(
                email=cleaned.get("email", ""),
                first_name=cleaned.get("first_name", ""),
                last_name=cleaned.get("last_name", ""),
            )
            try:
                validate_password(password1, user=probe)
            except ValidationError as exc:
                self.add_error("password1", exc)

        first_name, last_name = cleaned.get("first_name"), cleaned.get("last_name")
        if first_name and last_name and not services.find_claimable_resident(first_name, last_name):
            raise ValidationError(services.NAME_NOT_APPROVED_MESSAGE)
        return cleaned


class BootstrapAuthenticationForm(BootstrapFormMixin, AuthenticationForm):
    pass


class BootstrapPasswordResetForm(BootstrapFormMixin, PasswordResetForm):
    pass


class BootstrapSetPasswordForm(BootstrapFormMixin, SetPasswordForm):
    pass


class ApprovedResidentCSVForm(forms.Form):
    """Upload form for the Django Admin CSV import (stdlib csv only)."""

    csv_file = forms.FileField(
        label="CSV file",
        help_text='Two columns: first name, last name. A header row "first_name,last_name" is optional.',
        widget=forms.ClearableFileInput(attrs={"accept": ".csv"}),
    )
