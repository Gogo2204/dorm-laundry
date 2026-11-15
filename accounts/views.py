from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy
from django.views.generic import FormView, TemplateView

from . import services
from .forms import RegistrationForm

VERIFICATION_MESSAGES = {
    services.VerificationResult.VERIFIED: (
        True,
        "Your email address is confirmed and your account is active. You can now log in.",
    ),
    services.VerificationResult.ALREADY_VERIFIED: (
        True,
        "This email address was already confirmed. You can log in.",
    ),
    services.VerificationResult.EXPIRED: (
        False,
        "This verification link has expired. Please register again or contact an administrator.",
    ),
    services.VerificationResult.INVALID: (
        False,
        "This verification link is not valid.",
    ),
    services.VerificationResult.DEACTIVATED: (
        False,
        "This account cannot be activated. Please contact an administrator.",
    ),
}


class RegisterView(FormView):
    template_name = "accounts/register.html"
    form_class = RegistrationForm
    success_url = reverse_lazy("registration_pending")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return HttpResponseRedirect(reverse_lazy("home"))
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            user = services.register_resident(
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password1"],
            )
        except services.RegistrationError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        services.send_verification_email(user, self.request)
        return super().form_valid(form)


class RegistrationPendingView(TemplateView):
    template_name = "accounts/registration_pending.html"


class VerifyEmailView(TemplateView):
    template_name = "accounts/verify_email.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        result, _user = services.verify_email_token(kwargs["token"])
        context["success"], context["message"] = VERIFICATION_MESSAGES[result]
        return context


class AccountView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/account.html"
