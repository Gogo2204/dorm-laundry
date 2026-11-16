from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.generic import FormView, TemplateView, View

from . import services
from .forms import RegistrationForm

VERIFICATION_MESSAGES = {
    services.VerificationResult.VERIFIED: (
        True,
        "Имейл адресът ви е потвърден и профилът ви е активен. Вече можете да влезете.",
    ),
    services.VerificationResult.ALREADY_VERIFIED: (
        True,
        "Този имейл адрес вече е потвърден. Можете да влезете.",
    ),
    services.VerificationResult.EXPIRED: (
        False,
        "Линкът за потвърждение е изтекъл. Моля, регистрирайте се отново или се свържете с администратор.",
    ),
    services.VerificationResult.INVALID: (
        False,
        "Този линк за потвърждение не е валиден.",
    ),
    services.VerificationResult.DEACTIVATED: (
        False,
        "Този профил не може да бъде активиран. Моля, свържете се с администратор.",
    ),
}


class RegisterView(FormView):
    template_name = "accounts/register.html"
    form_class = RegistrationForm
    success_url = reverse_lazy("registration_pending")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return HttpResponseRedirect(reverse_lazy("schedule"))
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
        result, user = services.verify_email_token(kwargs["token"])
        context["success"], context["message"] = VERIFICATION_MESSAGES[result]
        if result == services.VerificationResult.VERIFIED:
            from access_codes.services import user_assigned_pin

            if user_assigned_pin(user) is not None:
                context["pin_note"] = (
                    "Вашият личен ПИН код за кутията с ключ беше изпратен на имейла ви."
                )
            else:
                context["pin_note"] = (
                    "Вашият ПИН код за кутията с ключ очаква назначаване от "
                    "администратор; ще го получите по имейл веднага щом има свободен."
                )
        return context


def _account_context(user) -> dict:
    from access_codes.services import user_assigned_pin

    return {"assigned_pin": user_assigned_pin(user)}


class AccountView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/account.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_account_context(self.request.user))
        return context


@method_decorator(never_cache, name="dispatch")
class RevealPINView(LoginRequiredMixin, View):
    """Reveal the authenticated user's OWN PIN via an explicit POST.

    POST keeps the PIN out of URLs and of normally-cached GET responses;
    ``never_cache`` marks the response no-store. The PIN appears only in this
    response, never in the initial account page HTML.
    """

    def post(self, request):
        from access_codes.services import reveal_pin_for

        context = _account_context(request.user)
        context["revealed_pin"] = reveal_pin_for(request.user)
        return render(request, "accounts/account.html", context)
