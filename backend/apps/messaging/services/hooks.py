"""Derived-data hooks run inside the transaction that changed a message.

Every message create / status or timestamp change passes through here so
that derived state follows the source of truth:

- analytics: the message's day is marked dirty for re-aggregation;
- campaigns: the owning CampaignRecipient mirrors the message status.

Both are cheap (one INSERT … ON CONFLICT DO NOTHING, one UPDATE by indexed
key). Imports are local to keep messaging free of module-level dependencies
on the modules that consume it.
"""

from apps.messaging.models import Message


def message_changed(message: Message) -> None:
    from apps.analytics.tracking import mark_message

    mark_message(message)
    if message.campaign_id:
        from apps.campaigns.progress import sync_recipient_from_message

        sync_recipient_from_message(message)
