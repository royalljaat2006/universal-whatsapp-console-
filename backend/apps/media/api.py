"""Media API.

POST /api/v1/media/                 upload (multipart ``file``), business context required
GET  /api/v1/media/<id>/            metadata
GET  /api/v1/media/<id>/content/    bytes, streamed after the same business checks

Files are never served by nginx or from a public URL: every read goes through
tenant + business authorization.
"""

from typing import Any

from django.db.models import QuerySet
from django.http import StreamingHttpResponse
from rest_framework import generics, serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.media import storage
from apps.media.models import MediaAsset
from apps.media.rules import MAX_UPLOAD_BYTES
from apps.media.services import MediaRejectedError, store_upload
from apps.organizations.api.scoping import OrganizationScopedMixin
from core.exceptions import DomainError, NotFoundError

INLINE_KINDS = {"image", "audio", "video", "sticker"}


class BusinessRequiredError(DomainError):
    code = "BUSINESS_REQUIRED"
    default_message = "Select a business (X-Business-ID) to upload media."


class MediaAssetSerializer(serializers.ModelSerializer):
    business_id = serializers.UUIDField(read_only=True)
    content_url = serializers.SerializerMethodField()

    class Meta:
        model = MediaAsset
        fields = [
            "id",
            "business_id",
            "kind",
            "mime_type",
            "file_size",
            "original_filename",
            "source",
            "content_url",
            "created_at",
        ]
        read_only_fields = fields

    def get_content_url(self, obj: MediaAsset) -> str:
        return f"/api/v1/media/{obj.pk}/content/"


class MediaUploadView(OrganizationScopedMixin, APIView):
    parser_classes = [MultiPartParser]
    required_permissions = {"post": "messages.send"}

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        if self.business is None:
            raise BusinessRequiredError()
        upload = request.FILES.get("file")
        if upload is None:
            raise MediaRejectedError("A file is required.", details={"reason": "MEDIA_MISSING"})
        if upload.size > MAX_UPLOAD_BYTES:
            raise MediaRejectedError("File too large.", details={"reason": "MEDIA_TOO_LARGE"})
        asset = store_upload(
            business=self.business,
            file=upload.file,
            filename=upload.name or "",
            mime_type=upload.content_type or "",
            size=upload.size,
            actor=request.user,
        )
        return Response(MediaAssetSerializer(asset).data, status=status.HTTP_201_CREATED)


class MediaAssetDetailView(OrganizationScopedMixin, generics.RetrieveAPIView):
    queryset_model = MediaAsset
    business_field = "business"
    serializer_class = MediaAssetSerializer
    required_permissions = {"get": "conversations.view"}


class MediaContentView(OrganizationScopedMixin, APIView):
    required_permissions = {"get": "conversations.view"}

    def _asset(self, pk: str) -> MediaAsset:
        queryset: QuerySet[MediaAsset] = self.restrict_to_businesses(
            MediaAsset.objects.for_organization(self.organization), "business"
        )
        asset = queryset.filter(pk=pk).first()
        if asset is None:
            raise NotFoundError("media not found")
        return asset

    def get(self, request: Request, pk: str) -> StreamingHttpResponse:
        asset = self._asset(pk)
        response = StreamingHttpResponse(
            storage.iter_chunks(asset.storage_key), content_type=asset.mime_type
        )
        disposition = "inline" if asset.kind in INLINE_KINDS else "attachment"
        filename = (asset.original_filename or f"{asset.pk}").replace('"', "")
        response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
        response["Content-Length"] = str(asset.file_size)
        response["X-Content-Type-Options"] = "nosniff"
        # User-supplied content: never let it run as an active document.
        response["Content-Security-Policy"] = "sandbox; default-src 'none'"
        response["Cache-Control"] = "private, max-age=300"
        return response
