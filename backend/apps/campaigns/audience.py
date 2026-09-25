"""Audience segmentation: a small, validated condition language compiled to
ORM filters over the business's contacts.

Definition::

    {"match": "all" | "any",
     "conditions": [
        {"field": "tag", "op": "has" | "not_has", "value": "<tag id>"},
        {"field": "attribute", "key": "city", "op": "eq" | "neq" | "contains" | "exists" | "not_exists", "value": "Pune"},
        {"field": "phone", "op": "starts_with" | "contains", "value": "9198"},
        {"field": "name", "op": "contains", "value": "sharma"},
        {"field": "last_activity" | "last_inbound", "op": "within_days" | "older_than_days" | "never", "value": 7},
        {"field": "conversation_status", "op": "eq", "value": "open"},
        {"field": "opt_in", "op": "eq" | "neq", "value": "opted_in"},
        {"field": "template_received", "op": "has" | "not_has", "value": "<template id>"},
        {"field": "created", "op": "after" | "before", "value": "2026-09-01"}
     ]}

Always applied, whatever the definition says:

- the campaign's business only (a Kiosk campaign can never reach EPS contacts);
- contacts who opted out are excluded;
- contacts with no reachable address (no phone number and no BSUID) are excluded.

Ids in conditions (tags, templates) are validated to belong to the same
business; everything else is whitelisted, so no user input reaches SQL
except as bound parameters.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from apps.contacts.models import Contact, ContactIdentity, Tag
from apps.conversations.models import Conversation
from apps.messaging.models import Direction, Message
from apps.messaging.state import MessageStatus
from apps.templates.models import MessageTemplate
from core.exceptions import DomainError

MAX_CONDITIONS = 20
ATTRIBUTE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")


class AudienceDefinitionError(DomainError):
    code = "AUDIENCE_INVALID"
    status_code = 422
    default_message = "The audience definition is invalid."


@dataclass(frozen=True)
class _Field:
    ops: frozenset[str]


FIELDS: dict[str, _Field] = {
    "tag": _Field(frozenset({"has", "not_has"})),
    "attribute": _Field(frozenset({"eq", "neq", "contains", "exists", "not_exists"})),
    "phone": _Field(frozenset({"starts_with", "contains"})),
    "name": _Field(frozenset({"contains"})),
    "last_activity": _Field(frozenset({"within_days", "older_than_days", "never"})),
    "last_inbound": _Field(frozenset({"within_days", "older_than_days", "never"})),
    "conversation_status": _Field(frozenset({"eq"})),
    "opt_in": _Field(frozenset({"eq", "neq"})),
    "template_received": _Field(frozenset({"has", "not_has"})),
    "created": _Field(frozenset({"after", "before"})),
}


def _fail(index: int, message: str) -> AudienceDefinitionError:
    return AudienceDefinitionError(details={"conditions": {str(index): message}})


def validate_definition(business: Any, definition: Any) -> dict[str, Any]:
    """Return a normalised copy of ``definition`` or raise AudienceDefinitionError."""
    if definition in (None, {}):
        definition = {"match": "all", "conditions": []}
    if not isinstance(definition, dict):
        raise AudienceDefinitionError("The audience must be an object.")
    match = definition.get("match", "all")
    if match not in {"all", "any"}:
        raise AudienceDefinitionError(details={"match": "must be 'all' or 'any'"})
    conditions = definition.get("conditions") or []
    if not isinstance(conditions, list):
        raise AudienceDefinitionError(details={"conditions": "must be a list"})
    if len(conditions) > MAX_CONDITIONS:
        raise AudienceDefinitionError(details={"conditions": f"at most {MAX_CONDITIONS}"})
    normalised = []
    for i, raw in enumerate(conditions):
        if not isinstance(raw, dict):
            raise _fail(i, "must be an object")
        name, op = raw.get("field"), raw.get("op")
        spec = FIELDS.get(str(name))
        if spec is None:
            raise _fail(i, f"unknown field '{name}'")
        if op not in spec.ops:
            raise _fail(i, f"operator must be one of {sorted(spec.ops)}")
        value = raw.get("value")
        item: dict[str, Any] = {"field": name, "op": op}
        if name == "tag":
            if not Tag.objects.filter(pk=_uuid(value, i), business=business).exists():
                raise _fail(i, "tag not found in this business")
            item["value"] = str(value)
        elif name == "template_received":
            if not MessageTemplate.objects.filter(pk=_uuid(value, i), business=business).exists():
                raise _fail(i, "template not found in this business")
            item["value"] = str(value)
        elif name == "attribute":
            key = str(raw.get("key") or "")
            if not ATTRIBUTE_KEY.match(key):
                raise _fail(i, "invalid attribute name")
            item["key"] = key
            if op not in {"exists", "not_exists"}:
                item["value"] = _text(value, i)
        elif name in {"phone", "name"}:
            text = _text(value, i)
            if name == "phone":
                text = re.sub(r"\D", "", text)
                if not text:
                    raise _fail(i, "digits required")
            item["value"] = text
        elif name in {"last_activity", "last_inbound"}:
            if op != "never":
                item["value"] = _days(value, i)
        elif name == "conversation_status":
            if value not in Conversation.Status.values:
                raise _fail(i, f"status must be one of {Conversation.Status.values}")
            item["value"] = value
        elif name == "opt_in":
            if value not in Contact.OptInStatus.values:
                raise _fail(i, f"opt-in must be one of {Contact.OptInStatus.values}")
            item["value"] = value
        elif name == "created":
            try:
                item["value"] = date.fromisoformat(str(value)).isoformat()
            except ValueError as exc:
                raise _fail(i, "date must be YYYY-MM-DD") from exc
        normalised.append(item)
    return {"match": match, "conditions": normalised}


def _uuid(value: Any, index: int) -> str:
    import uuid

    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise _fail(index, "invalid id") from exc


def _text(value: Any, index: int) -> str:
    text = str(value if value is not None else "").strip()
    if not text or len(text) > 200:
        raise _fail(index, "a value of 1-200 characters is required")
    return text


def _days(value: Any, index: int) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise _fail(index, "days must be a whole number") from exc
    if not 0 <= days <= 3650:
        raise _fail(index, "days must be between 0 and 3650")
    return days


def _condition_q(item: dict[str, Any]) -> Q:
    name, op = item["field"], item["op"]
    value: Any = item.get("value")
    if name == "tag":
        q = Q(Exists(Tag.contacts.through.objects.filter(contact_id=OuterRef("pk"), tag_id=value)))
        return q if op == "has" else ~q
    if name == "attribute":
        key = item["key"]
        if op == "exists":
            return Q(attributes__has_key=key)
        if op == "not_exists":
            return ~Q(attributes__has_key=key)
        if op == "eq":
            return Q(**{f"attributes__{key}": value})
        if op == "neq":
            return ~Q(**{f"attributes__{key}": value})
        return Q(**{f"attributes__{key}__icontains": value})
    if name == "phone":
        return (
            Q(phone_number__startswith=value)
            if op == "starts_with"
            else Q(phone_number__contains=value)
        )
    if name == "name":
        return Q(display_name__icontains=value) | Q(profile_name__icontains=value)
    if name in {"last_activity", "last_inbound"}:
        column = "last_message_at" if name == "last_activity" else "last_inbound_at"
        if op == "never":
            return Q(**{f"{column}__isnull": True})
        cutoff = timezone.now() - timedelta(days=int(value))
        if op == "within_days":
            return Q(**{f"{column}__gte": cutoff})
        return Q(**{f"{column}__lt": cutoff})
    if name == "conversation_status":
        return Q(Exists(Conversation.objects.filter(contact_id=OuterRef("pk"), status=value)))
    if name == "opt_in":
        return Q(opt_in_status=value) if op == "eq" else ~Q(opt_in_status=value)
    if name == "template_received":
        q = Q(
            Exists(
                Message.objects.filter(
                    contact_id=OuterRef("pk"),
                    direction=Direction.OUTBOUND,
                    template_id=value,
                ).exclude(status=MessageStatus.FAILED)
            )
        )
        return q if op == "has" else ~q
    if name == "created":
        day = date.fromisoformat(value)
        return Q(created_at__date__gte=day) if op == "after" else Q(created_at__date__lt=day)
    raise AudienceDefinitionError(f"unsupported field {name}")  # pragma: no cover


def reachable_contacts(business: Any) -> QuerySet[Contact]:
    has_bsuid = Exists(
        ContactIdentity.objects.filter(contact_id=OuterRef("pk"), kind=ContactIdentity.Kind.BSUID)
    )
    return (
        Contact.objects.filter(business=business)
        .exclude(opt_in_status=Contact.OptInStatus.OPTED_OUT)
        .filter(Q(phone_number__gt="") | Q(has_bsuid))
    )


def audience_queryset(business: Any, definition: dict[str, Any]) -> QuerySet[Contact]:
    """``definition`` must already be validated for this business."""
    qs = reachable_contacts(business)
    conditions = definition.get("conditions") or []
    if not conditions:
        return qs
    parts = [_condition_q(c) for c in conditions]
    combined = parts[0]
    for part in parts[1:]:
        combined = combined & part if definition.get("match", "all") == "all" else combined | part
    return qs.filter(combined)


def describe(definition: dict[str, Any]) -> list[str]:
    """Human-readable condition list for previews and launch summaries."""
    texts = []
    for c in definition.get("conditions") or []:
        field, op, value = c["field"], c["op"], c.get("value")
        if field == "attribute":
            field = f"attribute '{c['key']}'"
        texts.append(
            " ".join(str(p) for p in (field, op.replace("_", " "), value) if p is not None)
        )
    return texts


def preview(business: Any, definition: dict[str, Any], sample_size: int = 10) -> dict[str, Any]:
    normalised = validate_definition(business, definition)
    qs = audience_queryset(business, normalised)
    total_reachable = reachable_contacts(business).count()
    count = qs.count()
    sample = [
        {
            "id": str(c.pk),
            "name": c.display_name or c.profile_name,
            "phone_number": c.phone_number,
            "opt_in_status": c.opt_in_status,
        }
        for c in qs.order_by("-last_message_at", "pk")[:sample_size]
    ]
    return {
        "definition": normalised,
        "conditions": describe(normalised),
        "match": normalised["match"],
        "count": count,
        "count_is_exact": True,
        "reachable_contacts_in_business": total_reachable,
        "excluded_always": ["opted-out contacts", "contacts without a phone number or BSUID"],
        "sample": sample,
    }
