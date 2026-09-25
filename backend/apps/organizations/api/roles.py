"""Custom roles (organization-scoped, roles.manage).

GET    /api/v1/organizations/current/roles/       list (name, permissions)
POST   /api/v1/organizations/current/roles/       {"name","description"?,"permissions":[...]}
PATCH  /api/v1/organizations/current/roles/<id>/  {"name"?,"description"?,"permissions"?}
DELETE /api/v1/organizations/current/roles/<id>/  refused if system-seeded or still assigned

The five DEFAULT_ROLES seeded on organization creation are ordinary rows here
(``is_system=True`` only blocks deletion, not editing) — permissions.py is a
starting point, not a code-level ceiling.
"""

from typing import Any

from django.db.models import ProtectedError
from rest_framework import generics, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.api.scoping import OrganizationScopedMixin
from apps.organizations.models import Permission, Role
from apps.organizations.permissions import validate_codes
from core.exceptions import ConflictError, DomainError, NotFoundError


class SystemRoleError(DomainError):
    code = "SYSTEM_ROLE_PROTECTED"
    status_code = status.HTTP_403_FORBIDDEN
    default_message = "A default system role cannot be deleted."


class RoleSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = ["id", "key", "name", "description", "is_system", "permissions", "created_at"]
        read_only_fields = ["id", "key", "is_system", "created_at"]

    def get_permissions(self, obj: Role) -> list[str]:
        return sorted(obj.permissions.values_list("code", flat=True))


class WriteRoleSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    description = serializers.CharField(max_length=255, required=False, allow_blank=True)
    permissions = serializers.ListField(child=serializers.CharField(), required=False)


def _validate_permission_codes(codes: set[str]) -> None:
    try:
        validate_codes(codes)
    except ValueError as exc:
        raise serializers.ValidationError({"permissions": [str(exc)]}) from exc


class RoleListCreateView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = Role
    serializer_class = RoleSerializer
    pagination_class = None
    required_permissions = {"get": "roles.manage", "post": "roles.manage"}

    def scope_queryset(self, queryset: Any) -> Any:
        return queryset.prefetch_related("permissions")

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = WriteRoleSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        if "name" not in payload.validated_data:
            raise serializers.ValidationError({"name": ["This field is required."]})
        codes = set(payload.validated_data.get("permissions", []))
        _validate_permission_codes(codes)
        key = payload.validated_data["name"].lower().replace(" ", "-")[:64]
        if Role.objects.for_organization(self.organization).filter(key=key).exists():
            raise ConflictError("A role with an equivalent name already exists.")
        role = Role.objects.create(
            organization=self.organization,
            key=key,
            name=payload.validated_data["name"],
            description=payload.validated_data.get("description", ""),
            is_system=False,
        )
        role.permissions.set(Permission.objects.filter(code__in=codes))
        return Response(RoleSerializer(role).data, status=status.HTTP_201_CREATED)


class RoleDetailView(OrganizationScopedMixin, APIView):
    queryset_model = Role
    required_permissions = {"patch": "roles.manage", "delete": "roles.manage"}

    def _role(self, pk: str) -> Role:
        role = Role.objects.for_organization(self.organization).filter(pk=pk).first()
        if role is None:
            raise NotFoundError("Role not found.")
        return role

    def patch(self, request: Request, pk: str, *args: Any, **kwargs: Any) -> Response:
        role = self._role(pk)
        payload = WriteRoleSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        if "name" in payload.validated_data:
            role.name = payload.validated_data["name"]
        if "description" in payload.validated_data:
            role.description = payload.validated_data["description"]
        role.save(update_fields=["name", "description", "updated_at"])
        if "permissions" in payload.validated_data:
            codes = set(payload.validated_data["permissions"])
            _validate_permission_codes(codes)
            role.permissions.set(Permission.objects.filter(code__in=codes))
        return Response(RoleSerializer(role).data)

    def delete(self, request: Request, pk: str, *args: Any, **kwargs: Any) -> Response:
        role = self._role(pk)
        if role.is_system:
            raise SystemRoleError()
        try:
            role.delete()
        except ProtectedError as exc:
            raise ConflictError("This role is still assigned to one or more members.") from exc
        return Response(status=status.HTTP_204_NO_CONTENT)
