"""Connect a Meta WhatsApp Business Account to a business of an organization.

    python manage.py connect_meta_waba --organization eko --business kiosk --waba-id 1234567890 \\
        [--credential-ref env:PROVIDER_SECRET_META_SYSTEM_USER_TOKEN] \\
        [--portfolio-id 998877] [--name "Kiosk WABA"] [--no-subscribe]

Performs REAL Graph API calls: GET /{WABA_ID} (connection check),
GET/POST /{WABA_ID}/subscribed_apps (webhook subscription) and
GET /{WABA_ID}/phone_numbers (sync). The token itself is never passed on the
command line — only a reference to where it lives.
"""

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.businesses.models import Business
from apps.organizations.models import Organization
from apps.whatsapp.models import ProviderCredential
from apps.whatsapp.services import connect_business_account, create_credential_reference
from core.exceptions import DomainError
from core.secrets import SecretReferenceError
from providers.whatsapp.errors import ProviderError


class Command(BaseCommand):
    help = "Connect a Meta WABA (and its phone numbers) to a business."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--organization", required=True, help="organization slug")
        parser.add_argument("--business", required=True, help="business slug (e.g. kiosk)")
        parser.add_argument("--waba-id", required=True)
        parser.add_argument("--credential-ref", default=None)
        parser.add_argument("--portfolio-id", default="")
        parser.add_argument("--name", default="")
        parser.add_argument("--no-subscribe", action="store_true")

    def handle(self, *args: Any, **opts: Any) -> None:
        organization = Organization.objects.filter(slug=opts["organization"]).first()
        if organization is None:
            raise CommandError("organization not found")
        business = Business.objects.filter(organization=organization, slug=opts["business"]).first()
        if business is None:
            raise CommandError("business not found (create it with create_business)")
        reference = opts["credential_ref"] or settings.META_ACCESS_TOKEN_REFERENCE
        if not reference:
            raise CommandError(
                "CONFIGURATION REQUIRED: pass --credential-ref or set META_ACCESS_TOKEN_REFERENCE"
            )

        credential = (
            ProviderCredential.objects.for_organization(organization)
            .filter(secret_reference=reference, status=ProviderCredential.Status.ACTIVE)
            .first()
        )
        try:
            if credential is None:
                credential = create_credential_reference(
                    organization=organization,
                    label=f"Meta system user ({reference})",
                    secret_reference=reference,
                )
            account = connect_business_account(
                organization=organization,
                business=business,
                external_id=opts["waba_id"],
                credential=credential,
                name=opts["name"],
                business_portfolio_external_id=opts["portfolio_id"],
                subscribe=not opts["no_subscribe"],
            )
        except SecretReferenceError as exc:
            raise CommandError(str(exc)) from exc
        except ProviderError as exc:
            raise CommandError(
                f"Meta API error [{exc.code} {exc.provider_code}]: {exc.message}"
            ) from exc
        except DomainError as exc:
            raise CommandError(f"{exc.code}: {exc.message}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"connected WABA {account.external_id} to business '{business.slug}' "
                f"(connection={account.connection_state}, sync={account.sync_state})"
            )
        )
        for number in account.phone_numbers.order_by("display_phone_number"):
            self.stdout.write(
                f"  {number.display_phone_number}  id={number.external_id}  "
                f"status={number.status}  quality={number.quality_rating or '-'}"
            )
