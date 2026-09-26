from django.urls import path

from apps.identity import api

urlpatterns = [
    path("csrf/", api.CsrfView.as_view(), name="auth-csrf"),
    path("register/", api.RegisterView.as_view(), name="auth-register"),
    path("login/", api.LoginView.as_view(), name="auth-login"),
    path("logout/", api.LogoutView.as_view(), name="auth-logout"),
    path("me/", api.MeView.as_view(), name="auth-me"),
    path(
        "password-reset/",
        api.RequestPasswordResetView.as_view(),
        name="auth-password-reset-request",
    ),
    path(
        "password-reset/confirm/",
        api.ConfirmPasswordResetView.as_view(),
        name="auth-password-reset-confirm",
    ),
    path(
        "verify-email/confirm/",
        api.ConfirmEmailVerificationView.as_view(),
        name="auth-verify-email-confirm",
    ),
    path(
        "verify-email/resend/",
        api.ResendEmailVerificationView.as_view(),
        name="auth-verify-email-resend",
    ),
    path("account/", api.DeleteAccountView.as_view(), name="auth-delete-account"),
]
