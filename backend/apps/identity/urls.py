from django.urls import path

from apps.identity import api

urlpatterns = [
    path("csrf/", api.CsrfView.as_view(), name="auth-csrf"),
    path("register/", api.RegisterView.as_view(), name="auth-register"),
    path("login/", api.LoginView.as_view(), name="auth-login"),
    path("logout/", api.LogoutView.as_view(), name="auth-logout"),
    path("me/", api.MeView.as_view(), name="auth-me"),
]
