"""API key management (organization-scoped, api_keys.manage).

GET    /api/v1/api-keys/        list (never exposes the hash or full key)
POST   /api/v1/api-keys/        {"name","scopes":[...],"expires_at"?} -> key shown once
DELETE /api/v1/api-keys/<id>/   revoke
"""

from typing import Any

from rest_framework import generics, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.apikeys.models import ApiKey
from apps.apikeys.services import create_api_key, revoke_api_key
from apps.organizations.api.scoping import OrganizationScopedMixin
from core.exceptions import NotFoundError


class ApiKeySerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = ApiKey
        fields = [
            "id",
            "name",
            "key_prefix",
            "scopes",
            "created_by_email",
            "is_active",
            "expires_at",
            "last_used_at",
            "revoked_at",
            "created_at",
        ]
        read_only_fields = fields


class CreateApiKeySerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    scopes = serializers.ListField(child=serializers.CharField(), allow_empty=False)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class ApiKeyListCreateView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = ApiKey
    serializer_class = ApiKeySerializer
    pagination_class = None
    required_permissions = {"get": "api_keys.manage", "post": "api_keys.manage"}

    def scope_queryset(self, queryset: Any) -> Any:
        return queryset.select_related("created_by")

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = CreateApiKeySerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        api_key, raw_key = create_api_key(
            organization=self.organization,
            created_by=request.user,
            name=payload.validated_data["name"],
            scopes=payload.validated_data["scopes"],
            expires_at=payload.validated_data.get("expires_at"),
        )
        body = ApiKeySerializer(api_key).data
        body["key"] = raw_key
        return Response(body, status=status.HTTP_201_CREATED)


class ApiKeyRevokeView(OrganizationScopedMixin, APIView):
    queryset_model = ApiKey
    required_permissions = {"delete": "api_keys.manage"}

    def delete(self, request: Request, pk: str, *args: Any, **kwargs: Any) -> Response:
        api_key = ApiKey.objects.for_organization(self.organization).filter(pk=pk).first()
        if api_key is None:
            raise NotFoundError("API key not found.")
        revoke_api_key(api_key=api_key, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
