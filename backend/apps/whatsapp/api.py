"""WhatsApp management API (credentials, WABAs, phone numbers).

Provider calls never run in the request: connect/verify/sync are queued and
report progress through ``connection_state`` / ``sync_state`` on the account.
Credentials are write-only: no endpoint ever returns a token.
"""

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, QuerySet
from django.utils import timezone
from rest_framework import generics, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import actions
from apps.audit.services import record as audit
from apps.businesses.services import get_business_for_membership
from apps.messaging.services import failure_categories
from apps.messaging.state import MessageStatus
from apps.organizations.api.scoping import OrganizationScopedMixin
from apps.whatsapp import health, signup_state
from apps.whatsapp.models import (
    ProviderCredential,
    WhatsAppAccountEvent,
    WhatsAppBusinessAccount,
    WhatsAppPhoneNumber,
)
from apps.whatsapp.services import (
    complete_embedded_signup,
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


# ---------------------------------------------------------------------------
# Embedded Signup
# ---------------------------------------------------------------------------


class EmbeddedSignupConfigView(OrganizationScopedMixin, APIView):
    """Non-secret values the frontend needs to launch the Facebook JS SDK.
    Gated the same as connecting a WABA: only someone who could complete the
    flow needs to see the launch config."""

    required_permissions = {"get": "numbers.manage"}

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return Response(
            {
                "app_id": settings.META_APP_ID,
                "config_id": settings.META_EMBEDDED_SIGNUP_CONFIG_ID,
                "graph_api_version": settings.META_GRAPH_API_VERSION,
                "configured": bool(
                    settings.META_APP_ID and settings.META_EMBEDDED_SIGNUP_CONFIG_ID
                ),
            }
        )


class EmbeddedSignupCompleteSerializer(serializers.Serializer):
    business_id = serializers.UUIDField()
    waba_id = serializers.RegexField(r"^\d{5,32}$", help_text="WABA ID from the FINISH event")
    phone_number_id = serializers.RegexField(
        r"^\d{5,32}$", help_text="Phone number ID from the FINISH event"
    )
    code = serializers.CharField(max_length=2048, trim_whitespace=False)
    state = serializers.CharField(
        max_length=128, help_text="One-time value from POST embedded-signup/session/"
    )


class EmbeddedSignupSessionView(OrganizationScopedMixin, APIView):
    """POST {business_id} -> {state, expires_in}. Call before opening Meta's
    dialog; the completion call must return the same ``state`` (single use,
    bound to this user, organization and business)."""

    required_permissions = {"post": "numbers.manage"}

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        business_id = request.data.get("business_id")
        if not business_id:
            raise serializers.ValidationError({"business_id": "required"})
        business = get_business_for_membership(self.membership, business_id)
        session = signup_state.issue_state(
            user=request.user, organization=self.organization, business=business
        )
        return Response({"state": session.state, "expires_in": session.expires_in})


class EmbeddedSignupCompleteView(OrganizationScopedMixin, APIView):
    """POST {business_id, waba_id, phone_number_id, code} — the data Embedded
    Signup's message event and FB.login callback hand back to the frontend.
    Exchanges the code, connects the WABA, and queues the same verify ->
    subscribe -> sync pipeline the manual connect flow uses."""

    required_permissions = {"post": "numbers.manage"}

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        from apps.whatsapp.tasks import connect_business_account

        payload = EmbeddedSignupCompleteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        business = get_business_for_membership(self.membership, data["business_id"])
        signup_state.consume_state(
            state=data["state"], user=request.user, organization=self.organization, business=business
        )
        # Not wrapped in a transaction: this calls Meta twice (code exchange,
        # phone registration) around the DB writes, and provider calls never
        # run inside a database transaction (see module docstring).
        account = complete_embedded_signup(
            organization=self.organization,
            business=business,
            waba_id=data["waba_id"],
            phone_number_id=data["phone_number_id"],
            code=data["code"],
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


class HealthView(OrganizationScopedMixin, APIView):
    """GET /api/v1/whatsapp/health/ - plain-language health of the selected
    business's WhatsApp accounts and numbers (every account the caller may see
    without X-Business-ID).

    Read-only and derived entirely from stored signals (see
    ``apps.whatsapp.health``): it never calls Meta in the request, never
    returns tokens or provider ids, and reports a missing signal as
    ``unknown`` rather than healthy. ``POST /accounts/<id>/verify|sync/``
    (existing) refreshes the underlying data."""

    required_permissions = {"get": "numbers.view"}

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        now = timezone.now()
        org = self.organization
        accounts = list(
            self.restrict_to_businesses(
                WhatsAppBusinessAccount.objects.for_organization(org).select_related("business"),
                "business",
            ).order_by("created_at")
        )
        numbers = list(
            self.restrict_to_businesses(
                WhatsAppPhoneNumber.objects.for_organization(org).select_related(
                    "business", "business_account"
                ),
                "business",
            ).order_by("created_at")
        )
        recent = self._recent_delivery(numbers, now)

        account_rows = []
        assessments: list[health.Assessment] = []
        for waba in accounts:
            result = health.assess_account(waba, now=now)
            assessments.append(result)
            account_rows.append(
                {
                    "id": str(waba.pk),
                    "name": waba.name,
                    "business_id": str(waba.business_id),
                    "business_name": waba.business.name,
                    "overall": result.overall,
                    "last_checked_at": waba.last_verified_at,
                    "checks": [health.as_dict(c) for c in result.checks],
                }
            )
        number_rows = []
        for phone in numbers:
            result = health.assess_number(
                phone, now=now, recent=recent.get(phone.pk, health.RecentDelivery())
            )
            assessments.append(result)
            number_rows.append(
                {
                    "id": str(phone.pk),
                    "label": phone.label,
                    "display_phone_number": phone.display_phone_number,
                    "verified_name": phone.verified_name,
                    "business_id": str(phone.business_id),
                    "business_name": phone.business.name,
                    "business_account_id": str(phone.business_account_id),
                    "overall": result.overall,
                    "last_checked_at": phone.last_synced_at,
                    "checks": [health.as_dict(c) for c in result.checks],
                }
            )

        overall, summary = health.summarize(assessments)
        alerts = (
            self.restrict_to_businesses(
                WhatsAppAccountEvent.objects.for_organization(org).select_related("business"),
                "business",
            )
            .filter(
                severity__in=[
                    WhatsAppAccountEvent.Severity.WARNING,
                    WhatsAppAccountEvent.Severity.CRITICAL,
                ],
                occurred_at__gte=now - timedelta(days=14),
            )
            .order_by("-occurred_at")[:5]
        )
        return Response(
            {
                "generated_at": now,
                "overall": overall,
                "summary": summary,
                "accounts": account_rows,
                "numbers": number_rows,
                "recent_alerts": [
                    {
                        "id": str(e.pk),
                        "severity": e.severity,
                        "summary": e.summary,
                        "occurred_at": e.occurred_at,
                        "business_name": e.business.name if e.business_id else "",
                    }
                    for e in alerts
                ],
                "notes": [
                    "Statuses are what Meta last reported to this console, not a live check. "
                    "Use Refresh from Meta to read the current values.",
                    "Rules marked “console” are this app's own thresholds, not Meta's.",
                ],
            }
        )

    def _recent_delivery(
        self, numbers: list[WhatsAppPhoneNumber], now: Any
    ) -> dict[Any, health.RecentDelivery]:
        from apps.messaging.models import Message

        if not numbers:
            return {}
        since = now - health.RECENT_WINDOW
        base = Message.objects.for_organization(self.organization).filter(
            direction="outbound",
            created_at__gte=since,
            phone_number_id__in=[n.pk for n in numbers],
        )
        totals = {
            row["phone_number_id"]: (row["total"], row["failed"])
            for row in base.values("phone_number_id").annotate(
                total=Count("id"), failed=Count("id", filter=Q(status=MessageStatus.FAILED))
            )
        }
        causes: dict[Any, dict[str, int]] = {}
        for row in (
            base.filter(status=MessageStatus.FAILED)
            .values("phone_number_id", "error_code", "error_details__provider_code")
            .annotate(n=Count("id"))
        ):
            category = failure_categories.categorize(
                row["error_code"], {"provider_code": row["error_details__provider_code"]}
            )
            bucket = causes.setdefault(row["phone_number_id"], {})
            bucket[category] = bucket.get(category, 0) + row["n"]
        return {
            pk: health.RecentDelivery(
                total=total,
                failed=failed,
                causes=tuple(sorted(causes.get(pk, {}).items(), key=lambda kv: -kv[1])),
            )
            for pk, (total, failed) in totals.items()
        }
