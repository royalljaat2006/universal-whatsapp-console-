"""Small explicit factories for domain objects used across tests."""

from datetime import timedelta
from itertools import count
from typing import Any

from django.utils import timezone

from apps.businesses.models import Business
from apps.businesses.services import create_business
from apps.contacts.services import ContactIdentifiers, resolve_contact
from apps.conversations.services import get_or_create_conversation
from apps.identity.models import User
from apps.organizations.models import Organization
from apps.organizations.services import add_member, create_organization
from apps.templates.models import MessageTemplate
from apps.whatsapp.models import ProviderCredential, WhatsAppBusinessAccount, WhatsAppPhoneNumber

_seq = count(1)
TEST_TOKEN_ENV = "PROVIDER_SECRET_META_TEST_TOKEN"
TEST_TOKEN = "EAAtesttokenvalue1234567890abcdef"


def make_user(email: str | None = None, **extra: Any) -> User:
    n = next(_seq)
    return User.objects.create_user(
        email=email or f"user{n}@example.com", password="correct-horse-battery-staple", **extra
    )


def make_organization(owner: User | None = None, name: str | None = None) -> Organization:
    n = next(_seq)
    return create_organization(name=name or f"Org {n}", slug=f"org-{n}", owner=owner or make_user())


def make_business(organization: Organization, name: str | None = None) -> Business:
    n = next(_seq)
    return create_business(organization=organization, name=name or f"Business {n}", slug=f"biz-{n}")


def default_business(organization: Organization) -> Business:
    return Business.objects.filter(organization=organization).order_by("created_at").first() or (
        make_business(organization, "Default")
    )


def make_member(
    organization: Organization,
    role_key: str,
    user: User | None = None,
    businesses: list[Business] | None = None,
    all_businesses: bool = True,
) -> User:
    """Non-admin roles are least-privilege by default in the product; tests
    opt into all-business visibility explicitly unless ``businesses`` given."""
    from apps.organizations.models import Membership

    user = user or make_user()
    membership = add_member(
        organization=organization, user=user, role_key=role_key, businesses=businesses
    )
    if businesses is None and all_businesses:
        membership.business_scope = Membership.BusinessScope.ALL
        membership.save(update_fields=["business_scope"])
    return user


def make_waba(
    organization: Organization,
    external_id: str | None = None,
    business: Business | None = None,
) -> WhatsAppBusinessAccount:
    n = next(_seq)
    business = business or default_business(organization)
    credential = ProviderCredential.objects.create(
        organization=organization,
        provider="meta",
        kind=ProviderCredential.Kind.SYSTEM_USER_TOKEN,
        label="test",
        secret_reference=f"env:{TEST_TOKEN_ENV}",
    )
    return WhatsAppBusinessAccount.objects.create(
        organization=organization,
        business=business,
        connection_state="connected",
        external_id=external_id or f"10{n:013d}",
        business_portfolio_external_id=f"90{n:013d}",
        credential=credential,
        name=f"WABA {n}",
    )


def make_phone_number(
    waba: WhatsAppBusinessAccount,
    external_id: str | None = None,
    status: str = WhatsAppPhoneNumber.Status.CONNECTED,
) -> WhatsAppPhoneNumber:
    n = next(_seq)
    return WhatsAppPhoneNumber.objects.create(
        business_account=waba,
        external_id=external_id or f"20{n:013d}",
        display_phone_number=f"+91 90000 {n:05d}",
        status=status,
    )


def make_template(
    waba: WhatsAppBusinessAccount, name: str = "order_update", status: str = "APPROVED"
) -> MessageTemplate:
    return MessageTemplate.objects.create(
        business_account=waba,
        external_id=str(next(_seq)),
        name=name,
        language="en",
        category="UTILITY",
        status=status,
        components=[{"type": "BODY", "text": "Order {{1}} shipped"}],
    )


def open_service_window(phone: WhatsAppPhoneNumber, wa_id: str) -> None:
    contact = resolve_contact(
        phone.business,
        ContactIdentifiers(phone_number=wa_id, bsuid_scope=phone.business_account.bsuid_scope),
    )
    conversation, _ = get_or_create_conversation(phone, contact)
    conversation.last_inbound_at = timezone.now() - timedelta(hours=1)
    conversation.save(update_fields=["last_inbound_at"])


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF_BYTES = b"%PDF-1.7\n" + b"0" * 64


def make_media_asset(business: Any, *, kind: str = "image", data: bytes = PNG_BYTES) -> Any:
    from apps.media.rules import rule_for_mime
    from apps.media.services import store_inbound

    mime = {"image": "image/png", "document": "application/pdf"}[kind]
    rule = rule_for_mime(mime)
    asset = store_inbound(
        business=business, data=data, rule=rule, filename=f"file.{mime.split('/')[1]}"
    )
    asset.source = "upload"
    asset.save(update_fields=["source"])
    return asset


def make_rich_template(waba: WhatsAppBusinessAccount, **overrides: Any) -> MessageTemplate:
    """A realistic synced template: IMAGE header, positional body, URL button."""
    defaults: dict[str, Any] = {
        "business_account": waba,
        "external_id": str(next(_seq)),
        "name": "order_shipped_v1",
        "language": "en",
        "category": "UTILITY",
        "status": "APPROVED",
        "parameter_format": "POSITIONAL",
        "components": [
            {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["h"]}},
            {
                "type": "BODY",
                "text": "Hi {{1}}, order {{2}} has shipped.",
                "example": {"body_text": [["Rajesh", "ORD-1"]]},
            },
            {"type": "FOOTER", "text": "Reply STOP to opt out"},
            {
                "type": "BUTTONS",
                "buttons": [
                    {
                        "type": "URL",
                        "text": "Track",
                        "url": "https://x.example/t/{{1}}",
                        "example": ["abc"],
                    },
                    {"type": "QUICK_REPLY", "text": "Help"},
                ],
            },
        ],
    }
    defaults.update(overrides)
    return MessageTemplate.objects.create(**defaults)


def make_contact(
    business: Any,
    phone: str,
    *,
    name: str = "",
    attributes: dict[str, Any] | None = None,
    opt_in: str = "opted_in",
    tags: list[Any] | None = None,
    last_message_days_ago: int | None = None,
) -> Any:
    contact = resolve_contact(business, ContactIdentifiers(phone_number=phone))
    contact.display_name = name
    contact.attributes = attributes or {}
    contact.opt_in_status = opt_in
    if last_message_days_ago is not None:
        contact.last_message_at = timezone.now() - timedelta(days=last_message_days_ago)
    contact.save()
    if tags:
        contact.tags.add(*tags)
    return contact


def make_tag(business: Any, name: str) -> Any:
    from apps.contacts.models import Tag

    return Tag.objects.create(organization=business.organization, business=business, name=name)


def make_campaign_template(waba: WhatsAppBusinessAccount, **overrides: Any) -> MessageTemplate:
    """Positional body with one variable, as used by campaign tests."""
    defaults: dict[str, Any] = {
        "business_account": waba,
        "external_id": str(next(_seq)),
        "name": "account_update",
        "language": "en",
        "category": "UTILITY",
        "status": "APPROVED",
        "parameter_format": "POSITIONAL",
        "components": [
            {
                "type": "BODY",
                "text": "Hello {{1}}, your account has a pending update.",
                "example": {"body_text": [["Asha"]]},
            }
        ],
    }
    defaults.update(overrides)
    return MessageTemplate.objects.create(**defaults)
