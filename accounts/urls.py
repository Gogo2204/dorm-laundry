from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import (
    BootstrapAuthenticationForm,
    BootstrapPasswordResetForm,
    BootstrapSetPasswordForm,
)

urlpatterns = [
    path("register/", views.RegisterView.as_view(), name="register"),
    path("register/pending/", views.RegistrationPendingView.as_view(), name="registration_pending"),
    path("verify/<str:token>/", views.VerifyEmailView.as_view(), name="verify_email"),
    path("account/", views.AccountView.as_view(), name="account"),
    path("account/pin/reveal/", views.RevealPINView.as_view(), name="reveal_pin"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="accounts/login.html",
            authentication_form=BootstrapAuthenticationForm,
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="accounts/password_reset_form.html",
            email_template_name="accounts/emails/password_reset_email.txt",
            subject_template_name="accounts/emails/password_reset_subject.txt",
            form_class=BootstrapPasswordResetForm,
        ),
        name="password_reset",
    ),
    path(
        "password-reset/sent/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="accounts/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            form_class=BootstrapSetPasswordForm,
        ),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="accounts/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
]
