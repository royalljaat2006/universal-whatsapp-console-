"""Template Studio: plain-English content -> checks -> provider components.

Every limit and rule below is from Meta's own documentation (read 2026-10-12:
developers.facebook.com/documentation/business-messaging/whatsapp/templates
overview, components, template-review) and is the *only* thing the console
enforces about Meta's side. We do not guess what Meta will approve: content
review (policy, spam, category fit) happens at Meta, and the studio says so.

Supported by this integration (and so by the studio): categories MARKETING and
UTILITY; positional or named variables; a TEXT header; a body; a footer; and
QUICK_REPLY, URL and PHONE_NUMBER buttons. Not supported, and stated as such
rather than hidden: AUTHENTICATION templates (one-time-code format), media /
location headers (need Meta's Resumable Upload handle, which this console
cannot create), and COPY_CODE / other button kinds.

Pure functions, no I/O: the same function backs live checks, draft saving and
the final submit, so the preview, the checks and what is sent cannot drift.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

# --- Meta documented limits -------------------------------------------------
NAME_MAX = 512
NAME_PATTERN = re.compile(r"^[a-z0-9_]+$")
HEADER_TEXT_MAX = 60
HEADER_VARIABLES_MAX = 1
BODY_MAX = 1024
FOOTER_MAX = 60
BUTTONS_MAX = 10
QUICK_REPLY_MAX = 10
URL_BUTTONS_MAX = 2
PHONE_BUTTONS_MAX = 1
BUTTON_TEXT_MAX = 25
URL_MAX = 2000
PHONE_MAX = 20
TEMPLATES_PER_WABA_UNVERIFIED = 250
CREATIONS_PER_WABA_PER_HOUR = 100
REVIEW_HOURS = 24

CATEGORIES: list[dict[str, Any]] = [
    {
        "value": "UTILITY",
        "label": "Update about something the customer did or asked for",
        "when": "Order and delivery updates, appointment reminders, payment receipts, account changes.",
        "supported": True,
    },
    {
        "value": "MARKETING",
        "label": "Promotion, offer or announcement",
        "when": "Sales, discounts, new products, events. Only message people who opted in.",
        "supported": True,
    },
    {
        "value": "AUTHENTICATION",
        "label": "One-time code",
        "when": "Login or verification codes.",
        "supported": False,
        "note": (
            "Authentication templates use a special one-time-code format that the Template "
            "Studio does not support yet. Create them in WhatsApp Manager."
        ),
    },
]

# Meta's supported template languages, trimmed to the common set.
LANGUAGES: list[tuple[str, str]] = [
    ("en_US", "English (US)"),
    ("en_GB", "English (UK)"),
    ("en", "English"),
    ("hi", "Hindi"),
    ("bn", "Bengali"),
    ("ta", "Tamil"),
    ("te", "Telugu"),
    ("mr", "Marathi"),
    ("gu", "Gujarati"),
    ("kn", "Kannada"),
    ("ml", "Malayalam"),
    ("pa", "Punjabi"),
    ("ur", "Urdu"),
    ("es", "Spanish"),
    ("es_MX", "Spanish (Mexico)"),
    ("pt_BR", "Portuguese (Brazil)"),
    ("pt_PT", "Portuguese (Portugal)"),
    ("fr", "French"),
    ("de", "German"),
    ("it", "Italian"),
    ("nl", "Dutch"),
    ("ru", "Russian"),
    ("tr", "Turkish"),
    ("ar", "Arabic"),
    ("id", "Indonesian"),
    ("vi", "Vietnamese"),
    ("th", "Thai"),
    ("fil", "Filipino"),
    ("zh_CN", "Chinese (Simplified)"),
    ("zh_HK", "Chinese (Hong Kong)"),
    ("zh_TW", "Chinese (Traditional)"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
]
LANGUAGE_CODES = {code for code, _ in LANGUAGES}

SUPPORTED_CATEGORIES = {c["value"] for c in CATEGORIES if c["supported"]}
BUTTON_TYPES = ("QUICK_REPLY", "URL", "PHONE_NUMBER")

# Starter examples the person can load and edit. They are not approved
# templates and nothing says Meta will approve them.
STARTERS: list[dict[str, Any]] = [
    {
        "id": "order_shipped",
        "title": "Order has shipped",
        "category": "UTILITY",
        "content": {
            "header_text": "",
            "body": "Hi {{1}}, your order {{2}} has shipped and should arrive soon.",
            "examples": {"1": "Asha", "2": "ORD-1042"},
            "footer": "",
            "buttons": [],
        },
    },
    {
        "id": "appointment_reminder",
        "title": "Appointment reminder",
        "category": "UTILITY",
        "content": {
            "header_text": "",
            "body": "Hello {{1}}, this is a reminder of your appointment on {{2}}. Reply to confirm or reschedule.",
            "examples": {"1": "Ravi", "2": "Friday at 4 pm"},
            "footer": "",
            "buttons": [
                {"type": "QUICK_REPLY", "text": "Confirm"},
                {"type": "QUICK_REPLY", "text": "Reschedule"},
            ],
        },
    },
    {
        "id": "festive_offer",
        "title": "Offer for opted-in customers",
        "category": "MARKETING",
        "content": {
            "header_text": "",
            "body": "Hi {{1}}, we have a special offer for you this week: {{2}}. Reply STOP to opt out.",
            "examples": {"1": "Meera", "2": "10% off your next visit"},
            "footer": "",
            "buttons": [],
        },
    },
]

_VARIABLE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
_STRICT_VARIABLE = re.compile(r"\{\{([^{}\s]+)\}\}")
_NAMED = re.compile(r"^[a-z_]+$")


@dataclass
class Issue:
    code: str
    field: str
    message: str


@dataclass
class Validation:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    components: list[dict[str, Any]] = field(default_factory=list)
    preview: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def variables_in(text: str) -> list[str]:
    """Names/numbers of the variables written in ``text``, in order, without duplicates."""
    seen: list[str] = []
    for match in _VARIABLE.finditer(text or ""):
        key = match.group(1)
        if key not in seen:
            seen.append(key)
    return seen


def content_hash(
    *, name: str, language: str, category: str, parameter_format: str, components: list[dict]
) -> str:
    raw = json.dumps(
        [name, language, category, parameter_format, components],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def normalise_content(content: dict[str, Any] | None) -> dict[str, Any]:
    """Only the fields the studio knows, with the right types. Anything else
    in the request is dropped, never forwarded to Meta."""
    content = content or {}
    buttons = []
    for raw in content.get("buttons") or []:
        if not isinstance(raw, dict):
            continue
        button: dict[str, str] = {
            "type": _clean(raw.get("type")).upper(),
            "text": _clean(raw.get("text")),
        }
        for key in ("url", "url_example", "phone_number"):
            if key in raw:
                button[key] = _clean(raw.get(key))
        buttons.append(button)
    examples = content.get("examples") or {}
    return {
        "header_text": _clean(content.get("header_text")),
        "body": _clean(content.get("body")),
        "examples": {str(k): _clean(v) for k, v in examples.items()}
        if isinstance(examples, dict)
        else {},
        "footer": _clean(content.get("footer")),
        "buttons": buttons,
    }


def _example_text(text: str, examples: dict[str, str]) -> str:
    return _VARIABLE.sub(lambda m: examples.get(m.group(1)) or m.group(0), text)


def _check_variables(
    *,
    text: str,
    where: str,
    label: str,
    fmt: str,
    examples: dict[str, str],
    out: Validation,
    limit: int | None = None,
) -> list[str]:
    """Variable syntax, numbering and examples for one component."""
    keys = variables_in(text)
    raw_tokens = _VARIABLE.findall(text)
    strict = _STRICT_VARIABLE.findall(text)
    if (
        len(strict) != len(raw_tokens)
        or text.count("{{") != text.count("}}")
        or text.count("{{") != len(raw_tokens)
    ):
        out.errors.append(
            Issue(
                "variable_format",
                where,
                f"The {label} has a variable written wrongly. Write each as {{{{1}}}} (no spaces) "
                "with matching double curly brackets.",
            )
        )
        return keys
    if limit is not None and len(keys) > limit:
        out.errors.append(
            Issue("too_many_variables", where, f"The {label} can have at most {limit} variable.")
        )
    if fmt == "POSITIONAL":
        bad = [k for k in keys if not k.isdigit() or k.startswith("0")]
        if bad:
            out.errors.append(
                Issue(
                    "variable_numbering",
                    where,
                    f"Variables in the {label} must be numbers starting at 1, like {{{{1}}}}. "
                    f"Found {{{{{bad[0]}}}}}.",
                )
            )
        else:
            numbers = sorted(int(k) for k in keys)
            if numbers and numbers != list(range(1, len(numbers) + 1)):
                out.errors.append(
                    Issue(
                        "variable_sequence",
                        where,
                        f"Variables in the {label} must go 1, 2, 3 with no gaps. "
                        f"You have {', '.join(str(n) for n in numbers)}.",
                    )
                )
    else:
        bad_named = [k for k in keys if not _NAMED.match(k)]
        if bad_named:
            out.errors.append(
                Issue(
                    "variable_name",
                    where,
                    f"Named variables use only lowercase letters and underscores, like "
                    f"{{{{first_name}}}}. Found {{{{{bad_named[0]}}}}}.",
                )
            )
    for key in keys:
        if not examples.get(key):
            out.errors.append(
                Issue(
                    "example_missing",
                    f"{where}.{key}",
                    f"Give an example value for {{{{{key}}}}}. Meta needs one for every variable "
                    "so its reviewers can see what the message will look like.",
                )
            )
    return keys


def validate(
    *,
    name: str,
    language: str,
    category: str,
    parameter_format: str,
    content: dict[str, Any] | None,
    existing: list[dict[str, str]] | None = None,
    template_count: int = 0,
    business_verified: bool | None = None,
) -> Validation:
    """Check a template against Meta's documented rules and build its components.

    ``existing``: other templates on the same account as dicts with
    ``name``, ``language``, ``body``, ``footer`` (used for the name-taken and
    duplicate-wording rules). ``business_verified`` is None when unknown."""
    out = Validation()
    c = normalise_content(content)
    fmt = parameter_format if parameter_format in ("POSITIONAL", "NAMED") else "POSITIONAL"
    existing = existing or []

    # ---- name / language / category
    if not name:
        out.errors.append(Issue("name_required", "name", "Give the template a name."))
    elif not NAME_PATTERN.match(name):
        out.errors.append(
            Issue(
                "name_format",
                "name",
                "A template name can only use lowercase letters, numbers and underscores "
                "(for example order_shipped).",
            )
        )
    elif len(name) > NAME_MAX:
        out.errors.append(
            Issue("name_length", "name", f"The name can be at most {NAME_MAX} characters.")
        )
    if language not in LANGUAGE_CODES:
        out.errors.append(
            Issue("language_unsupported", "language", "Choose a language from the list.")
        )
    if category not in SUPPORTED_CATEGORIES:
        note = next((x.get("note") for x in CATEGORIES if x["value"] == category), None)
        out.errors.append(
            Issue(
                "category_unsupported",
                "category",
                note or "Choose either an update template or a promotion template.",
            )
        )
    if name and language and any(e["name"] == name and e["language"] == language for e in existing):
        out.errors.append(
            Issue(
                "name_taken",
                "name",
                "This account already has a template with this name in this language. "
                "Choose a different name (for example add _v2).",
            )
        )

    # ---- header
    components: list[dict[str, Any]] = []
    header = c["header_text"]
    out.counts["header"] = {"used": len(header), "limit": HEADER_TEXT_MAX}
    if header:
        if len(header) > HEADER_TEXT_MAX:
            out.errors.append(
                Issue(
                    "header_length",
                    "header",
                    f"The header can be at most {HEADER_TEXT_MAX} characters (it has {len(header)}).",
                )
            )
        h_keys = _check_variables(
            text=header,
            where="header",
            label="header",
            fmt=fmt,
            examples=c["examples"],
            out=out,
            limit=HEADER_VARIABLES_MAX,
        )
        component: dict[str, Any] = {"type": "HEADER", "format": "TEXT", "text": header}
        if h_keys:
            component["example"] = _example_for(h_keys, c["examples"], fmt, "header")
        components.append(component)

    # ---- body
    body = c["body"]
    out.counts["body"] = {"used": len(body), "limit": BODY_MAX}
    b_keys: list[str] = []
    if not body:
        out.errors.append(
            Issue("body_required", "body", "Write the message itself. It is required.")
        )
    else:
        if len(body) > BODY_MAX:
            out.errors.append(
                Issue(
                    "body_length",
                    "body",
                    f"The message can be at most {BODY_MAX} characters (it has {len(body)}).",
                )
            )
        b_keys = _check_variables(
            text=body, where="body", label="message", fmt=fmt, examples=c["examples"], out=out
        )
        if re.match(r"^\{\{[^{}]*\}\}", body) or re.search(r"\{\{[^{}]*\}\}$", body):
            out.errors.append(
                Issue(
                    "variable_at_edge",
                    "body",
                    "A message cannot start or end with a variable. Meta rejects these. "
                    "Add a few words before the first and after the last variable.",
                )
            )
        component = {"type": "BODY", "text": body}
        if b_keys:
            component["example"] = _example_for(b_keys, c["examples"], fmt, "body")
        components.append(component)

    # ---- footer
    footer = c["footer"]
    out.counts["footer"] = {"used": len(footer), "limit": FOOTER_MAX}
    if footer:
        if len(footer) > FOOTER_MAX:
            out.errors.append(
                Issue(
                    "footer_length",
                    "footer",
                    f"The footer can be at most {FOOTER_MAX} characters (it has {len(footer)}).",
                )
            )
        if "{{" in footer or "}}" in footer:
            out.errors.append(
                Issue(
                    "footer_variable",
                    "footer",
                    "A footer is plain text. It cannot contain variables.",
                )
            )
        components.append({"type": "FOOTER", "text": footer})

    # ---- buttons (quick replies first, then the others, as Meta requires)
    buttons = c["buttons"]
    out.counts["buttons"] = {"used": len(buttons), "limit": BUTTONS_MAX}
    if len(buttons) > BUTTONS_MAX:
        out.errors.append(
            Issue("buttons_total", "buttons", f"A template can have at most {BUTTONS_MAX} buttons.")
        )
    quick = [b for b in buttons if b["type"] == "QUICK_REPLY"]
    urls = [b for b in buttons if b["type"] == "URL"]
    phones = [b for b in buttons if b["type"] == "PHONE_NUMBER"]
    for b in buttons:
        if b["type"] not in BUTTON_TYPES:
            out.errors.append(
                Issue("button_type", "buttons", "That button type is not supported here.")
            )
    if len(quick) > QUICK_REPLY_MAX:
        out.errors.append(
            Issue("quick_reply_count", "buttons", f"At most {QUICK_REPLY_MAX} quick replies.")
        )
    if len(urls) > URL_BUTTONS_MAX:
        out.errors.append(
            Issue("url_count", "buttons", f"At most {URL_BUTTONS_MAX} website buttons.")
        )
    if len(phones) > PHONE_BUTTONS_MAX:
        out.errors.append(
            Issue("phone_count", "buttons", f"At most {PHONE_BUTTONS_MAX} call button.")
        )
    api_buttons: list[dict[str, Any]] = []
    for i, b in enumerate([*quick, *urls, *phones]):
        where = f"buttons.{i}"
        if not b["text"]:
            out.errors.append(Issue("button_text", where, "Every button needs text."))
        elif len(b["text"]) > BUTTON_TEXT_MAX:
            out.errors.append(
                Issue(
                    "button_text_length",
                    where,
                    f"Button text can be at most {BUTTON_TEXT_MAX} characters "
                    f"({b['text']!r} has {len(b['text'])}).",
                )
            )
        if b["type"] == "QUICK_REPLY":
            api_buttons.append({"type": "QUICK_REPLY", "text": b["text"]})
        elif b["type"] == "URL":
            url = b.get("url", "")
            if not url:
                out.errors.append(
                    Issue("url_required", where, "A website button needs a web address.")
                )
            elif len(url) > URL_MAX:
                out.errors.append(
                    Issue(
                        "url_length", where, f"The web address can be at most {URL_MAX} characters."
                    )
                )
            elif not re.match(r"^https?://[^\s{}]+(\{\{1\}\})?$", url):
                out.errors.append(
                    Issue(
                        "url_format",
                        where,
                        "Write a full web address starting with https://. It may end with {{1}} "
                        "for one changing part.",
                    )
                )
            btn: dict[str, Any] = {"type": "URL", "text": b["text"], "url": url}
            if url.endswith("{{1}}"):
                example = b.get("url_example", "")
                if not example:
                    out.errors.append(
                        Issue(
                            "url_example_missing",
                            where,
                            "Give an example for the changing part of the web address.",
                        )
                    )
                btn["example"] = [example] if example else []
            api_buttons.append(btn)
        elif b["type"] == "PHONE_NUMBER":
            phone = b.get("phone_number", "")
            if not re.match(r"^\+?\d{6,19}$", phone):
                out.errors.append(
                    Issue(
                        "phone_format",
                        where,
                        f"Write the phone number with country code, at most {PHONE_MAX} characters "
                        "(for example +919876543210).",
                    )
                )
            api_buttons.append({"type": "PHONE_NUMBER", "text": b["text"], "phone_number": phone})
    if api_buttons:
        components.append({"type": "BUTTONS", "buttons": api_buttons})
    if quick and (urls or phones):
        out.warnings.append(
            Issue(
                "desktop_view",
                "buttons",
                "Quick replies together with other button types cannot be viewed on WhatsApp "
                "desktop.",
            )
        )
    if len(buttons) > 3:
        out.warnings.append(
            Issue(
                "many_buttons",
                "buttons",
                "With more than three buttons, WhatsApp shows two and hides the rest behind "
                '"See all options".',
            )
        )

    # ---- duplicate wording (Meta rejects the same body + footer as an existing template)
    if body and any(
        e.get("body") == body and (e.get("footer") or "") == footer
        for e in existing
        if not (e["name"] == name and e["language"] == language)
    ):
        out.errors.append(
            Issue(
                "duplicate_content",
                "body",
                "Another template on this account already has exactly this message and footer. "
                "Meta rejects duplicates: change the wording.",
            )
        )

    # ---- account-level advisories (documented limits; not checks of this template)
    if template_count >= TEMPLATES_PER_WABA_UNVERIFIED and business_verified is not True:
        out.warnings.append(
            Issue(
                "template_limit",
                "account",
                f"This account has {template_count} templates. Accounts whose business is not "
                f"verified with Meta can have up to {TEMPLATES_PER_WABA_UNVERIFIED}.",
            )
        )
    out.warnings.append(
        Issue(
            "meta_review",
            "review",
            f"Meta reviews every template, usually within {REVIEW_HOURS} hours. The console "
            "cannot predict the result: policy, spam and category fit are decided by Meta.",
        )
    )

    out.components = components
    out.preview = {
        "header": _example_text(header, c["examples"]) if header else "",
        "body": _example_text(body, c["examples"]),
        "footer": footer,
        "buttons": [
            {"type": b["type"], "text": b["text"]}
            for b in [*quick, *urls, *phones]
            if b["type"] in BUTTON_TYPES and b["text"]
        ],
        "variables": b_keys,
    }
    return out


def _example_for(keys: list[str], examples: dict[str, str], fmt: str, where: str) -> dict[str, Any]:
    if fmt == "NAMED":
        field_name = f"{where}_text_named_params"
        return {field_name: [{"param_name": k, "example": examples.get(k, "")} for k in keys]}
    ordered = sorted(keys, key=lambda k: int(k) if k.isdigit() else 10_000)
    values = [examples.get(k, "") for k in ordered]
    return {"header_text": values} if where == "header" else {"body_text": [values]}


def hash_for(
    name: str, language: str, category: str, parameter_format: str, validation: Validation
) -> str:
    return content_hash(
        name=name,
        language=language,
        category=category,
        parameter_format=parameter_format
        if parameter_format in ("POSITIONAL", "NAMED")
        else "POSITIONAL",
        components=validation.components,
    )


def issues_payload(issues: list[Issue]) -> list[dict[str, str]]:
    return [{"code": i.code, "field": i.field, "message": i.message} for i in issues]
