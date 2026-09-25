"""Conversations API (business-scoped): inbox list with search/filters,
detail, status, read, assignment (+history), tags, assignee options."""

import contextlib
import uuid
from datetime import datetime
from typing import Any

from django.db.models import Exists, OuterRef, Prefetch, Q, QuerySet
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import generics, serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.businesses.services import accessible_business_ids
from apps.contacts.management_services import tag_conversation
from apps.contacts.models import Tag
from apps.conversations.assignment import assign
from apps.conversations.models import Conversation, ConversationAssignment
from apps.conversations.services import change_status, mark_read
from apps.messaging import policy
from apps.messaging.models import Message
from apps.messaging.services import events
from apps.organizations.api.scoping import OrganizationScopedMixin
from apps.organizations.models import Membership
from core.api.pagination import StandardCursorPagination
from core.exceptions import DomainError, NotFoundError


def conversation_queryset(queryset: QuerySet[Conversation]) -> QuerySet[Conversation]:
    """Everything the serializer touches, in a fixed number of queries."""
    return queryset.select_related(
        "contact", "phone_number", "assigned_membership__user", "assigned_team"
    ).prefetch_related(Prefetch("tags", queryset=Tag.objects.order_by("name")))


class _TagBrief(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    color = serializers.CharField()


class ConversationSerializer(serializers.ModelSerializer):
    business_id = serializers.UUIDField(read_only=True)
    contact_id = serializers.UUIDField(read_only=True)
    contact_name = serializers.SerializerMethodField()
    contact_phone_number = serializers.CharField(source="contact.phone_number", read_only=True)
    phone_number_id = serializers.UUIDField(read_only=True)
    phone_number_display = serializers.CharField(
        source="phone_number.display_phone_number", read_only=True
    )
    assignee = serializers.SerializerMethodField()
    team = serializers.SerializerMethodField()
    tags = _TagBrief(many=True, read_only=True)
    send_policy = serializers.SerializerMethodField()
    service_window_expires_at = serializers.SerializerMethodField()
    service_window_open = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "business_id",
            "contact_id",
            "contact_name",
            "contact_phone_number",
            "phone_number_id",
            "phone_number_display",
            "status",
            "assignee",
            "team",
            "tags",
            "unread_count",
            "last_message_at",
            "last_inbound_at",
            "last_message_preview",
            "last_message_direction",
            "send_policy",
            "service_window_expires_at",
            "service_window_open",
            "status_changed_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_contact_name(self, obj: Conversation) -> str:
        return str(obj.contact)

    def get_assignee(self, obj: Conversation) -> dict[str, Any] | None:
        membership = obj.assigned_membership
        if membership is None:
            return None
        return {
            "membership_id": str(membership.pk),
            "name": membership.user.full_name or membership.user.email,
            "email": membership.user.email,
        }

    def get_team(self, obj: Conversation) -> dict[str, Any] | None:
        team = obj.assigned_team
        return {"id": str(team.pk), "name": team.name} if team else None

    def get_send_policy(self, obj: Conversation) -> dict[str, object]:
        return policy.evaluate(obj).as_dict()

    def get_service_window_expires_at(self, obj: Conversation) -> str | None:
        expires = obj.service_window_expires_at()
        return expires.isoformat() if expires else None

    def get_service_window_open(self, obj: Conversation) -> bool:
        return obj.is_service_window_open()


class ConversationStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Conversation.Status.choices)


class AssignSerializer(serializers.Serializer):
    membership_id = serializers.UUIDField(required=False, allow_null=True)
    team_id = serializers.UUIDField(required=False, allow_null=True)


class InboxPagination(StandardCursorPagination):
    ordering = ("-last_message_at", "-id")


def _bound(raw: str | None, *, end: bool) -> datetime | None:
    if not raw:
        return None
    from datetime import time

    from django.utils import timezone

    # A bare date covers the whole local day. Checked first because Django 5's
    # parse_datetime also accepts "YYYY-MM-DD" (as naive midnight).
    try:
        day = parse_date(raw)
        parsed = None if day else parse_datetime(raw)
    except ValueError:
        day = parsed = None
    if day is not None:
        return timezone.make_aware(datetime.combine(day, time.max if end else time.min))
    if parsed is None:
        raise DomainError("Invalid date filter.", details={"value": raw})
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


class ConversationListView(OrganizationScopedMixin, generics.ListAPIView):
    """Filters: phone_number, status (archived hidden unless asked), unread,
    assigned=me|none|<membership id>, team, tag, date_from, date_to, q
    (contact name, phone, message text, conversation id)."""

    queryset_model = Conversation
    business_field = "business"
    serializer_class = ConversationSerializer
    pagination_class = InboxPagination
    required_permissions = {"get": "conversations.view"}

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        params = self.request.query_params
        if params.get("phone_number"):
            queryset = queryset.filter(phone_number_id=params["phone_number"])
        if params.get("status"):
            queryset = queryset.filter(status=params["status"])
        else:
            queryset = queryset.exclude(status=Conversation.Status.ARCHIVED)
        if params.get("unread") in {"1", "true"}:
            queryset = queryset.filter(unread_count__gt=0)
        assigned = params.get("assigned")
        if assigned == "me":
            queryset = queryset.filter(assigned_membership=self.membership)
        elif assigned == "none":
            queryset = queryset.filter(assigned_membership__isnull=True, assigned_team__isnull=True)
        elif assigned:
            queryset = queryset.filter(assigned_membership_id=assigned)
        if params.get("team"):
            queryset = queryset.filter(assigned_team_id=params["team"])
        if params.get("tag"):
            queryset = queryset.filter(tags__id=params["tag"])
        date_from = _bound(params.get("date_from"), end=False)
        date_to = _bound(params.get("date_to"), end=True)
        if date_from:
            queryset = queryset.filter(last_message_at__gte=date_from)
        if date_to:
            queryset = queryset.filter(last_message_at__lte=date_to)
        q = (params.get("q") or "").strip()
        if q:
            queryset = queryset.filter(self._search(q))
        return conversation_queryset(queryset.filter(last_message_at__isnull=False))

    def _search(self, q: str) -> Any:
        condition: Any = Q(contact__display_name__icontains=q) | Q(
            contact__profile_name__icontains=q
        )
        digits = "".join(ch for ch in q if ch.isdigit())
        if len(digits) >= 3:
            condition |= Q(contact__phone_number__contains=digits)
        with contextlib.suppress(ValueError):
            condition |= Q(pk=uuid.UUID(q))
        if len(q) >= 3:
            condition |= Exists(
                Message.objects.filter(conversation_id=OuterRef("pk"), text__icontains=q)
            )
        return condition


class _ConversationObjectMixin(OrganizationScopedMixin):
    def conversation(self, pk: Any) -> Conversation:
        queryset = self.restrict_to_businesses(
            Conversation.objects.for_organization(self.organization), "business"
        )
        conversation = conversation_queryset(queryset).filter(pk=pk).first()
        if conversation is None:
            raise NotFoundError("conversation not found")
        return conversation

    def respond(self, pk: Any) -> Response:
        return Response(ConversationSerializer(self.conversation(pk)).data)


class ConversationDetailView(_ConversationObjectMixin, APIView):
    """GET a conversation; PATCH ``{"status": ...}`` (conversations.assign)."""

    required_permissions = {"get": "conversations.view", "patch": "conversations.assign"}

    def get(self, request: Request, pk: str) -> Response:
        return self.respond(pk)

    def patch(self, request: Request, pk: str) -> Response:
        payload = ConversationStatusSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        conversation = change_status(
            self.conversation(pk),
            payload.validated_data["status"],
            actor=request.user,
            request=request,
        )
        events.conversation_updated(conversation)
        return self.respond(pk)


class ConversationReadView(_ConversationObjectMixin, APIView):
    """POST: the agent has seen the conversation; resets the unread count."""

    required_permissions = {"post": "conversations.view"}

    def post(self, request: Request, pk: str) -> Response:
        conversation = mark_read(self.conversation(pk))
        events.conversation_updated(conversation)
        return self.respond(pk)


class ConversationAssignView(_ConversationObjectMixin, APIView):
    """POST {"membership_id": uuid|null, "team_id": uuid|null}."""

    required_permissions = {"post": "conversations.assign"}

    def post(self, request: Request, pk: str) -> Response:
        payload = AssignSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        assign(
            self.conversation(pk),
            membership_id=payload.validated_data.get("membership_id"),
            team_id=payload.validated_data.get("team_id"),
            actor=request.user,
            request=request,
        )
        return self.respond(pk)


class AssignmentSerializer(serializers.ModelSerializer):
    previous_membership_id = serializers.UUIDField(read_only=True, allow_null=True)
    new_membership_id = serializers.UUIDField(read_only=True, allow_null=True)
    previous_team_id = serializers.UUIDField(read_only=True, allow_null=True)
    new_team_id = serializers.UUIDField(read_only=True, allow_null=True)
    new_assignee_name = serializers.SerializerMethodField()
    new_team_name = serializers.CharField(source="new_team.name", read_only=True, default=None)
    assigned_by_email = serializers.EmailField(
        source="assigned_by.email", read_only=True, default=None
    )

    class Meta:
        model = ConversationAssignment
        fields = [
            "id",
            "previous_membership_id",
            "previous_team_id",
            "new_membership_id",
            "new_team_id",
            "new_assignee_name",
            "new_team_name",
            "assigned_by_email",
            "created_at",
        ]
        read_only_fields = fields

    def get_new_assignee_name(self, obj: ConversationAssignment) -> str | None:
        membership = obj.new_membership
        if membership is None:
            return None
        return membership.user.full_name or membership.user.email


class ConversationAssignmentHistoryView(_ConversationObjectMixin, APIView):
    required_permissions = {"get": "conversations.view"}

    def get(self, request: Request, pk: str) -> Response:
        history = (
            ConversationAssignment.objects.filter(conversation=self.conversation(pk))
            .select_related("new_membership__user", "new_team", "assigned_by")
            .order_by("-created_at")[:100]
        )
        return Response(AssignmentSerializer(history, many=True).data)


class ConversationTagView(_ConversationObjectMixin, APIView):
    """POST {"tag_id"} adds; DELETE /<tag_id>/ removes (tag of the same business)."""

    required_permissions = {"post": "messages.send", "delete": "messages.send"}

    def post(self, request: Request, pk: str) -> Response:
        tag_conversation(self.conversation(pk), request.data.get("tag_id"), add=True)
        return self.respond(pk)

    def delete(self, request: Request, pk: str, tag_id: str) -> Response:
        tag_conversation(self.conversation(pk), tag_id, add=False)
        return self.respond(pk)


class AssigneeOptionsView(OrganizationScopedMixin, APIView):
    """Members who may be assigned conversations of the selected business."""

    required_permissions = {"get": "conversations.view"}

    def get(self, request: Request) -> Response:
        from apps.teams.models import Team

        if self.business is None:
            raise DomainError("Select a business (X-Business-ID).", details={})
        members = []
        for membership in Membership.objects.filter(
            organization=self.organization, status=Membership.Status.ACTIVE
        ).select_related("user", "role"):
            allowed = accessible_business_ids(membership)
            if allowed is None or self.business.pk in allowed:
                members.append(
                    {
                        "membership_id": str(membership.pk),
                        "name": membership.user.full_name or membership.user.email,
                        "email": membership.user.email,
                        "role": membership.role.key,
                    }
                )
        teams = [
            {"id": str(t.pk), "name": t.name}
            for t in Team.objects.filter(organization=self.organization).order_by("name")
        ]
        return Response({"members": members, "teams": teams})
