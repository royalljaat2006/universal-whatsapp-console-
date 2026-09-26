"""WhatsApp account/number services (public interface of the whatsapp module).

This is the only module that turns stored credential references into live
provider references; other modules ask for a SenderRef/AccountRef here.

Lifecycle of a WABA connection::

    create (PENDING) -> verify (GET /{WABA}) -> CONNECTED | ERROR
                     -> subscribe webhooks (POST /{WABA}/subscribed_apps)
                     -> sync numbers (GET /{WABA}/phone_numbers): NEVER -> SYNCING -> SUCCEEDED | FAILED

Provider calls never run inside a database transaction.
"""

import logging
import secrets
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.audit import actions
from apps.audit.services import record as audit
from apps.businesses.models import Business
from apps.organizations.models import Organization
from apps.whatsapp.models import (
    ConnectionState,
    Provider,
    ProviderCredential,
    SyncState,
    WhatsAppBusinessAccount,
    WhatsAppPhoneNumber,
)
from core import crypto
from core.exceptions import (
    ConfigurationRequiredError,
    ConflictError,
    EmbeddedSignupError,
    NotFoundError,
)
from core.secrets import SecretReference
from providers.whatsapp.base import WhatsAppProvider
from providers.whatsapp.errors import ProviderError
from providers.whatsapp.registry import get_provider
from providers.whatsapp.types import AccountRef, PhoneNumberInfo, SenderRef

logger = logging.getLogger(__name__)

# Meta phone status (verbatim) -> internal status. Unknown values map to
# PENDING and are kept verbatim in provider_status.
_PHONE_STATUS_MAP = {
    "CONNECTED": WhatsAppPhoneNumber.Status.CONNECTED,
    "DISCONNECTED": WhatsAppPhoneNumber.Status.DISCONNECTED,
    "FLAGGED": WhatsAppPhoneNumber.Status.FLAGGED,
    "RESTRICTED": WhatsAppPhoneNumber.Status.RESTRICTED,
}


def provider_for(entity: WhatsAppBusinessAccount | WhatsAppPhoneNumber) -> WhatsAppProvider:
    return get_provider(entity.provider)


def account_ref(account: WhatsAppBusinessAccount) -> AccountRef:
    return AccountRef(
        business_account_external_id=account.external_id,
        access_token=account.credential.resolve_secret(),
    )


def sender_ref(phone_number: WhatsAppPhoneNumber) -> SenderRef:
    return SenderRef(
        phone_number_external_id=phone_number.external_id,
        access_token=phone_number.business_account.credential.resolve_secret(),
    )


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def create_credential_reference(
    *, organization: Organization, label: str, secret_reference: str, created_by: Any = None
) -> ProviderCredential:
    SecretReference.parse(secret_reference)  # validates backend + allowed prefix
    credential = ProviderCredential.objects.create(
        organization=organization,
        provider=Provider.META,
        kind=ProviderCredential.Kind.SYSTEM_USER_TOKEN,
        label=label,
        secret_reference=secret_reference,
        created_by=created_by,
    )
    audit(
        actions.CREDENTIAL_CREATED,
        organization=organization,
        actor=created_by,
        resource=credential,
        metadata={"label": label, "backend": "env"},
    )
    return credential


def create_encrypted_credential(
    *, organization: Organization, label: str, access_token: str, created_by: Any = None
) -> ProviderCredential:
    """Store a token supplied through the API, encrypted at rest. The token is
    never returned; only its last four characters are kept as a fingerprint."""
    token = access_token.strip()
    if len(token) < 20:
        raise ConfigurationRequiredError("access token looks invalid")
    credential = ProviderCredential(
        organization=organization,
        provider=Provider.META,
        kind=ProviderCredential.Kind.SYSTEM_USER_TOKEN,
        label=label,
        fingerprint=f"…{token[-4:]}",
        created_by=created_by,
    )
    credential.encrypted_secret = crypto.encrypt(token)
    credential.save()
    audit(
        actions.CREDENTIAL_CREATED,
        organization=organization,
        actor=created_by,
        resource=credential,
        metadata={"label": label, "backend": "encrypted"},
    )
    return credential


# ---------------------------------------------------------------------------
# Business accounts
# ---------------------------------------------------------------------------


def register_business_account(
    *,
    organization: Organization,
    business: Business,
    external_id: str,
    credential: ProviderCredential,
    name: str = "",
    business_portfolio_external_id: str = "",
    actor: Any = None,
    request: Any = None,
) -> WhatsAppBusinessAccount:
    """Create (or re-attach) a WABA record in PENDING state. No provider call;
    verification and sync run afterwards (inline for the CLI, queued for the API)."""
    if business.organization_id != organization.id or credential.organization_id != organization.id:
        raise NotFoundError("business or credential not found")
    with transaction.atomic():
        account, created = WhatsAppBusinessAccount.objects.select_for_update().get_or_create(
            provider=Provider.META,
            external_id=external_id,
            defaults={
                "organization": organization,
                "business": business,
                "credential": credential,
                "name": name,
                "business_portfolio_external_id": business_portfolio_external_id,
            },
        )
        if account.organization_id != organization.id:
            # Never disclose that another tenant owns this WABA.
            raise NotFoundError("business account cannot be connected")
        if not created:
            if account.business_id != business.id:
                raise ConflictError(
                    "this WhatsApp Business Account is already connected to another business"
                )
            account.credential = credential
            account.name = name or account.name
            account.business_portfolio_external_id = (
                business_portfolio_external_id or account.business_portfolio_external_id
            )
            account.status = WhatsAppBusinessAccount.Status.ACTIVE
            account.connection_state = ConnectionState.PENDING
            account.save()
        audit(
            actions.WABA_CONNECTED,
            organization=organization,
            actor=actor,
            resource=account,
            metadata={
                "external_id": external_id,
                "business_id": str(business.pk),
                "created": created,
            },
            request=request,
        )
    return account


def _record_connection_failure(account: WhatsAppBusinessAccount, exc: Exception) -> None:
    code = getattr(exc, "code", type(exc).__name__)
    message = getattr(exc, "message", str(exc))
    WhatsAppBusinessAccount.objects.filter(pk=account.pk).update(
        connection_state=ConnectionState.ERROR,
        connection_error=str(message)[:500],
        connection_error_code=str(code)[:64],
        updated_at=timezone.now(),
    )
    account.refresh_from_db()


def verify_connection(
    account: WhatsAppBusinessAccount, *, actor: Any = None
) -> WhatsAppBusinessAccount:
    """Verify the credential can read the WABA and refresh provider attributes.
    Local fields (name override, metadata, business) are never overwritten."""
    try:
        ref = account_ref(account)
        info = provider_for(account).get_business_account(ref)
        subscribed = provider_for(account).is_app_subscribed(ref)
    except (ProviderError, ConfigurationRequiredError, PermissionError) as exc:
        _record_connection_failure(account, exc)
        logger.warning(
            "waba connection verification failed",
            extra={"waba_id": str(account.pk), "error_code": getattr(exc, "code", "")},
        )
        raise
    now = timezone.now()
    with transaction.atomic():
        locked = WhatsAppBusinessAccount.objects.select_for_update().get(pk=account.pk)
        locked.connection_state = ConnectionState.CONNECTED
        locked.connection_error = ""
        locked.connection_error_code = ""
        locked.last_verified_at = now
        locked.name = locked.name or info.name
        locked.timezone_id = info.timezone_id
        locked.message_template_namespace = info.message_template_namespace
        locked.account_review_status = info.account_review_status
        locked.business_verification_status = info.business_verification_status
        locked.country = info.country
        locked.ownership_type = info.ownership_type
        locked.provider_metadata = {**locked.provider_metadata, "account": info.raw}
        if subscribed and locked.webhooks_subscribed_at is None:
            locked.webhooks_subscribed_at = now
        if not subscribed:
            locked.webhooks_subscribed_at = None
        locked.save()
        audit(
            actions.WABA_VERIFIED,
            organization=locked.organization,
            actor=actor,
            resource=locked,
            metadata={"webhooks_subscribed": subscribed},
        )
    return locked


def subscribe_webhooks(account: WhatsAppBusinessAccount) -> None:
    provider_for(account).subscribe_webhooks(account_ref(account))
    WhatsAppBusinessAccount.objects.filter(pk=account.pk).update(
        webhooks_subscribed_at=timezone.now(), updated_at=timezone.now()
    )
    account.refresh_from_db()


def _apply_phone_info(number: WhatsAppPhoneNumber, info: PhoneNumberInfo) -> None:
    """Provider-owned fields only; ``label``, ``throughput_mps`` and
    ``metadata`` are local and preserved."""
    number.display_phone_number = info.display_phone_number or number.display_phone_number
    number.verified_name = info.verified_name
    number.provider_status = info.status
    number.status = _PHONE_STATUS_MAP.get(info.status.upper(), WhatsAppPhoneNumber.Status.PENDING)
    number.quality_rating = info.quality_rating
    number.name_status = info.name_status
    number.code_verification_status = info.code_verification_status
    number.messaging_limit_tier = info.messaging_limit_tier
    number.provider_metadata = {
        **number.provider_metadata,
        "throughput_level": info.throughput_level,
    }
    number.last_error = ""
    number.last_synced_at = timezone.now()


def sync_phone_numbers(
    account: WhatsAppBusinessAccount, *, actor: Any = None
) -> list[WhatsAppPhoneNumber]:
    """Reconcile the WABA's numbers from the provider.

    - Numbers missing at the provider are marked DISCONNECTED, never deleted.
    - A number already owned by another WABA/tenant is skipped and reported,
      never re-parented silently.
    - Failures leave existing data untouched and set ``sync_state=failed``.
    """
    WhatsAppBusinessAccount.objects.filter(pk=account.pk).update(
        sync_state=SyncState.SYNCING, sync_started_at=timezone.now(), updated_at=timezone.now()
    )
    try:
        infos = provider_for(account).list_phone_numbers(account_ref(account))
    except (ProviderError, ConfigurationRequiredError, PermissionError) as exc:
        WhatsAppBusinessAccount.objects.filter(pk=account.pk).update(
            sync_state=SyncState.FAILED,
            sync_error=f"{getattr(exc, 'code', type(exc).__name__)}: {getattr(exc, 'message', exc)}"[
                :500
            ],
            updated_at=timezone.now(),
        )
        account.refresh_from_db()
        raise

    seen: set[str] = set()
    conflicts: list[str] = []
    synced: list[WhatsAppPhoneNumber] = []
    with transaction.atomic():
        for info in infos:
            seen.add(info.external_id)
            number = (
                WhatsAppPhoneNumber.objects.select_for_update()
                .filter(provider=account.provider, external_id=info.external_id)
                .first()
            )
            if number is None:
                number = WhatsAppPhoneNumber(
                    business_account=account,
                    provider=account.provider,
                    external_id=info.external_id,
                )
            elif number.business_account_id != account.id:
                conflicts.append(info.external_id)
                logger.error(
                    "phone number belongs to another WABA; skipped",
                    extra={"phone_number_external_id": info.external_id},
                )
                continue
            _apply_phone_info(number, info)
            number.save()
            synced.append(number)
        (
            WhatsAppPhoneNumber.objects.filter(business_account=account)
            .exclude(external_id__in=seen)
            .update(status=WhatsAppPhoneNumber.Status.DISCONNECTED, updated_at=timezone.now())
        )
        WhatsAppBusinessAccount.objects.filter(pk=account.pk).update(
            sync_state=SyncState.SUCCEEDED,
            sync_error=(
                f"{len(conflicts)} number(s) belong to another account: {', '.join(conflicts)}"
                if conflicts
                else ""
            )[:500],
            last_synced_at=timezone.now(),
            updated_at=timezone.now(),
        )
        audit(
            actions.PHONE_NUMBERS_SYNCED,
            organization=account.organization,
            actor=actor,
            resource=account,
            metadata={"count": len(synced), "conflicts": conflicts},
        )
    account.refresh_from_db()
    return synced


def connect_business_account(
    *,
    organization: Organization,
    business: Business,
    external_id: str,
    credential: ProviderCredential,
    name: str = "",
    business_portfolio_external_id: str = "",
    subscribe: bool = True,
    actor: Any = None,
) -> WhatsAppBusinessAccount:
    """Synchronous register -> verify -> subscribe -> sync (used by the CLI).
    The API performs the same steps asynchronously (tasks.connect_business_account)."""
    account = register_business_account(
        organization=organization,
        business=business,
        external_id=external_id,
        credential=credential,
        name=name,
        business_portfolio_external_id=business_portfolio_external_id,
        actor=actor,
    )
    run_connection_pipeline(account, subscribe=subscribe, actor=actor)
    account.refresh_from_db()
    return account


def run_connection_pipeline(
    account: WhatsAppBusinessAccount, *, subscribe: bool = True, actor: Any = None
) -> None:
    verify_connection(account, actor=actor)
    account.refresh_from_db()
    if subscribe and account.webhooks_subscribed_at is None:
        subscribe_webhooks(account)
    sync_phone_numbers(account, actor=actor)


# ---------------------------------------------------------------------------
# Embedded Signup
# ---------------------------------------------------------------------------


def complete_embedded_signup(
    *,
    organization: Organization,
    business: Business,
    waba_id: str,
    phone_number_id: str,
    code: str,
    actor: Any = None,
    request: Any = None,
) -> WhatsAppBusinessAccount:
    """Turn an Embedded Signup completion (the JS SDK's FINISH event + the
    authorization code from FB.login) into a connected WABA.

    The code is only valid for ~30 seconds, so the exchange and phone
    registration run synchronously here rather than through a queued task —
    the one deliberate exception to this module's "provider calls never run
    in the request" rule for the *rest* of the connection pipeline (verify /
    subscribe / sync numbers), which the caller still queues afterwards.

    Registering the phone number establishes its two-step verification PIN.
    We generate one and do not keep it: it is not needed for any ongoing
    operation, and if the number already had a *different* PIN from a prior
    registration, this step fails without blocking the rest of the
    connection — sync can still pick up an already-registered number.
    """
    provider = get_provider(Provider.META)
    try:
        token = provider.exchange_code_for_token(code)
    except ProviderError as exc:
        raise EmbeddedSignupError(
            "Meta rejected the Embedded Signup code. It may have expired — try connecting again.",
            details={"provider_code": exc.code},
        ) from exc

    credential = create_encrypted_credential(
        organization=organization,
        label=f"Embedded Signup — WABA {waba_id}",
        access_token=token,
        created_by=actor,
    )

    account = register_business_account(
        organization=organization,
        business=business,
        external_id=waba_id,
        credential=credential,
        actor=actor,
        request=request,
    )

    pin = f"{secrets.randbelow(1_000_000):06d}"
    try:
        provider.register_phone_number(
            SenderRef(phone_number_external_id=phone_number_id, access_token=token), pin=pin
        )
    except ProviderError as exc:
        logger.warning(
            "embedded signup: phone number registration failed (may already be registered)",
            extra={"phone_number_external_id": phone_number_id, "error_code": exc.code},
        )
        reported_code = exc.provider_code or exc.code
        account.provider_metadata = {
            **account.provider_metadata,
            "embedded_signup_phone_registration_error": f"{reported_code}: {exc.message}"[:300],
        }
        account.save(update_fields=["provider_metadata"])
    else:
        audit(
            actions.PHONE_NUMBER_CONNECTED,
            organization=organization,
            actor=actor,
            resource=account,
            metadata={"phone_number_external_id": phone_number_id},
            request=request,
        )

    audit(
        actions.EMBEDDED_SIGNUP_COMPLETED,
        organization=organization,
        actor=actor,
        resource=account,
        metadata={"waba_id": waba_id, "phone_number_external_id": phone_number_id},
        request=request,
    )
    return account
