from django.contrib import admin

from apps.organizations.models import Membership, Organization, Permission, Role


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "created_at")
    search_fields = ("name", "slug")
    list_filter = ("status",)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "organization", "is_system")
    list_filter = ("is_system",)
    filter_horizontal = ("permissions",)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "status", "joined_at")
    list_filter = ("status",)
    raw_id_fields = ("user", "invited_by")


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("code", "description")
