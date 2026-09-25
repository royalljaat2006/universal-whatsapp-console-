"""Map template variables to contact data.

Mapping shape (keys follow the template structure from
apps.templates.parameters.describe)::

    {"header":  {"source": "static", "media_asset_id": "<uuid>"}        # media header
              | {"source": "contact.name", "fallback": "Customer"},     # text header
     "body":    {"1": {"source": "contact.name", "fallback": "there"},
                 "2": {"source": "contact.attribute", "key": "plan"},
                 "3": {"source": "static", "value": "30 September"}},
     "buttons": {"0": {"source": "contact.attribute", "key": "order_path"}}}

Sources: ``contact.name`` (display name, else WhatsApp profile name),
``contact.display_name``, ``contact.profile_name``, ``contact.phone_number``,
``contact.attribute`` (+ ``key``), ``static`` (+ ``value``).

Rules: every template variable must be mapped; a value that resolves empty
uses ``fallback``; if there is no fallback the recipient is **skipped** with
``VARIABLE_MISSING`` — a template is never sent with an empty or malformed
variable. Values are re-validated by the template builder per recipient.
"""

import re
from dataclasses import dataclass
from typing import Any

from django.db.models import Q

from apps.media.models import MediaAsset
from apps.templates.models import MessageTemplate
from apps.templates.parameters import (
    ParameterSpec,
    TemplateParameterError,
    UnsupportedTemplateError,
    build,
    describe,
)
from core.exceptions import DomainError

SOURCES = frozenset(
    {
        "contact.name",
        "contact.display_name",
        "contact.profile_name",
        "contact.phone_number",
        "contact.attribute",
        "static",
    }
)
ATTRIBUTE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
MAX_VALUE = 1024
CONTACT_FIELDS = ("pk", "display_name", "profile_name", "phone_number", "attributes")


class VariableMappingError(DomainError):
    code = "VARIABLE_MAPPING_INVALID"
    status_code = 422
    default_message = "The template variable mapping is invalid."


class VariableMissingError(Exception):
    def __init__(self, path: str):
        super().__init__(path)
        self.path = path


def _path(spec: ParameterSpec) -> tuple[str, str | None]:
    if spec.component == "header":
        return "header", None
    return ("body" if spec.component == "body" else "buttons"), spec.key


def _entry(mapping: dict[str, Any], spec: ParameterSpec) -> Any:
    section, key = _path(spec)
    value = mapping.get(section)
    return value if key is None else (value or {}).get(key)


def label(spec: ParameterSpec) -> str:
    section, key = _path(spec)
    return section if key is None else f"{section}.{key}"


def template_specs(template: MessageTemplate) -> list[ParameterSpec]:
    try:
        return describe(template).parameters
    except UnsupportedTemplateError as exc:
        raise VariableMappingError(str(exc)) from exc


def validate_mapping(template: MessageTemplate, business: Any, mapping: Any) -> dict[str, Any]:
    """Return a normalised mapping or raise VariableMappingError (details per path)."""
    mapping = mapping or {}
    if not isinstance(mapping, dict):
        raise VariableMappingError("The mapping must be an object.")
    errors: dict[str, str] = {}
    out: dict[str, Any] = {}
    for spec in template_specs(template):
        path = label(spec)
        entry = _entry(mapping, spec)
        if not isinstance(entry, dict):
            errors[path] = "this template variable must be mapped"
            continue
        source = entry.get("source")
        normalised: dict[str, Any] = {"source": source}
        if spec.kind != "text":
            if source != "static":
                errors[path] = f"a {spec.kind} header needs an uploaded file (source 'static')"
                continue
            asset_id = entry.get("media_asset_id")
            asset = (
                MediaAsset.objects.filter(pk=asset_id, business=business).first()
                if asset_id
                else None
            )
            if asset is None:
                errors[path] = "media file not found in this business"
                continue
            if asset.kind != spec.kind:
                errors[path] = f"a {spec.kind} file is required"
                continue
            normalised["media_asset_id"] = str(asset.pk)
        else:
            if source not in SOURCES:
                errors[path] = f"source must be one of {sorted(SOURCES)}"
                continue
            if source == "static":
                value = str(entry.get("value") or "").strip()
                if not value or len(value) > MAX_VALUE:
                    errors[path] = f"a static value of 1-{MAX_VALUE} characters is required"
                    continue
                normalised["value"] = value
            if source == "contact.attribute":
                key = str(entry.get("key") or "")
                if not ATTRIBUTE_KEY.match(key):
                    errors[path] = "invalid attribute name"
                    continue
                normalised["key"] = key
            fallback = str(entry.get("fallback") or "").strip()
            if len(fallback) > MAX_VALUE:
                errors[path] = f"fallback must be at most {MAX_VALUE} characters"
                continue
            if fallback:
                normalised["fallback"] = fallback
        section, key_name = _path(spec)
        if key_name is None:
            out[section] = normalised
        else:
            out.setdefault(section, {})[key_name] = normalised
    if errors:
        raise VariableMappingError(details={"fields": errors})
    return out


def _source_value(entry: dict[str, Any], contact: dict[str, Any]) -> str:
    source = entry["source"]
    if source == "static":
        return str(entry["value"])
    if source == "contact.name":
        return str(contact.get("display_name") or contact.get("profile_name") or "")
    if source == "contact.attribute":
        value = (contact.get("attributes") or {}).get(entry["key"])
        return "" if value is None else str(value)
    return str(contact.get(source.split(".", 1)[1]) or "")


@dataclass(frozen=True)
class ResolvedVariables:
    parameters: dict[str, Any]
    rendered_body: str


def resolve(
    template: MessageTemplate, mapping: dict[str, Any], contact: dict[str, Any]
) -> ResolvedVariables:
    """Parameters for one contact. Raises VariableMissingError or
    TemplateParameterError (from the builder) — the recipient is then skipped."""
    parameters: dict[str, Any] = {}
    for spec in template_specs(template):
        entry = _entry(mapping, spec)
        section, key = _path(spec)
        if spec.kind != "text":
            parameters["header"] = {"media_asset_id": entry["media_asset_id"]}
            continue
        value = _source_value(entry, contact).strip() or entry.get("fallback", "")
        if not value:
            raise VariableMissingError(label(spec))
        if key is None:
            parameters["header"] = {"text": value}
        else:
            parameters.setdefault(section, {})[key] = value
    built = build(template, parameters)
    return ResolvedVariables(parameters, built.rendered_body)


def missing_filter(template: MessageTemplate, mapping: dict[str, Any]) -> Q | None:
    """Contacts for which at least one variable (without fallback) resolves
    empty — they would be skipped. ``None`` when no contact can be missing."""
    missing: list[Q] = []
    for spec in template_specs(template):
        entry = _entry(mapping, spec)
        if spec.kind != "text" or entry.get("fallback") or entry["source"] == "static":
            continue
        source = entry["source"]
        if source == "contact.name":
            missing.append(Q(display_name="") & Q(profile_name=""))
        elif source == "contact.attribute":
            key = entry["key"]
            missing.append(
                ~Q(attributes__has_key=key)
                | Q(**{f"attributes__{key}": ""})
                | Q(**{f"attributes__{key}": None})
            )
        else:
            missing.append(Q(**{source.split(".", 1)[1]: ""}))
    if not missing:
        return None
    combined = missing[0]
    for q in missing[1:]:
        combined |= q
    return combined


__all__ = [
    "TemplateParameterError",
    "VariableMappingError",
    "VariableMissingError",
    "missing_filter",
    "resolve",
    "validate_mapping",
]
