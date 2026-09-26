"""Session authentication API for the first-party web client.

Sessions (HttpOnly cookie) + CSRF are used rather than bearer tokens in
browser storage: the SPA is served same-origin behind nginx, so cookies need
no CORS and tokens are never exposed to JavaScript (ADR-007).
"""

import base64
from typing import Any

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.tokens import default_token_generator
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
from apps.identity.tasks import send_password_reset_email, send_verification_email
from apps.identity.tokens import email_verification_token
from apps.organizations.services import (
    create_organization,
    sole_owner_organizations,
    unique_organization_slug,
)
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
        fields = ["id", "email", "full_name", "is_staff", "is_email_verified"]
        read_only_fields = fields


def _uid_for(user: User) -> str:
    return base64.urlsafe_b64encode(str(user.pk).encode()).decode().rstrip("=")


def _user_from_uid(uid: str) -> User | None:
    try:
        padded = uid + "=" * (-len(uid) % 4)
        pk = base64.urlsafe_b64decode(padded).decode()
        return User.objects.filter(pk=pk, is_active=True).first()
    except (ValueError, TypeError):
        return None


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
        _send_verification_email(user)
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


def _send_verification_email(user: User) -> None:
    uid = _uid_for(user)
    token = email_verification_token.make_token(user)
    verify_url = f"{settings.PUBLIC_BASE_URL}/verify-email?uid={uid}&token={token}"
    send_verification_email.delay(email=user.email, verify_url=verify_url)


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


class RequestPasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()


class RequestPasswordResetView(APIView):
    """Always returns 204 regardless of whether the email exists — the
    response must not disclose account existence (enumeration)."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = RequestPasswordResetSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        email = payload.validated_data["email"].lower()
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is not None:
            uid = _uid_for(user)
            token = default_token_generator.make_token(user)
            reset_url = f"{settings.PUBLIC_BASE_URL}/reset-password?uid={uid}&token={token}"
            send_password_reset_email.delay(email=user.email, reset_url=reset_url)
            audit(actions.AUTH_PASSWORD_RESET_REQUESTED, actor=user, resource=user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConfirmPasswordResetSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False, max_length=512, min_length=10)


class InvalidTokenError(DomainError):
    code = "INVALID_TOKEN"
    status_code = status.HTTP_403_FORBIDDEN
    default_message = "This link is invalid or has expired."


class ConfirmPasswordResetView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = ConfirmPasswordResetSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user = _user_from_uid(payload.validated_data["uid"])
        if user is None or not default_token_generator.check_token(
            user, payload.validated_data["token"]
        ):
            raise InvalidTokenError()
        user.set_password(payload.validated_data["password"])
        user.save(update_fields=["password"])
        # Django compares the session's stored auth hash (derived from the
        # password) on every request — changing it signs every other session
        # for this user out automatically, no extra code needed.
        audit(actions.AUTH_PASSWORD_RESET_COMPLETED, actor=user, resource=user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConfirmEmailVerificationSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()


class ConfirmEmailVerificationView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = ConfirmEmailVerificationSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user = _user_from_uid(payload.validated_data["uid"])
        if user is None or not email_verification_token.check_token(
            user, payload.validated_data["token"]
        ):
            raise InvalidTokenError()
        if not user.is_email_verified:
            user.is_email_verified = True
            user.save(update_fields=["is_email_verified"])
            audit(actions.AUTH_EMAIL_VERIFIED, actor=user, resource=user, request=request)
        return Response(UserSerializer(user).data)


class ResendEmailVerificationView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        user: User = request.user  # type: ignore[assignment]  # IsAuthenticated guarantees a User
        if not user.is_email_verified:
            _send_verification_email(user)
            audit(
                actions.AUTH_EMAIL_VERIFICATION_RESENT, actor=user, resource=user, request=request
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class DeleteAccountSerializer(serializers.Serializer):
    password = serializers.CharField(trim_whitespace=False, max_length=512)


class DeleteAccountView(APIView):
    """Hard-deletes the account. Blocked if it would leave any organization
    with zero active owners — transfer ownership or delete that organization
    first. Memberships/API keys cascade-delete; audit log entries survive
    with their actor set to null (see AuditLog.actor, SET_NULL)."""

    permission_classes = [IsAuthenticated]

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = DeleteAccountSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user: User = request.user  # type: ignore[assignment]  # IsAuthenticated guarantees a User
        if not user.check_password(payload.validated_data["password"]):
            raise InvalidCredentialsError("Incorrect password.")
        blocking = sole_owner_organizations(user)
        if blocking:
            names = ", ".join(o.name for o in blocking)
            raise ConflictError(f"Transfer ownership or delete these organizations first: {names}")
        audit(actions.AUTH_ACCOUNT_DELETED, actor=user, resource=user, request=request)
        logout(request)
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
