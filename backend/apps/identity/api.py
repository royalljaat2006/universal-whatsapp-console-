"""Session authentication API for the first-party web client.

Sessions (HttpOnly cookie) + CSRF are used rather than bearer tokens in
browser storage: the SPA is served same-origin behind nginx, so cookies need
no CORS and tokens are never exposed to JavaScript (ADR-007).
"""

from typing import Any

from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import serializers, status
from rest_framework.authentication import CSRFCheck
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.audit import actions
from apps.audit.services import record as audit
from apps.identity.models import User
from apps.organizations.services import create_organization, unique_organization_slug
from core.exceptions import ConflictError, DomainError


class InvalidCredentialsError(DomainError):
    code = "INVALID_CREDENTIALS"
    status_code = status.HTTP_401_UNAUTHORIZED
    default_message = "Email or password is incorrect."


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False, max_length=512)


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False, max_length=512, min_length=10)
    full_name = serializers.CharField(max_length=200, allow_blank=True, default="")
    organization_name = serializers.CharField(max_length=200)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "full_name", "is_staff"]
        read_only_fields = fields


@method_decorator(ensure_csrf_cookie, name="get")
class CsrfView(APIView):
    """Sets the CSRF cookie so the SPA can make its first unsafe request."""

    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        return Response(status=status.HTTP_204_NO_CONTENT)


def enforce_csrf(request: Request) -> None:
    """DRF only enforces CSRF for already-authenticated sessions; login must be
    protected too (login-CSRF would sign a victim into an attacker's account)."""
    check = CSRFCheck(lambda _request: None)  # type: ignore[arg-type]
    check.process_request(request._request)
    reason = check.process_view(request._request, None, (), {})  # type: ignore[arg-type]
    if reason:
        raise PermissionDenied(f"CSRF failed: {reason}")


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        enforce_csrf(request)
        payload = LoginSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        email = payload.validated_data["email"].lower()
        user = authenticate(request, email=email, password=payload.validated_data["password"])
        if user is None:
            # Never record the attempted password; email only, for abuse analysis.
            audit(actions.AUTH_LOGIN_FAILED, metadata={"email": email}, request=request)
            raise InvalidCredentialsError()
        login(request, user)
        audit(actions.AUTH_LOGIN_SUCCEEDED, actor=user, resource=user, request=request)
        return Response(UserSerializer(user).data)


class RegisterView(APIView):
    """Self-serve signup: creates a brand-new account *and* a brand-new
    organization (the caller becomes its owner). Joining an *existing*
    organization goes through an invitation instead
    (apps.organizations.api.invitations) — this endpoint never attaches a new
    user to someone else's organization."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        enforce_csrf(request)
        payload = RegisterSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        email = payload.validated_data["email"].lower()
        try:
            user = User.objects.create_user(
                email=email,
                password=payload.validated_data["password"],
                full_name=payload.validated_data.get("full_name", ""),
            )
        except (IntegrityError, DjangoValidationError) as exc:
            raise ConflictError("An account with this email already exists.") from exc
        slug = unique_organization_slug(payload.validated_data["organization_name"])
        create_organization(name=payload.validated_data["organization_name"], owner=user, slug=slug)
        login(request, user)
        audit(actions.AUTH_REGISTERED, actor=user, resource=user, request=request)
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        audit(actions.AUTH_LOGOUT, actor=request.user, request=request)
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return Response(UserSerializer(request.user).data)
