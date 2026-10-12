"""WhatsApp setup readiness: what is *actually* done, from real evidence.

Nothing here is stored progress: every step is derived from what the platform
holds (Meta-reported connection state, the numbers Meta returned, signed
webhook events that really arrived, template sync results, and messages
actually sent), so the wizard is resumable by construction and can never claim
a step is done that the evidence does not support.

Step states:

- ``verified``    evidence says it is done
- ``pending``     not done yet / waiting for something; nothing is wrong
- ``failed``      evidence says it is broken (with a plain explanation)
- ``unsupported`` this console or deployment cannot do/check it (stated openly)
- ``unknown``     there is not enough evidence to say; never shown as done

A webhook is never "verified" because of an HTTP request alone. Subscription is
what Meta reports for the WABA; delivery is verified only by a signed event
that was actually received and stored for that WABA.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from django.conf import settings

from apps.whatsapp.models import (
    ConnectionState,
    SyncState,
    WhatsAppBusinessAccount,
    WhatsAppPhoneNumber,
)

State = Literal["verified", "pending", "failed", "unsupported", "unknown"]
Overall = Literal["not_started", "in_progress", "blocked", "ready"]
FixIn = Literal["console", "meta", "administrator", "none"]

WEBHOOK_PATH = "/webhooks/v1/meta/whatsapp/"


@dataclass
class Step:
    id: str
    title: str
    state: State
    detail: str
    next_step: str = ""
    fix_in: FixIn = "none"
    # "connect" | "refresh" | "test_message" | "" : what the console can do right now.
    action: str = ""
    # What the state is based on, shown in Advanced mode.
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WebhookEvidence:
    """Signed, stored deliveries for the accounts in scope."""

    received: int = 0
    last_received_at: datetime | None = None
    failed: int = 0  # stored but could not be processed (failed / dead)


@dataclass(frozen=True)
class TestMessageEvidence:
    """Messages sent through the test-message flow (idempotency prefix ``testmsg:``)."""

    total: int = 0
    delivered: int = 0
    failed: int = 0
    in_flight: int = 0
    last_status: str = ""
    last_at: datetime | None = None


def callback_url() -> str:
    return f"{settings.PUBLIC_BASE_URL}{WEBHOOK_PATH}"


def _is_public(url: str) -> bool:
    lowered = url.lower()
    return lowered.startswith("https://") and not any(
        host in lowered
        for host in ("localhost", "127.0.0.1", "0.0.0.0", ".local")  # noqa: S104
    )


def embedded_signup_capability() -> dict[str, Any]:
    configured = bool(settings.META_APP_ID and settings.META_EMBEDDED_SIGNUP_CONFIG_ID)
    if configured:
        return {
            "state": "verified",
            "detail": "Connecting through Meta's guided sign-up is set up on this server.",
        }
    return {
        "state": "unsupported",
        "detail": (
            "Meta's guided sign-up is not configured on this server, so the Connect button is "
            "unavailable. An administrator can connect an account manually instead."
        ),
    }


def callback_capability() -> dict[str, Any]:
    return {
        "state": "unsupported",
        "detail": (
            "This console cannot read the callback URL saved in your Meta app. Webhook delivery "
            "is confirmed only by events that actually arrive."
        ),
    }


def webhook_server_config() -> Step:
    token = bool(settings.META_WEBHOOK_VERIFY_TOKEN)
    secret = bool(settings.META_APP_SECRET)
    url = callback_url()
    evidence = {
        "callback_url": url,
        "verify_token_configured": token,
        "app_secret_configured": secret,
        "public_https": _is_public(url),
    }
    if not (token and secret):
        missing = " and ".join(
            n for n, ok in (("the verify token", token), ("the app secret", secret)) if not ok
        )
        return Step(
            "webhook_server",
            "Webhook settings on this server",
            "failed",
            f"This server is missing {missing}, so it cannot confirm or accept events from Meta.",
            "Ask your administrator to set them on the server.",
            "administrator",
            evidence=evidence,
        )
    if not evidence["public_https"]:
        return Step(
            "webhook_server",
            "Webhook settings on this server",
            "failed",
            "This server's public address is not a public https address, so Meta cannot reach it.",
            "Ask your administrator to set the public address of this console.",
            "administrator",
            evidence=evidence,
        )
    return Step(
        "webhook_server",
        "Webhook settings on this server",
        "verified",
        "The verify token and app secret are set and the public address is https.",
        evidence=evidence,
    )


def _connection(accounts: list[WhatsAppBusinessAccount]) -> Step:
    if not accounts:
        return Step(
            "connection",
            "Connect your WhatsApp account",
            "pending",
            "No WhatsApp Business Account is connected to this business yet.",
            "Connect your account with Meta's guided sign-up.",
            "console",
            "connect",
        )
    connected = [a for a in accounts if a.connection_state == ConnectionState.CONNECTED]
    evidence = {
        "accounts": len(accounts),
        "connected": len(connected),
        "last_verified_at": max(
            (a.last_verified_at for a in accounts if a.last_verified_at), default=None
        ),
    }
    if connected:
        return Step(
            "connection",
            "Connect your WhatsApp account",
            "verified",
            f"Meta confirmed {len(connected)} connected account"
            f"{'s' if len(connected) != 1 else ''}.",
            evidence=evidence,
        )
    failed = [a for a in accounts if a.connection_state == ConnectionState.ERROR]
    if failed:
        reason = failed[0].connection_error or "Meta did not accept the connection."
        return Step(
            "connection",
            "Connect your WhatsApp account",
            "failed",
            f"Meta could not verify the connection: {reason}",
            "Try again. If it keeps failing, reconnect the account.",
            "console",
            "refresh",
            evidence={**evidence, "error_code": failed[0].connection_error_code},
        )
    if any(a.connection_state == ConnectionState.DISCONNECTED for a in accounts):
        return Step(
            "connection",
            "Connect your WhatsApp account",
            "failed",
            "This WhatsApp account was disconnected.",
            "Connect it again.",
            "console",
            "connect",
            evidence=evidence,
        )
    return Step(
        "connection",
        "Connect your WhatsApp account",
        "pending",
        "The account was added and is waiting for Meta to confirm it.",
        "Check again in a minute.",
        "console",
        "refresh",
        evidence=evidence,
    )


def _numbers(accounts: list[WhatsAppBusinessAccount], numbers: list[WhatsAppPhoneNumber]) -> Step:
    title = "A WhatsApp number to send from"
    connected_accounts = [a for a in accounts if a.connection_state == ConnectionState.CONNECTED]
    if not connected_accounts:
        return Step(
            "numbers",
            title,
            "unknown",
            "Numbers are listed after the account is connected.",
            evidence={"numbers": 0},
        )
    if any(a.sync_state == SyncState.FAILED for a in connected_accounts) and not numbers:
        return Step(
            "numbers",
            title,
            "failed",
            "Meta's list of numbers could not be loaded.",
            "Refresh from Meta.",
            "console",
            "refresh",
            evidence={"sync_error": connected_accounts[0].sync_error},
        )
    if not numbers:
        synced = any(a.sync_state == SyncState.SUCCEEDED for a in connected_accounts)
        return Step(
            "numbers",
            title,
            "pending",
            "Meta reports no phone number on this account yet."
            if synced
            else "The numbers have not been loaded from Meta yet.",
            "Add a number in WhatsApp Manager, then refresh." if synced else "Refresh from Meta.",
            "meta" if synced else "console",
            "refresh",
            evidence={"numbers": 0},
        )
    usable = [n for n in numbers if n.status == WhatsAppPhoneNumber.Status.CONNECTED]
    evidence = {
        "numbers": len(numbers),
        "connected": len(usable),
        "statuses": sorted({n.provider_status or n.status for n in numbers}),
    }
    if usable:
        return Step(
            "numbers",
            title,
            "verified",
            f"{len(usable)} number{'s are' if len(usable) != 1 else ' is'} connected and "
            "can be used to send.",
            evidence=evidence,
        )
    bad = [
        n
        for n in numbers
        if n.status
        in {
            WhatsAppPhoneNumber.Status.FLAGGED,
            WhatsAppPhoneNumber.Status.RESTRICTED,
            WhatsAppPhoneNumber.Status.DISCONNECTED,
        }
    ]
    if bad:
        return Step(
            "numbers",
            title,
            "failed",
            f"Meta reports this number as {(bad[0].provider_status or bad[0].status).lower()}, "
            "so it cannot send.",
            "Open WhatsApp Manager in Meta to see why and how to fix it.",
            "meta",
            "refresh",
            evidence=evidence,
        )
    return Step(
        "numbers",
        title,
        "pending",
        "The number is not fully registered yet (Meta reports it as pending).",
        "Finish registering the number in WhatsApp Manager, then refresh.",
        "meta",
        "refresh",
        evidence=evidence,
    )


def _subscription(accounts: list[WhatsAppBusinessAccount]) -> Step:
    title = "Meta is set to send updates to this console"
    connected = [a for a in accounts if a.connection_state == ConnectionState.CONNECTED]
    if not connected:
        return Step(
            "webhook_subscription",
            title,
            "unknown",
            "This is checked after the account is connected.",
        )
    subscribed = [a for a in connected if a.webhooks_subscribed_at]
    evidence = {
        "subscribed": len(subscribed),
        "accounts": len(connected),
        "last_checked_at": max(
            (a.last_verified_at for a in connected if a.last_verified_at), default=None
        ),
    }
    if len(subscribed) == len(connected):
        return Step(
            "webhook_subscription",
            title,
            "verified",
            "Meta reports this app is subscribed to your WhatsApp account's updates.",
            evidence=evidence,
        )
    if not any(a.last_verified_at for a in connected):
        return Step(
            "webhook_subscription",
            title,
            "unknown",
            "Meta has not been asked yet whether updates are switched on.",
            "Refresh from Meta.",
            "console",
            "refresh",
            evidence=evidence,
        )
    return Step(
        "webhook_subscription",
        title,
        "failed",
        "Meta reports this app is not subscribed to your account's updates, so messages and "
        "delivery reports will not arrive.",
        "Refresh from Meta. If it stays this way, an administrator must subscribe the app.",
        "administrator",
        "refresh",
        evidence=evidence,
    )


def _delivery(subscription: Step, events: WebhookEvidence) -> Step:
    title = "Updates from Meta are arriving"
    evidence: dict[str, Any] = {
        "events_received": events.received,
        "last_received_at": events.last_received_at,
        "events_failed": events.failed,
    }
    if events.received:
        if events.failed:
            return Step(
                "webhook_delivery",
                title,
                "failed",
                f"Updates from Meta are arriving, but {events.failed} could not be processed.",
                "Ask your administrator to check the webhook processing log.",
                "administrator",
                evidence=evidence,
            )
        return Step(
            "webhook_delivery",
            title,
            "verified",
            "A signed update from Meta for this account was received and stored.",
            evidence=evidence,
        )
    if subscription.state == "failed":
        return Step(
            "webhook_delivery",
            title,
            "unknown",
            "Nothing has arrived, and Meta is not subscribed, so this cannot be checked yet.",
            evidence=evidence,
        )
    return Step(
        "webhook_delivery",
        title,
        "pending",
        "No update from Meta has arrived yet. That is normal until the first message, status "
        "change or account alert happens.",
        "Send the test message below: its delivery report is the first real proof.",
        "console",
        evidence=evidence,
    )


def _templates(accounts: list[WhatsAppBusinessAccount], approved: int) -> Step:
    title = "Approved message templates"
    connected = [a for a in accounts if a.connection_state == ConnectionState.CONNECTED]
    if not connected:
        return Step(
            "templates", title, "unknown", "Templates are loaded after the account is connected."
        )
    evidence = {"approved": approved}
    if any(a.template_sync_state == SyncState.FAILED for a in connected) and not approved:
        return Step(
            "templates",
            title,
            "failed",
            "Meta's templates could not be loaded.",
            "Refresh from Meta.",
            "console",
            "refresh",
            evidence=evidence,
        )
    if approved:
        return Step(
            "templates",
            title,
            "verified",
            f"{approved} approved template{'s' if approved != 1 else ''} available.",
            evidence=evidence,
        )
    synced = any(a.template_sync_state == SyncState.SUCCEEDED for a in connected)
    return Step(
        "templates",
        title,
        "pending",
        "No approved template yet. WhatsApp only lets you start a conversation with an "
        "approved template."
        if synced
        else "Templates have not been loaded from Meta yet.",
        "Create a template on the Templates page and wait for Meta's approval."
        if synced
        else "Refresh from Meta.",
        "console",
        evidence=evidence,
    )


def _test_message(prior_ok: bool, evidence: TestMessageEvidence) -> Step:
    title = "A test message that really arrives"
    ev = {
        "sent": evidence.total,
        "delivered": evidence.delivered,
        "failed": evidence.failed,
        "last_status": evidence.last_status,
        "last_at": evidence.last_at,
    }
    if evidence.delivered:
        return Step(
            "test_message",
            title,
            "verified",
            "WhatsApp reported a test message as delivered.",
            evidence=ev,
        )
    if evidence.in_flight:
        return Step(
            "test_message",
            title,
            "pending",
            "A test message was accepted for sending. WhatsApp has not reported it delivered yet.",
            "Wait for the delivery report.",
            "console",
            "test_message",
            evidence=ev,
        )
    if evidence.failed:
        return Step(
            "test_message",
            title,
            "failed",
            "The last test message failed. Open Failed messages to see why.",
            "Fix the cause shown there, then send another test.",
            "console",
            "test_message",
            evidence=ev,
        )
    if not prior_ok:
        return Step(
            "test_message",
            title,
            "pending",
            "Finish the steps above first.",
            evidence=ev,
        )
    return Step(
        "test_message",
        title,
        "pending",
        "No test message has been sent yet.",
        "Preview a test message, approve it, and see it arrive.",
        "console",
        "test_message",
        evidence=ev,
    )


def assess_setup(
    *,
    accounts: list[WhatsAppBusinessAccount],
    numbers: list[WhatsAppPhoneNumber],
    approved_templates: int,
    webhook: WebhookEvidence,
    test_message: TestMessageEvidence,
) -> list[Step]:
    connection = _connection(accounts)
    number_step = _numbers(accounts, numbers)
    subscription = _subscription(accounts)
    delivery = _delivery(subscription, webhook)
    templates = _templates(accounts, approved_templates)
    prior_ok = all(s.state == "verified" for s in (connection, number_step, subscription))
    return [
        connection,
        number_step,
        subscription,
        delivery,
        templates,
        _test_message(prior_ok, test_message),
    ]


# These must be verified for sending to work; templates and first-event are
# helpful but do not by themselves stop a number from sending.
REQUIRED = ("connection", "numbers", "webhook_subscription", "test_message")


def summarize(steps: list[Step], server: Step) -> tuple[Overall, str, str]:
    """(overall, one plain sentence, id of the step to do next)."""
    by_id = {s.id: s for s in steps}
    if by_id["connection"].state == "pending" and not by_id["connection"].evidence:
        return "not_started", "WhatsApp is not connected yet.", "connection"
    problems = [s for s in (*steps, server) if s.state == "failed"]
    if problems:
        return "blocked", f"{problems[0].title}: this needs attention.", problems[0].id
    if all(by_id[i].state == "verified" for i in REQUIRED):
        return "ready", "WhatsApp is set up and a test message arrived.", ""
    nxt = next((s for s in steps if s.state != "verified"), steps[0])
    return "in_progress", f"Next: {nxt.title.lower()}.", nxt.id
