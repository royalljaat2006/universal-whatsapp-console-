"""Contacts, notes and tags API (business-scoped)."""

import re
from typing import Any

from django.db import transaction
from django.db.models import Count, Prefetch, Q, QuerySet
from rest_framework import generics, serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import actions
from apps.audit.services import record as audit
from apps.contacts import management_services as svc
from apps.contacts.models import Contact, ContactIdentity, ContactImportJob, ContactNote, Tag
from apps.conversations.models import Conversation
from apps.media import storage
from apps.organizations.api.scoping import OrganizationScopedMixin
from core.api.pagination import StandardCursorPagination
from core.exceptions import DomainError, NotFoundError

MAX_IMPORT_UPLOAD_BYTES = 5 * 1024 * 1024


class BusinessRequiredError(DomainError):
    code = "BUSINESS_REQUIRED"
    default_message = "Select a business (X-Business-ID) for this action."


class TagSerializer(serializers.ModelSerializer):
    business_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Tag
        fields = ["id", "business_id", "name", "color"]
        read_only_fields = ["id", "business_id"]


class ContactIdentitySerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactIdentity
        fields = ["kind", "value"]
        read_only_fields = fields


class ContactSerializer(serializers.ModelSerializer):
    business_id = serializers.UUIDField(read_only=True)
    identities = ContactIdentitySerializer(many=True, read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    conversation_count = serializers.IntegerField(read_only=True, default=None)
    first_seen_at = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = Contact
        fields = [
            "id",
            "business_id",
            "display_name",
            "profile_name",
            "phone_number",
            "country_code",
            "attributes",
            "opt_in_status",
            "opt_in_source",
            "metadata",
            "identities",
            "tags",
            "conversation_count",
            "first_seen_at",
            "last_message_at",
            "last_inbound_at",
            "updated_at",
        ]
        read_only_fields = fields


class ContactUpdateSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    attributes = serializers.DictField(
        child=serializers.CharField(max_length=500, allow_blank=True), required=False
    )
    opt_in_status = serializers.ChoiceField(choices=Contact.OptInStatus.choices, required=False)
    opt_in_source = serializers.CharField(max_length=100, required=False, allow_blank=True)

    def validate_attributes(self, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 50:
            raise serializers.ValidationError("at most 50 attributes")
        for key in value:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", key):
                raise serializers.ValidationError(f"invalid attribute name '{key}'")
        return value


class NoteSerializer(serializers.ModelSerializer):
    author_email = serializers.EmailField(source="author.email", read_only=True, default=None)

    class Meta:
        model = ContactNote
        fields = ["id", "body", "author_email", "created_at"]
        read_only_fields = ["id", "author_email", "created_at"]


def _contacts(queryset: QuerySet[Contact]) -> QuerySet[Contact]:
    return queryset.annotate(
        conversation_count=Count("conversations", distinct=True)
    ).prefetch_related("identities", Prefetch("tags", queryset=Tag.objects.order_by("name")))


class ContactListView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = Contact
    business_field = "business"
    serializer_class = ContactSerializer
    required_permissions = {"get": "contacts.view"}

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        params = self.request.query_params
        q = (params.get("search") or params.get("q") or "").strip()
        if q:
            digits = "".join(ch for ch in q if ch.isdigit())
            condition = Q(display_name__icontains=q) | Q(profile_name__icontains=q)
            if len(digits) >= 3:
                condition |= Q(phone_number__contains=digits)
            queryset = queryset.filter(condition)
        if params.get("tag"):
            queryset = queryset.filter(tags__id=params["tag"])
        return _contacts(queryset)


class _ContactObjectMixin(OrganizationScopedMixin):
    def contact(self, pk: Any) -> Contact:
        queryset = self.restrict_to_businesses(
            Contact.objects.for_organization(self.organization), "business"
        )
        contact = _contacts(queryset).filter(pk=pk).first()
        if contact is None:
            raise NotFoundError("contact not found")
        return contact


class ContactDetailView(_ContactObjectMixin, APIView):
    required_permissions = {"get": "contacts.view", "patch": "contacts.manage"}

    def get(self, request: Request, pk: str) -> Response:
        return Response(ContactSerializer(self.contact(pk)).data)

    def patch(self, request: Request, pk: str) -> Response:
        contact = self.contact(pk)
        payload = ContactUpdateSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        svc.update_contact(contact, dict(payload.validated_data), actor=request.user)
        return Response(ContactSerializer(self.contact(pk)).data)


class ContactNotesView(_ContactObjectMixin, APIView):
    required_permissions = {"get": "contacts.view", "post": "contacts.manage"}

    def get(self, request: Request, pk: str) -> Response:
        notes = ContactNote.objects.filter(contact=self.contact(pk)).select_related("author")[:200]
        return Response(NoteSerializer(notes, many=True).data)

    def post(self, request: Request, pk: str) -> Response:
        payload = NoteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        note = svc.add_note(self.contact(pk), payload.validated_data["body"], author=request.user)
        return Response(NoteSerializer(note).data, status=status.HTTP_201_CREATED)


class ContactNoteDetailView(_ContactObjectMixin, APIView):
    required_permissions = {"delete": "contacts.manage"}

    def delete(self, request: Request, pk: str, note_id: str) -> Response:
        note = ContactNote.objects.filter(pk=note_id, contact=self.contact(pk)).first()
        if note is None:
            raise NotFoundError("note not found")
        svc.delete_note(note, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ContactTagView(_ContactObjectMixin, APIView):
    """POST {"tag_id"} adds; DELETE /<tag_id>/ removes."""

    required_permissions = {"post": "contacts.manage", "delete": "contacts.manage"}

    def post(self, request: Request, pk: str) -> Response:
        contact = svc.tag_contact(self.contact(pk), request.data.get("tag_id"), add=True)
        return Response(ContactSerializer(self.contact(contact.pk)).data)

    def delete(self, request: Request, pk: str, tag_id: str) -> Response:
        contact = svc.tag_contact(self.contact(pk), tag_id, add=False)
        return Response(ContactSerializer(self.contact(contact.pk)).data)


class ContactHistoryView(_ContactObjectMixin, APIView):
    """Conversations of this contact (all numbers of the business)."""

    required_permissions = {"get": "contacts.view"}

    def get(self, request: Request, pk: str) -> Response:
        from apps.conversations.api import ConversationSerializer, conversation_queryset

        contact = self.contact(pk)
        conversations = conversation_queryset(
            Conversation.objects.filter(contact=contact)
        ).order_by("-last_message_at")[:100]
        return Response(ConversationSerializer(conversations, many=True).data)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


class TagListCreateView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = Tag
    business_field = "business"
    serializer_class = TagSerializer
    pagination_class = None  # bounded per business
    required_permissions = {"get": "contacts.view", "post": "contacts.manage"}

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        q = (self.request.query_params.get("q") or "").strip()
        if q:
            queryset = queryset.filter(name__icontains=q)
        return queryset.order_by("name")[:500]

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        if self.business is None:
            raise BusinessRequiredError()
        payload = TagSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        tag = svc.create_tag(
            business=self.business,
            name=payload.validated_data["name"],
            color=payload.validated_data.get("color", ""),
            actor=request.user,
        )
        return Response(TagSerializer(tag).data, status=status.HTTP_201_CREATED)


class TagDetailView(OrganizationScopedMixin, APIView):
    required_permissions = {"patch": "contacts.manage", "delete": "contacts.manage"}

    def tag(self, pk: Any) -> Tag:
        queryset = self.restrict_to_businesses(
            Tag.objects.for_organization(self.organization), "business"
        )
        tag = queryset.filter(pk=pk).first()
        if tag is None:
            raise NotFoundError("tag not found")
        return tag

    def patch(self, request: Request, pk: str) -> Response:
        payload = TagSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        tag = svc.update_tag(
            self.tag(pk),
            name=payload.validated_data.get("name"),
            color=payload.validated_data.get("color"),
            actor=request.user,
        )
        return Response(TagSerializer(tag).data)

    def delete(self, request: Request, pk: str) -> Response:
        svc.delete_tag(self.tag(pk), actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------


class ContactImportJobSerializer(serializers.ModelSerializer):
    business_id = serializers.UUIDField(read_only=True)
    requested_by_email = serializers.EmailField(
        source="requested_by.email", read_only=True, default=None
    )

    class Meta:
        model = ContactImportJob
        fields = [
            "id",
            "business_id",
            "filename",
            "tag_name",
            "status",
            "total_rows",
            "created_count",
            "updated_count",
            "failed_count",
            "row_errors",
            "error",
            "started_at",
            "finished_at",
            "requested_by_email",
            "created_at",
        ]
        read_only_fields = fields


class ImportPagination(StandardCursorPagination):
    ordering = ("-created_at", "-id")


class ContactImportListCreateView(OrganizationScopedMixin, generics.ListAPIView):
    """POST (multipart ``file``, optional ``tag_name``) queues a CSV import;
    GET lists this business's import jobs, latest first."""

    queryset_model = ContactImportJob
    business_field = "business"
    serializer_class = ContactImportJobSerializer
    pagination_class = ImportPagination
    parser_classes = [MultiPartParser]
    required_permissions = {"get": "contacts.view", "post": "contacts.manage"}

    def scope_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        return queryset.select_related("requested_by")

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        from apps.contacts.tasks import process_contact_import

        if self.business is None:
            raise BusinessRequiredError()
        upload = request.FILES.get("file")
        if upload is None:
            raise DomainError("A file is required.")
        if upload.size > MAX_IMPORT_UPLOAD_BYTES:
            raise DomainError("File too large.")
        tag_name = (request.data.get("tag_name") or "").strip()[:64]
        key = storage.build_key(self.organization.pk, self.business.pk, ".csv")
        stored = storage.save(key, upload.file)
        with transaction.atomic():
            job = ContactImportJob.objects.create(
                organization=self.organization,
                business=self.business,
                storage_key=stored.key,
                filename=(upload.name or "contacts.csv")[:200],
                tag_name=tag_name,
                requested_by=request.user if request.user.is_authenticated else None,
            )
            audit(
                actions.CONTACTS_IMPORT_REQUESTED,
                organization=self.organization,
                actor=request.user,
                resource=job,
                metadata={"business_id": str(self.business.pk), "filename": job.filename},
                request=request,
            )
            job_id = str(job.pk)
            transaction.on_commit(lambda: process_contact_import.delay(job_id))
        return Response(ContactImportJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class ContactImportDetailView(OrganizationScopedMixin, APIView):
    required_permissions = {"get": "contacts.view"}

    def get(self, request: Request, pk: str) -> Response:
        job = (
            self.restrict_to_businesses(
                ContactImportJob.objects.for_organization(self.organization), "business"
            )
            .select_related("requested_by")
            .filter(pk=pk)
            .first()
        )
        if job is None:
            raise NotFoundError("import job not found")
        return Response(ContactImportJobSerializer(job).data)
