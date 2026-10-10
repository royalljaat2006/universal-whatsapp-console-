from django.urls import path

from apps.identity import api

urlpatterns = [
    path("csrf/", api.CsrfView.as_view(), name="auth-csrf"),
    path("register/", api.RegisterView.as_view(), name="auth-register"),
    path("login/", api.LoginView.as_view(), name="auth-login"),
    path("mfa/", api.MfaStatusView.as_view(), name="auth-mfa-status"),
    path("mfa/verify/", api.MfaVerifyLoginView.as_view(), name="auth-mfa-verify"),
    path("mfa/setup/", api.MfaSetupView.as_view(), name="auth-mfa-setup"),
    path("mfa/confirm/", api.MfaConfirmView.as_view(), name="auth-mfa-confirm"),
    path("mfa/disable/", api.MfaDisableView.as_view(), name="auth-mfa-disable"),
    path("mfa/recovery-codes/", api.MfaRecoveryCodesView.as_view(), name="auth-mfa-recovery"),
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
