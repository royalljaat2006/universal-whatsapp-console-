from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.billing.models import MessageCost, PricingMarket, PricingRule


@admin.register(PricingMarket)
class PricingMarketAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "provider", "effective_from", "effective_to", "source")
    search_fields = ("code", "name")


@admin.register(PricingRule)
class PricingRuleAdmin(admin.ModelAdmin):
    list_display = (
        "provider",
        "market",
        "category",
        "currency",
        "rate",
        "volume_tier_min",
        "volume_tier_max",
        "effective_from",
        "effective_to",
        "version",
    )
    list_filter = ("provider", "currency", "category")
    search_fields = ("market", "source", "version")


@admin.register(MessageCost)
class MessageCostAdmin(admin.ModelAdmin):
    """Read-only: historical costs are immutable."""

    list_display = ("message", "market", "category", "amount", "currency", "priced_at")
    readonly_fields = [f.name for f in MessageCost._meta.fields]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
