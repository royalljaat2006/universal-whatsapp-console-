from django.contrib import admin

from apps.businesses.models import Business, BusinessAccess


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "organization", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "slug")


@admin.register(BusinessAccess)
class BusinessAccessAdmin(admin.ModelAdmin):
    list_display = ("membership", "business", "created_at")
    raw_id_fields = ("membership", "business")
