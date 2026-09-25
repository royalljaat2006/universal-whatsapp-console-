"""Analytics API (read-only; derived tables only).

GET /api/v1/analytics/overview/     totals, rates, contacts, conversations, cost
GET /api/v1/analytics/timeseries/   ?granularity=day|hour
GET /api/v1/analytics/numbers/      per-number performance
GET /api/v1/analytics/templates/    per-template performance
GET /api/v1/analytics/costs/        ?group_by=business|business_account|phone_number|template|campaign|category|date
GET /api/v1/analytics/failures/     failures grouped by cause + most recent

Common query parameters: ``date_from``/``date_to`` (YYYY-MM-DD, business-local
days; default last 7 days), ``business_account``, ``phone_number``,
``template``, ``campaign``, ``language``. Every id is checked against the
caller's accessible businesses (unknown/foreign -> 404). Without
X-Business-ID the figures cover every business the caller may access.
"""

from datetime import date, timedelta
from typing import Any

from django.utils import timezone
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analytics import queries
from apps.analytics.tracking import business_zone, zone
from apps.businesses.models import Business
from apps.campaigns.models import Campaign
from apps.organizations.api.scoping import OrganizationScopedMixin
from apps.templates.models import MessageTemplate
from apps.whatsapp.models import WhatsAppBusinessAccount, WhatsAppPhoneNumber
from core.exceptions import DomainError, NotFoundError


class _AnalyticsView(OrganizationScopedMixin, APIView):
    required_permissions = {"get": "analytics.view"}

    def _businesses(self) -> list[Business]:
        qs = self.restrict_to_businesses(
            Business.objects.for_organization(self.organization).select_related("organization"),
            "pk",
        )
        return list(qs)

    def _date(self, name: str, default: date) -> date:
        raw = self.request.query_params.get(name)
        if not raw:
            return default
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise DomainError("Invalid date (expected YYYY-MM-DD).", details={name: raw}) from exc

    def _owned(
        self, model: Any, param: str, business_ids: list[Any], field: str = "business_id"
    ) -> Any:
        raw = self.request.query_params.get(param)
        if not raw:
            return None
        obj = (
            model.objects.filter(pk=raw, **{f"{field}__in": business_ids}).first()
            if _is_uuid(raw)
            else None
        )
        if obj is None:
            raise NotFoundError(f"{param.replace('_', ' ')} not found")
        return obj.pk

    def scope(self) -> queries.AnalyticsScope:
        businesses = self._businesses()
        ids = [b.pk for b in businesses]
        tz = (
            business_zone(businesses[0])
            if len(businesses) == 1
            else zone(self.organization.timezone)
        )
        today = timezone.now().astimezone(tz).date()
        date_to = self._date("date_to", today)
        date_from = self._date("date_from", date_to - timedelta(days=6))
        if date_from > date_to:
            raise DomainError("date_from must not be after date_to.")
        if (date_to - date_from).days + 1 > queries.MAX_RANGE_DAYS:
            raise DomainError(f"The date range is limited to {queries.MAX_RANGE_DAYS} days.")
        language = (self.request.query_params.get("language") or "").strip() or None
        return queries.AnalyticsScope(
            business_ids=ids,
            date_from=date_from,
            date_to=date_to,
            tz=tz,
            business_account_id=self._owned(WhatsAppBusinessAccount, "business_account", ids),
            phone_number_id=self._owned(WhatsAppPhoneNumber, "phone_number", ids),
            template_id=self._owned(MessageTemplate, "template", ids),
            campaign_id=self._owned(Campaign, "campaign", ids),
            language=language,
        )

    def envelope(self, scope: queries.AnalyticsScope, data: dict[str, Any]) -> Response:
        return Response(
            {
                "range": {
                    "date_from": scope.date_from.isoformat(),
                    "date_to": scope.date_to.isoformat(),
                    "timezone": str(scope.tz),
                },
                **data,
            }
        )


def _is_uuid(raw: str) -> bool:
    import uuid

    try:
        uuid.UUID(raw)
    except ValueError:
        return False
    return True


class OverviewView(_AnalyticsView):
    def get(self, request: Request) -> Response:
        scope = self.scope()
        return self.envelope(scope, queries.overview(scope))


class TimeseriesView(_AnalyticsView):
    def get(self, request: Request) -> Response:
        scope = self.scope()
        granularity = request.query_params.get("granularity") or (
            "hour" if (scope.date_to - scope.date_from).days < 2 else "day"
        )
        if granularity not in {"hour", "day"}:
            raise DomainError("granularity must be 'hour' or 'day'.")
        if (
            granularity == "hour"
            and (scope.date_to - scope.date_from).days + 1 > queries.MAX_HOURLY_DAYS
        ):
            raise DomainError(f"Hourly data is limited to {queries.MAX_HOURLY_DAYS} days.")
        return self.envelope(scope, queries.timeseries(scope, granularity))


class NumbersView(_AnalyticsView):
    def get(self, request: Request) -> Response:
        scope = self.scope()
        return self.envelope(scope, {"numbers": queries.numbers(scope)})


class TemplatesView(_AnalyticsView):
    def get(self, request: Request) -> Response:
        scope = self.scope()
        return self.envelope(scope, {"templates": queries.templates(scope)})


class CostsView(_AnalyticsView):
    def get(self, request: Request) -> Response:
        scope = self.scope()
        group_by = request.query_params.get("group_by") or "date"
        if group_by not in queries.COST_DIMENSIONS:
            raise DomainError(
                "Unsupported group_by.", details={"allowed": sorted(queries.COST_DIMENSIONS)}
            )
        return self.envelope(scope, queries.costs(scope, group_by))


class FailuresView(_AnalyticsView):
    def get(self, request: Request) -> Response:
        scope = self.scope()
        return self.envelope(scope, queries.failures(scope))
