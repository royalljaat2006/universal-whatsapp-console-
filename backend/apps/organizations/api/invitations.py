"""Team invitations.

Management (tenant-scoped, members.manage):
  GET    /api/v1/organizations/current/invitations/       list pending+history
  POST   /api/v1/organizations/current/invitations/       {"email","role_key","business_ids"?}
  DELETE /api/v1/organizations/current/invitations/<id>/  revoke

Redemption (public — the recipient has no membership yet, so these are not
tenant-scoped; the token in the body is the only credential):
  GET  /api/v1/invitations/<id>/         preview (org name, role, email) — no token required,
                                          reveals nothing an attacker couldn't already guess
                                          from the id, and never reveals whether an id exists
                                          beyond a generic 404
  POST /api/v1/invitations/<id>/accept/  {"token", "password"?, "full_name"?}

An email is sent when EMAIL_BACKEND is configured for real delivery (see
config/settings/base.py); with the default console backend it just logs.
Either way, the raw token/accept_path is still returned once in the create
response so the inviter can share the link manually as a fallback.
"""

from typing import Any

from django.conf import settings
from rest_framework import generics, serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.businesses.models import Business
from apps.identity.api import enforce_csrf
from apps.identity.models import User
from apps.organizations.api.scoping import OrganizationScopedMixin
from apps.organizations.models import Invitation
from apps.organizations.services import (
    accept_invitation,
    create_invitation,
    revoke_invitation,
)
from core.exceptions import NotFoundError


class InvitationSerializer(serializers.ModelSerializer):
    role = serializers.CharField(source="role.key", read_only=True)
    invited_by_email = serializers.EmailField(source="invited_by.email", read_only=True)

    class Meta:
        model = Invitation
        fields = [
            "id",
            "email",
            "role",
            "status",
            "invited_by_email",
            "expires_at",
            "accepted_at",
            "created_at",
        ]
        read_only_fields = fields


class CreateInvitationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role_key = serializers.CharField()
    business_ids = serializers.ListField(child=serializers.UUIDField(), required=False)


class InvitationListCreateView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = Invitation
    serializer_class = InvitationSerializer
    pagination_class = None
    required_permissions = {"get": "members.view", "post": "members.manage"}

    def scope_queryset(self, queryset: Any) -> Any:
        return queryset.select_related("role", "invited_by").order_by("-created_at")

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        from apps.organizations.tasks import send_invitation_email

        payload = CreateInvitationSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        businesses = None
        business_ids = payload.validated_data.get("business_ids")
        if business_ids:
            businesses = list(
                Business.objects.for_organization(self.organization).filter(pk__in=business_ids)
            )
        invitation, raw_token = create_invitation(
            organization=self.organization,
            email=payload.validated_data["email"],
            role_key=payload.validated_data["role_key"],
            invited_by=request.user,
            businesses=businesses,
        )
        body = InvitationSerializer(invitation).data
        # Shown exactly once — only the hash is ever stored.
        body["token"] = raw_token
        accept_path = f"/invitations/{invitation.pk}/accept?token={raw_token}"
        body["accept_path"] = accept_path
        accept_url = f"{settings.PUBLIC_BASE_URL}{accept_path}"
        user: User = request.user  # type: ignore[assignment]  # IsAuthenticated guarantees a User
        inviter_name = user.full_name or user.email
        # No transaction.on_commit needed (unlike job-processing tasks
        # elsewhere in this codebase): the task takes every value as an
        # argument and never re-queries the Invitation row, so there's no
        # race with this request's own transaction committing — same
        # reasoning as apps.identity.api's verification/reset email sends.
        send_invitation_email.delay(
            email=invitation.email,
            organization_name=self.organization.name,
            inviter_name=inviter_name,
            role_name=invitation.role.name,
            accept_url=accept_url,
        )
        return Response(body, status=status.HTTP_201_CREATED)


class InvitationRevokeView(OrganizationScopedMixin, APIView):
    queryset_model = Invitation
    required_permissions = {"delete": "members.manage"}

    def delete(self, request: Request, pk: str, *args: Any, **kwargs: Any) -> Response:
        invitation = Invitation.objects.for_organization(self.organization).filter(pk=pk).first()
        if invitation is None:
            raise NotFoundError("Invitation not found.")
        revoke_invitation(invitation=invitation, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvitationPreviewSerializer(serializers.Serializer):
    organization_name = serializers.CharField()
    role_name = serializers.CharField()
    email = serializers.EmailField()
    status = serializers.CharField()
    expires_at = serializers.DateTimeField()


class InvitationPreviewView(APIView):
    """Lets an unauthenticated recipient see what they're accepting before
    creating an account. Deliberately does not require the token: it discloses
    only what the id already implies is being asked about, nothing the
    account/role/business relationships that require the token to touch."""

    permission_classes = [AllowAny]

    def get(self, request: Request, pk: str, *args: Any, **kwargs: Any) -> Response:
        invitation = Invitation.objects.select_related("organization", "role").filter(pk=pk).first()
        if invitation is None:
            raise NotFoundError("Invitation not found.")
        return Response(
            InvitationPreviewSerializer(
                {
                    "organization_name": invitation.organization.name,
                    "role_name": invitation.role.name,
                    "email": invitation.email,
                    "status": invitation.status,
                    "expires_at": invitation.expires_at,
                }
            ).data
        )


class AcceptInvitationSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(required=False, trim_whitespace=False, max_length=512)
    full_name = serializers.CharField(required=False, max_length=200, allow_blank=True)


class InvitationAcceptView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request, pk: str, *args: Any, **kwargs: Any) -> Response:
        # Accepting can log the caller in (new-user branch below), the same
        # login-CSRF exposure LoginView guards against — enforced regardless
        # of which branch runs, for one consistent rule.
        enforce_csrf(request)
        invitation = Invitation.objects.filter(pk=pk).first()
        if invitation is None:
            raise NotFoundError("Invitation not found.")
        payload = AcceptInvitationSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        caller = request.user if request.user.is_authenticated else None
        membership, user = accept_invitation(
            invitation=invitation,
            raw_token=payload.validated_data["token"],
            user=caller,
            password=payload.validated_data.get("password"),
            full_name=payload.validated_data.get("full_name", ""),
        )
        if caller is None:
            from django.contrib.auth import login

            # Created programmatically, not via authenticate(): login() needs
            # .backend set explicitly (only one backend is configured).
            user.backend = "django.contrib.auth.backends.ModelBackend"
            login(request, user)
        return Response(
            {
                "organization_id": str(membership.organization_id),
                "role": membership.role.key,
                "joined_at": membership.joined_at,
            },
            status=status.HTTP_200_OK,
        )
