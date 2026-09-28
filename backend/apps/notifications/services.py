"""Single entry point for creating notifications. Other apps (messaging,
campaigns, whatsapp) call ``notify`` / ``notify_members_with_permission``
instead of touching Notification directly, so muting and realtime fan-out
stay consistent everywhere a notification is raised."""

from typing import Any

from apps.notifications.models import Notification, NotificationPreference
from apps.organizations.models import Membership
from apps.realtime import publisher

KIND_CONVERSATION_ASSIGNED = "conversation.assigned"
KIND_CAMPAIGN_COMPLETED = "campaign.completed"
KIND_CAMPAIGN_FAILED = "campaign.failed"
KIND_ACCOUNT_ALERT = "account.alert"
KIND_CONTACT_IMPORT_FINISHED = "contact_import.finished"

# code -> human label, surfaced to the frontend so preferences can be toggled
# per kind without a schema change each time a new kind is added.
KINDS: dict[str, str] = {
    KIND_CONVERSATION_ASSIGNED: "A conversation is assigned to me",
    KIND_CAMPAIGN_COMPLETED: "A campaign I launched completes",
    KIND_CAMPAIGN_FAILED: "A campaign I launched fails",
    KIND_ACCOUNT_ALERT: "A WhatsApp account needs attention",
    KIND_CONTACT_IMPORT_FINISHED: "A contact import finishes",
}


def _summary(notification: Notification) -> dict[str, Any]:
    return {
        "id": str(notification.pk),
        "kind": notification.kind,
        "title": notification.title,
        "body": notification.body,
        "data": notification.data,
        "read_at": notification.read_at.isoformat() if notification.read_at else None,
        "created_at": notification.created_at.isoformat(),
    }


def _is_muted(membership_id: Any, kind: str) -> bool:
    preference = (
        NotificationPreference.objects.filter(membership_id=membership_id)
        .only("muted_kinds")
        .first()
    )
    return bool(preference and kind in preference.muted_kinds)


def notify(
    *,
    organization: Any,
    recipient: Membership,
    kind: str,
    title: str,
    body: str = "",
    data: dict[str, Any] | None = None,
) -> Notification | None:
    """Creates the notification row and pushes it to the recipient's realtime
    group. Returns None (no row created) when the member has muted this
    kind. Safe to call inside an existing transaction — the row is rolled
    back with everything else if the caller's transaction aborts, exactly
    like the rest of this codebase's domain events."""
    if _is_muted(recipient.pk, kind):
        return None
    notification = Notification.objects.create(
        organization=organization,
        recipient=recipient,
        kind=kind,
        title=title[:200],
        body=body,
        data=data or {},
    )
    publisher.publish_notification_on_commit(recipient.pk, _summary(notification))
    return notification


def notify_members_with_permission(
    *,
    organization: Any,
    permission_code: str,
    kind: str,
    title: str,
    body: str = "",
    data: dict[str, Any] | None = None,
    exclude_user: Any = None,
) -> None:
    """Notifies every ACTIVE member whose role grants ``permission_code``
    (e.g. account-health alerts go to whoever can manage numbers).

    Filters the permission check and the mute check in the database
    (2 queries total, plus 1 bulk insert) instead of looping per membership
    — the naive version would call apps.organizations.services.has_permission
    once per membership (a query each, since permission_codes() memoises per
    Membership *instance*, not per role) and apps.notifications.services.
    notify's mute check again on top of that."""
    exclude_user_id = getattr(exclude_user, "pk", exclude_user)
    memberships = list(
        Membership.objects.filter(
            organization=organization,
            status=Membership.Status.ACTIVE,
            role__permissions__code=permission_code,
        ).exclude(user_id=exclude_user_id)
        if exclude_user_id is not None
        else Membership.objects.filter(
            organization=organization,
            status=Membership.Status.ACTIVE,
            role__permissions__code=permission_code,
        )
    )
    if not memberships:
        return
    muted_membership_ids = set(
        NotificationPreference.objects.filter(
            membership__in=memberships, muted_kinds__contains=[kind]
        ).values_list("membership_id", flat=True)
    )
    notifications = [
        Notification(
            organization=organization,
            recipient=membership,
            kind=kind,
            title=title[:200],
            body=body,
            data=data or {},
        )
        for membership in memberships
        if membership.pk not in muted_membership_ids
    ]
    if not notifications:
        return
    Notification.objects.bulk_create(notifications)
    for notification in notifications:
        publisher.publish_notification_on_commit(notification.recipient_id, _summary(notification))
