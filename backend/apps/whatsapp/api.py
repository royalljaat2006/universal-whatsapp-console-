"""WhatsApp management API (credentials, WABAs, phone numbers).

Provider calls never run in the request: connect/verify/sync are queued and
report progress through ``connection_state`` / ``sync_state`` on the account.
Credentials are write-only: no endpoint ever returns a token.
"""

from typing import Any

from django.db import transaction
from django.db.models import Count, QuerySet
from rest_framework import generics, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import actions
from apps.audit.services import record as audit
from apps.businesses.services import get_business_for_membership
from apps.organizations.api.scoping import OrganizationScopedMixin
from apps.whatsapp.models import (
    ProviderCredential,
    WhatsAppAccountEvent,
    WhatsAppBusinessAccount,
    WhatsAppPhoneNumber,
)
from apps.whatsapp.services import (
    create_credential_reference,
    create_encrypted_credential,
    register_business_account,
)
from core.api.pagination import StandardCursorPagination
from core.exceptions import NotFoundError
from core.secrets import SecretReferenceError

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


class CredentialSerializer(serializers.ModelSerializer):
    backend = serializers.SerializerMethodField()

    class Meta:
        model = ProviderCredential
        fields = [
            "id",
            "label",
            "provider",
            "kind",
            "backend",
            "fingerprint",
            "status",
            "created_at",
        ]
        read_only_fields = fields

    def get_backend(self, obj: ProviderCredential) -> str:
        return "env" if obj.secret_reference else "encrypted"


class CredentialCreateSerializer(serializers.Serializer):
    # A declared field named "label" shadows Field.label for type checkers only.
    label = serializers.CharField(max_length=100)  # type: ignore[assignment]
    secret_reference = serializers.CharField(max_length=200, required=False)
    access_token = serializers.CharField(max_length=1024, required=False, write_only=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if bool(attrs.get("secret_reference")) == bool(attrs.get("access_token")):
            raise serializers.ValidationError(
                "provide exactly one of secret_reference or access_token"
            )
        return attrs


class CredentialListCreateView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = ProviderCredential
    serializer_class = CredentialSerializer
    required_permissions = {"get": "integrations.manage", "post": "integrations.manage"}

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = CredentialCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        try:
            if data.get("secret_reference"):
                credential = create_credential_reference(
                    organization=self.organization,
                    label=data["label"],
                    secret_reference=data["secret_reference"],
                    created_by=request.user,
                )
            else:
                credential = create_encrypted_credential(
                    organization=self.organization,
                    label=data["label"],
                    access_token=data["access_token"],
                    created_by=request.user,
                )
        except SecretReferenceError as exc:
            raise serializers.ValidationError({"secret_reference": str(exc)}) from exc
        return Response(CredentialSerializer(credential).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Business accounts (WABAs)
# ---------------------------------------------------------------------------


class BusinessAccountSerializer(serializers.ModelSerializer):
    business_id = serializers.UUIDField(read_only=True)
    business_name = serializers.CharField(source="business.name", read_only=True)
    credential_id = serializers.UUIDField(read_only=True)
    phone_number_count = serializers.IntegerField(read_only=True)
    webhooks_subscribed = serializers.SerializerMethodField()

    class Meta:
        model = WhatsAppBusinessAccount
        fields = [
            "id",
            "business_id",
            "business_name",
            "provider",
            "external_id",
            "business_portfolio_external_id",
            "name",
            "status",
            "credential_id",
            "connection_state",
            "connection_error",
            "connection_error_code",
            "last_verified_at",
            "sync_state",
            "sync_error",
            "last_synced_at",
            "template_sync_state",
            "template_sync_error",
            "templates_last_synced_at",
            "webhooks_subscribed",
            "account_review_status",
            "business_verification_status",
            "country",
            "timezone_id",
            "phone_number_count",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields

    def get_webhooks_subscribed(self, obj: WhatsAppBusinessAccount) -> bool:
        return obj.webhooks_subscribed_at is not None


class BusinessAccountCreateSerializer(serializers.Serializer):
    business_id = serializers.UUIDField()
    external_id = serializers.RegexField(r"^\d{5,32}$", help_text="WABA ID from Meta")
    credential_id = serializers.UUIDField()
    name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    business_portfolio_external_id = serializers.RegexField(
        r"^\d{5,32}$", required=False, allow_blank=True
    )


class BusinessAccountUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    credential_id = serializers.UUIDField(required=False)
    business_portfolio_external_id = serializers.RegexField(
        r"^\d{5,32}$", required=False, allow_blank=True
    )
    metadata = serializers.DictField(required=False)


def _annotated_accounts(queryset: QuerySet[Any]) -> QuerySet[Any]:
    return queryset.select_related("business").annotate(phone_number_count=Count("phone_numbers"))


def _queue(task: Any, account_id: str) -> None:
    transaction.on_commit(lambda: task.delay(account_id))


class BusinessAccountListCreateView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = WhatsAppBusinessAccount
    business_field = "business"
    serializer_class = BusinessAccountSerializer
    required_permissions = {"get": "numbers.view", "post": "numbers.manage"}

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        return _annotated_accounts(queryset)

    def _credential(self, credential_id: Any) -> ProviderCredential:
        credential = (
            ProviderCredential.objects.for_organization(self.organization)
            .filter(pk=credential_id, status=ProviderCredential.Status.ACTIVE)
            .first()
        )
        if credential is None:
            raise NotFoundError("credential not found")
        return credential

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        from apps.whatsapp.tasks import connect_business_account

        payload = BusinessAccountCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        business = get_business_for_membership(self.membership, data["business_id"])
        with transaction.atomic():
            account = register_business_account(
                organization=self.organization,
                business=business,
                external_id=data["external_id"],
                credential=self._credential(data["credential_id"]),
                name=data.get("name", ""),
                business_portfolio_external_id=data.get("business_portfolio_external_id", ""),
                actor=request.user,
                request=request,
            )
            _queue(connect_business_account, str(account.pk))
        account = _annotated_accounts(WhatsAppBusinessAccount.objects.filter(pk=account.pk)).get()
        return Response(BusinessAccountSerializer(account).data, status=status.HTTP_202_ACCEPTED)


class BusinessAccountDetailView(OrganizationScopedMixin, generics.RetrieveAPIView):
    queryset_model = WhatsAppBusinessAccount
    business_field = "business"
    serializer_class = BusinessAccountSerializer
    required_permissions = {"get": "numbers.view", "patch": "numbers.manage"}

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        return _annotated_accounts(queryset)

    def patch(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        account = self.get_object()
        payload = BusinessAccountUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        if "credential_id" in data:
            credential = (
                ProviderCredential.objects.for_organization(self.organization)
                .filter(pk=data.pop("credential_id"), status=ProviderCredential.Status.ACTIVE)
                .first()
            )
            if credential is None:
                raise NotFoundError("credential not found")
            account.credential = credential
            account.connection_state = "pending"
        for field, value in data.items():
            setattr(account, field, value)
        account.save()
        audit(
            actions.WABA_UPDATED,
            organization=self.organization,
            actor=request.user,
            resource=account,
            metadata={"fields": sorted(payload.validated_data)},
            request=request,
        )
        return Response(BusinessAccountSerializer(self.get_object()).data)


class _AccountActionView(OrganizationScopedMixin, APIView):
    required_permissions = {"post": "numbers.manage"}

    def account(self, pk: Any) -> WhatsAppBusinessAccount:
        queryset = self.restrict_to_businesses(
            WhatsAppBusinessAccount.objects.for_organization(self.organization), "business"
        )
        account = queryset.filter(pk=pk).first()
        if account is None:
            raise NotFoundError("business account not found")
        return account


class BusinessAccountVerifyView(_AccountActionView):
    """Queue a connection check (credential valid + WABA reachable + webhook
    subscription) against the provider."""

    def post(self, request: Request, pk: str) -> Response:
        from apps.whatsapp.tasks import verify_business_account

        _queue(verify_business_account, str(self.account(pk).pk))
        return Response({"status": "queued"}, status=status.HTTP_202_ACCEPTED)


class BusinessAccountSyncView(_AccountActionView):
    """Queue a phone-number and template sync from the provider."""

    def post(self, request: Request, pk: str) -> Response:
        from apps.templates.tasks import sync_business_account_templates
        from apps.whatsapp.tasks import sync_business_account_numbers

        account_id = str(self.account(pk).pk)
        _queue(sync_business_account_numbers, account_id)
        _queue(sync_business_account_templates, account_id)
        return Response({"status": "queued"}, status=status.HTTP_202_ACCEPTED)


# ---------------------------------------------------------------------------
# Phone numbers
# ---------------------------------------------------------------------------


class PhoneNumberSerializer(serializers.ModelSerializer):
    business_id = serializers.UUIDField(read_only=True)
    business_account_id = serializers.UUIDField(read_only=True)
    business_account_external_id = serializers.CharField(
        source="business_account.external_id", read_only=True
    )
    business_account_name = serializers.CharField(source="business_account.name", read_only=True)
    business_account_connection_state = serializers.CharField(
        source="business_account.connection_state", read_only=True
    )
    business_account_sync_state = serializers.CharField(
        source="business_account.sync_state", read_only=True
    )

    class Meta:
        model = WhatsAppPhoneNumber
        fields = [
            "id",
            "business_id",
            "business_account_id",
            "business_account_external_id",
            "business_account_name",
            "business_account_connection_state",
            "business_account_sync_state",
            "provider",
            "external_id",
            "display_phone_number",
            "verified_name",
            "label",
            "status",
            "provider_status",
            "quality_rating",
            "name_status",
            "messaging_limit_tier",
            "throughput_mps",
            "last_synced_at",
            "last_error",
            "metadata",
            "created_at",
        ]
        read_only_fields = [f for f in fields if f not in {"label", "throughput_mps", "metadata"}]


class PhoneNumberListView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = WhatsAppPhoneNumber
    business_field = "business"
    serializer_class = PhoneNumberSerializer
    required_permissions = {"get": "numbers.view"}

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        params = self.request.query_params
        if params.get("business_account"):
            queryset = queryset.filter(business_account_id=params["business_account"])
        if params.get("status"):
            queryset = queryset.filter(status=params["status"])
        return queryset.select_related("business_account")


class PhoneNumberDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    """Only local fields (label, throughput cap, metadata) are editable; provider
    fields change exclusively through sync."""

    queryset_model = WhatsAppPhoneNumber
    business_field = "business"
    serializer_class = PhoneNumberSerializer
    required_permissions = {"get": "numbers.view", "patch": "numbers.manage"}
    http_method_names = ["get", "patch", "head", "options"]

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        return queryset.select_related("business_account")

    def perform_update(self, serializer: serializers.BaseSerializer) -> None:
        number = serializer.save()
        audit(
            actions.PHONE_NUMBER_UPDATED,
            organization=self.organization,
            actor=self.request.user,
            resource=number,
            metadata={"fields": sorted(serializer.validated_data)},
            request=self.request,
        )


# ---------------------------------------------------------------------------
# Account / number events (quality, limits, alerts, account updates)
# ---------------------------------------------------------------------------


class AccountEventSerializer(serializers.ModelSerializer):
    business_id = serializers.UUIDField(read_only=True)
    business_account_id = serializers.UUIDField(read_only=True)
    phone_number_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = WhatsAppAccountEvent
        fields = [
            "id",
            "business_id",
            "business_account_id",
            "phone_number_id",
            "field",
            "event",
            "severity",
            "summary",
            "payload",
            "occurred_at",
        ]
        read_only_fields = fields


class EventPagination(StandardCursorPagination):
    ordering = ("-occurred_at", "-id")


class AccountEventListView(OrganizationScopedMixin, generics.ListAPIView):
    """GET /api/v1/whatsapp/events/?severity=&business_account=&phone_number="""

    queryset_model = WhatsAppAccountEvent
    business_field = "business"
    serializer_class = AccountEventSerializer
    pagination_class = EventPagination
    required_permissions = {"get": "numbers.view"}

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        params = self.request.query_params
        if params.get("severity"):
            queryset = queryset.filter(severity=params["severity"])
        if params.get("business_account"):
            queryset = queryset.filter(business_account_id=params["business_account"])
        if params.get("phone_number"):
            queryset = queryset.filter(phone_number_id=params["phone_number"])
        return queryset
