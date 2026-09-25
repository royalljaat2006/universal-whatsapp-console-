from django.contrib import admin

from apps.whatsapp.models import ProviderCredential, WhatsAppBusinessAccount, WhatsAppPhoneNumber


@admin.register(ProviderCredential)
class ProviderCredentialAdmin(admin.ModelAdmin):
    # encrypted_secret is deliberately never displayed or editable here.
    list_display = (
        "label",
        "organization",
        "provider",
        "kind",
        "status",
        "fingerprint",
        "created_at",
    )
    list_filter = ("provider", "status")
    fields = (
        "label",
        "provider",
        "kind",
        "secret_reference",
        "fingerprint",
        "status",
        "expires_at",
    )
    readonly_fields = ("provider", "kind")


@admin.register(WhatsAppBusinessAccount)
class WhatsAppBusinessAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "external_id", "organization", "status", "last_synced_at")
    list_filter = ("status", "provider")
    search_fields = ("name", "external_id")


@admin.register(WhatsAppPhoneNumber)
class WhatsAppPhoneNumberAdmin(admin.ModelAdmin):
    list_display = (
        "display_phone_number",
        "verified_name",
        "external_id",
        "organization",
        "status",
        "quality_rating",
        "last_synced_at",
    )
    list_filter = ("status", "quality_rating")
    search_fields = ("display_phone_number", "external_id", "verified_name")
