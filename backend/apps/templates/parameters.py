"""Template parameters: what a template needs, validation of supplied values,
and construction of the provider ``components`` payload — all server-side.

Components are stored exactly as synced from Meta (HEADER/BODY/FOOTER/BUTTONS
with ``text``/``format``/``buttons``). Verified rules (2026-09-23):
- parameters are positional ``{{1}}`` or named ``{{first_name}}``
  (``parameter_format`` POSITIONAL | NAMED); named values carry
  ``parameter_name``;
- a TEXT header has at most one parameter; media headers (IMAGE, VIDEO,
  DOCUMENT) take one media parameter;
- URL buttons may have a dynamic suffix parameter, sent as
  ``{"type": "button", "sub_type": "url", "index": "<n>", "parameters": [...]}``.

The browser supplies only values; it never supplies template text or
component structure.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from apps.templates.models import MessageTemplate

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
MEDIA_FORMATS = {"IMAGE": "image", "VIDEO": "video", "DOCUMENT": "document"}
# Internal sanity cap for a single value (not a Meta rule).
MAX_VALUE_LENGTH = 1024


class TemplateParameterError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


@dataclass(frozen=True)
class ParameterSpec:
    component: str  # header | body | button
    key: str  # "1", "2"… or a parameter name; buttons use the button index
    kind: str = "text"  # text | image | video | document
    example: str = ""
    button_index: int | None = None


@dataclass
class TemplateShape:
    parameter_format: str
    header_format: str | None
    header_text: str
    body_text: str
    footer_text: str
    buttons: list[dict[str, Any]] = field(default_factory=list)
    parameters: list[ParameterSpec] = field(default_factory=list)


class UnsupportedTemplateError(ValueError):
    """Template uses components this console cannot send yet (e.g. carousel)."""


def _component(components: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    for component in components:
        if str(component.get("type", "")).upper() == kind:
            return component
    return None


def _placeholders(text: str) -> list[str]:
    seen: list[str] = []
    for match in PLACEHOLDER.findall(text or ""):
        if match not in seen:
            seen.append(match)
    return seen


def _examples(component: dict[str, Any] | None, key: str) -> list[str]:
    example = (component or {}).get("example") or {}
    value = example.get(key)
    if isinstance(value, list) and value and isinstance(value[0], list):
        return [str(v) for v in value[0]]
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def describe(template: MessageTemplate) -> TemplateShape:
    components = list(template.components or [])
    for component in components:
        if str(component.get("type", "")).upper() in {"CAROUSEL", "LIMITED_TIME_OFFER"}:
            raise UnsupportedTemplateError(
                f"{component.get('type')} templates are not supported by the console yet"
            )
    parameter_format = (template.parameter_format or "POSITIONAL").upper()
    header = _component(components, "HEADER")
    body = _component(components, "BODY")
    footer = _component(components, "FOOTER")
    buttons_component = _component(components, "BUTTONS")
    shape = TemplateShape(
        parameter_format=parameter_format,
        header_format=str(header.get("format", "TEXT")).upper() if header else None,
        header_text=str((header or {}).get("text") or ""),
        body_text=str((body or {}).get("text") or ""),
        footer_text=str((footer or {}).get("text") or ""),
        buttons=list((buttons_component or {}).get("buttons") or []),
    )

    if header and shape.header_format == "TEXT":
        examples = _examples(header, "header_text")
        for i, name in enumerate(_placeholders(shape.header_text)[:1]):
            shape.parameters.append(
                ParameterSpec("header", name, "text", examples[i] if i < len(examples) else "")
            )
    elif header and shape.header_format in MEDIA_FORMATS:
        shape.parameters.append(
            ParameterSpec("header", "media", MEDIA_FORMATS[shape.header_format])
        )

    body_examples = _examples(body, "body_text")
    names = _placeholders(shape.body_text)
    if parameter_format == "POSITIONAL":
        names = sorted(names, key=lambda n: int(n) if n.isdigit() else 10_000)
    for i, name in enumerate(names):
        shape.parameters.append(
            ParameterSpec("body", name, "text", body_examples[i] if i < len(body_examples) else "")
        )

    for index, button in enumerate(shape.buttons):
        if str(button.get("type", "")).upper() == "URL" and PLACEHOLDER.search(
            str(button.get("url") or "")
        ):
            example = (button.get("example") or [""])[0] if button.get("example") else ""
            shape.parameters.append(
                ParameterSpec("button", str(index), "text", str(example), button_index=index)
            )
    return shape


@dataclass(frozen=True)
class BuiltTemplate:
    components: list[dict[str, Any]]
    values: dict[str, Any]
    rendered_body: str
    header_media_asset_id: str | None


def _text_value(errors: dict[str, str], path: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        errors[path] = "a non-empty text value is required"
        return ""
    if len(value) > MAX_VALUE_LENGTH:
        errors[path] = f"must be at most {MAX_VALUE_LENGTH} characters"
    return value


def build(template: MessageTemplate, supplied: dict[str, Any] | None) -> BuiltTemplate:
    """Validate ``supplied`` values against the template and build components.

    ``supplied`` shape::

        {"header": {"text": "..."} | {"media_asset_id": "<uuid>"},
         "body": {"1": "Rajesh", "2": "ORD-1"}   # or {"first_name": "Rajesh"}
         "buttons": {"0": "order/88213"}}
    """
    shape = describe(template)
    supplied = supplied or {}
    errors: dict[str, str] = {}
    components: list[dict[str, Any]] = []
    values: dict[str, Any] = {"header": {}, "body": {}, "buttons": {}}
    header_asset: str | None = None
    named = shape.parameter_format == "NAMED"

    def parameter(name: str, text: str) -> dict[str, Any]:
        item: dict[str, Any] = {"type": "text", "text": text}
        if named:
            item["parameter_name"] = name
        return item

    header_specs = [p for p in shape.parameters if p.component == "header"]
    if header_specs:
        spec = header_specs[0]
        header_in = supplied.get("header") or {}
        if spec.kind == "text":
            text = _text_value(errors, "header.text", header_in.get("text"))
            values["header"] = {"text": text}
            components.append({"type": "header", "parameters": [parameter(spec.key, text)]})
        else:
            asset_id = header_in.get("media_asset_id")
            if not asset_id:
                errors["header.media_asset_id"] = f"a {spec.kind} is required for this template"
            else:
                header_asset = str(asset_id)
                values["header"] = {"media_asset_id": header_asset, "kind": spec.kind}
                # Resolved to a provider media id at dispatch time.
                components.append(
                    {
                        "type": "header",
                        "parameters": [{"type": spec.kind, spec.kind: {"asset_id": header_asset}}],
                    }
                )

    body_specs = [p for p in shape.parameters if p.component == "body"]
    body_in = supplied.get("body") or {}
    if not isinstance(body_in, dict):
        errors["body"] = "must be an object keyed by parameter"
        body_in = {}
    unknown = set(body_in) - {p.key for p in body_specs}
    for key in sorted(unknown):
        errors[f"body.{key}"] = "unknown parameter for this template"
    body_params = []
    for spec in body_specs:
        text = _text_value(errors, f"body.{spec.key}", body_in.get(spec.key))
        values["body"][spec.key] = text
        body_params.append(parameter(spec.key, text))
    if body_params:
        components.append({"type": "body", "parameters": body_params})

    buttons_in = supplied.get("buttons") or {}
    for spec in (p for p in shape.parameters if p.component == "button"):
        text = _text_value(errors, f"buttons.{spec.key}", buttons_in.get(spec.key))
        values["buttons"][spec.key] = text
        components.append(
            {
                "type": "button",
                "sub_type": "url",
                "index": spec.key,
                "parameters": [{"type": "text", "text": text}],
            }
        )

    if errors:
        raise TemplateParameterError(errors)

    rendered = PLACEHOLDER.sub(
        lambda m: str(values["body"].get(m.group(1), m.group(0))), shape.body_text
    )
    return BuiltTemplate(
        components=components,
        values=values,
        rendered_body=rendered,
        header_media_asset_id=header_asset,
    )
