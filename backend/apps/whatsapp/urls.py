from django.urls import path

from apps.whatsapp import api

urlpatterns = [
    path("credentials/", api.CredentialListCreateView.as_view(), name="credential-list"),
    path("accounts/", api.BusinessAccountListCreateView.as_view(), name="waba-list"),
    path("accounts/<uuid:pk>/", api.BusinessAccountDetailView.as_view(), name="waba-detail"),
    path("accounts/<uuid:pk>/verify/", api.BusinessAccountVerifyView.as_view(), name="waba-verify"),
    path("accounts/<uuid:pk>/sync/", api.BusinessAccountSyncView.as_view(), name="waba-sync"),
    path(
        "embedded-signup/config/",
        api.EmbeddedSignupConfigView.as_view(),
        name="embedded-signup-config",
    ),
    path(
        "embedded-signup/complete/",
        api.EmbeddedSignupCompleteView.as_view(),
        name="embedded-signup-complete",
    ),
    path("events/", api.AccountEventListView.as_view(), name="account-event-list"),
    path("numbers/", api.PhoneNumberListView.as_view(), name="phone-number-list"),
    path("numbers/<uuid:pk>/", api.PhoneNumberDetailView.as_view(), name="phone-number-detail"),
]
