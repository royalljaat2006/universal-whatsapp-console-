from rest_framework import serializers

from apps.organizations.models import Membership, Organization
from apps.organizations.services import permission_codes


class OrganizationSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "slug", "status", "timezone"]
        read_only_fields = fields


class MyMembershipSerializer(serializers.ModelSerializer):
    """The caller's own membership: what the UI needs to render org switcher
    and feature gating (permissions are advisory in the UI; the API enforces)."""

    organization = OrganizationSummarySerializer(read_only=True)
    role = serializers.CharField(source="role.key", read_only=True)
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = ["id", "organization", "role", "status", "permissions"]
        read_only_fields = fields

    def get_permissions(self, obj: Membership) -> list[str]:
        return sorted(permission_codes(obj))


class MemberSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    role = serializers.CharField(source="role.key", read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "email", "full_name", "role", "status", "joined_at", "created_at"]
        read_only_fields = fields
