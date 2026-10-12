"""Setup status and the verified test message.

GET  /api/v1/whatsapp/setup/                     readiness from real evidence
POST /api/v1/whatsapp/test-message/preview/      what WOULD be sent; sends nothing
POST /api/v1/whatsapp/test-message/send/         separate approval gate, then the real send
POST /api/v1/whatsapp/test-message/cancel/       discard an approval

See ``apps.whatsapp.onboarding`` and ``apps.whatsapp.test_message`` for the rules.
"""

from dataclasses import asdict
from typing import Any

from django.conf import settings
from django.db.models import Count, Max, Q
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.messaging.models import Direction, Message
from apps.messaging.services.outbound import SendScope
from apps.messaging.state import MessageStatus
from apps.organizations.api.scoping import OrganizationScopedMixin
from apps.templates.models import MessageTemplate
from apps.webhooks.models import WebhookEvent
from apps.whatsapp import onboarding, test_message
from apps.whatsapp.models import WhatsAppBusinessAccount, WhatsAppPhoneNumber

_IN_FLIGHT = (
    MessageStatus.QUEUED,
    MessageStatus.SENDING,
    MessageStatus.ACCEPTED,
    MessageStatus.SENT,
)


class SetupStatusView(OrganizationScopedMixin, APIView):
    """Read-only. Never calls Meta; ``POST /accounts/<id>/verify|sync/`` refreshes the data."""

    required_permissions = {"get": "numbers.view"}

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        org = self.organization
        accounts = list(
            self.restrict_to_businesses(
                WhatsAppBusinessAccount.objects.for_organization(org).select_related("business"),
                "business",
            ).order_by("created_at")
        )
        numbers = list(
            self.restrict_to_businesses(
                WhatsAppPhoneNumber.objects.for_organization(org).select_related("business"),
                "business",
            ).order_by("created_at")
        )
        waba_ids = [a.external_id for a in accounts]

        # A WABA belongs to exactly one organization (global unique constraint), so
        # filtering by this scope's WABA ids can never include another tenant's events.
        events = WebhookEvent.objects.filter(
            business_account_external_ids__overlap=waba_ids
        ).aggregate(
            received=Count("id"),
            last=Max("received_at"),
            failed=Count("id", filter=Q(status__in=["failed", "dead"])),
        )
        webhook = onboarding.WebhookEvidence(
            received=events["received"] or 0,
            last_received_at=events["last"],
            failed=events["failed"] or 0,
        )

        tests = Message.objects.for_organization(org).filter(
            direction=Direction.OUTBOUND,
            idempotency_key__startswith=test_message.KEY_PREFIX,
            phone_number_id__in=[n.pk for n in numbers],
        )
        agg = tests.aggregate(
            total=Count("id"),
            delivered=Count(
                "id", filter=Q(status__in=[MessageStatus.DELIVERED, MessageStatus.READ])
            ),
            failed=Count("id", filter=Q(status=MessageStatus.FAILED)),
            in_flight=Count("id", filter=Q(status__in=_IN_FLIGHT)),
        )
        latest = tests.order_by("-created_at").first()
        test_evidence = onboarding.TestMessageEvidence(
            total=agg["total"] or 0,
            delivered=agg["delivered"] or 0,
            failed=agg["failed"] or 0,
            in_flight=agg["in_flight"] or 0,
            last_status=latest.status if latest else "",
            last_at=latest.created_at if latest else None,
        )
        approved = MessageTemplate.objects.filter(
            business_account__in=[a.pk for a in accounts],
            status="APPROVED",
            missing_from_provider=False,
        ).count()

        steps = onboarding.assess_setup(
            accounts=accounts,
            numbers=numbers,
            approved_templates=approved,
            webhook=webhook,
            test_message=test_evidence,
        )
        server = onboarding.webhook_server_config()
        overall, summary, next_id = onboarding.summarize(steps, server)
        business = self.business
        return Response(
            {
                "generated_at": timezone.now(),
                "business": {"id": str(business.pk), "name": business.name} if business else None,
                "overall": overall,
                "summary": summary,
                "next_step": next_id,
                "steps": [asdict(s) for s in steps],
                "server": asdict(server),
                "capabilities": {
                    "embedded_signup": onboarding.embedded_signup_capability(),
                    "callback_check": onboarding.callback_capability(),
                },
                "webhook": {
                    "callback_url": onboarding.callback_url(),
                    "verify_token_configured": bool(settings.META_WEBHOOK_VERIFY_TOKEN),
                },
                "accounts": [
                    {
                        "id": str(a.pk),
                        "name": a.name,
                        "business_id": str(a.business_id),
                        "connection_state": a.connection_state,
                    }
                    for a in accounts
                ],
                "numbers": [
                    {
                        "id": str(n.pk),
                        "label": n.label or n.verified_name,
                        "display_phone_number": n.display_phone_number,
                        "status": n.status,
                        "business_id": str(n.business_id),
                    }
                    for n in numbers
                ],
            }
        )


class PreviewRequestSerializer(serializers.Serializer):
    phone_number_id = serializers.UUIDField()
    contact_id = serializers.UUIDField(required=False)
    to = serializers.CharField(required=False, max_length=32)
    attest_consent = serializers.BooleanField(required=False, default=False)
    template_id = serializers.UUIDField(required=False)


class _TestMessageView(OrganizationScopedMixin, APIView):
    required_permissions = {"post": "messages.send"}

    def scope(self) -> SendScope:
        return SendScope(
            allowed_business_ids=self.allowed_business_ids,
            business_id=self.business.pk if self.business else None,
        )


class TestMessagePreviewView(_TestMessageView):
    __test__ = False

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        body = PreviewRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        preview = test_message.build_preview(
            organization=self.organization,
            scope=self.scope(),
            phone_number_id=data["phone_number_id"],
            contact_id=data.get("contact_id"),
            to=data.get("to"),
            attest_consent=data["attest_consent"],
            template_id=data.get("template_id"),
        )
        payload = test_message.preview_payload(preview)
        payload["approval"] = None
        if preview.can_send:
            token, ttl = test_message.issue_approval(
                preview=preview, user=request.user, organization=self.organization
            )
            payload["approval"] = {"token": token, "expires_in": ttl}
        return Response(payload)


class SendRequestSerializer(serializers.Serializer):
    approval_token = serializers.CharField(max_length=128)
    approve = serializers.BooleanField()


class TestMessageSendView(_TestMessageView):
    __test__ = False
    throttle_scope = "send"

    def get_throttles(self) -> list[Any]:
        return [*super().get_throttles(), ScopedRateThrottle()]

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        body = SendRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        message, created = test_message.approve_and_send(
            token=body.validated_data["approval_token"],
            approve=body.validated_data["approve"],
            user=request.user,
            organization=self.organization,
            scope=self.scope(),
            request=request,
        )
        message = Message.objects.get(pk=message.pk)
        return Response(
            {
                "created": created,
                "message_id": str(message.pk),
                "status": message.status,
                "status_note": (
                    "Queued for sending. This does not mean it was delivered: WhatsApp reports "
                    "that separately, and the status below shows exactly what it reported."
                ),
            },
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )


class TestMessageCancelView(_TestMessageView):
    __test__ = False

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        token = str(request.data.get("approval_token") or "")
        test_message.cancel_approval(
            token=token, user=request.user, organization=self.organization, request=request
        )
        return Response({"cancelled": True})
