"""Analytics/campaign indexes on the (potentially very large) message table,
built CONCURRENTLY so writes are not blocked while they are created."""

from django.conf import settings
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("businesses", "0002_backfill_business_scope"),
        ("campaigns", "0003_audience_campaignevent_and_more"),
        ("contacts", "0007_alter_tag_business"),
        ("conversations", "0005_conversationassignment"),
        ("messaging", "0005_backfill_message_business_account"),
        ("organizations", "0005_reports_export_permission"),
        ("templates", "0004_messagetemplate_missing_from_provider"),
        ("whatsapp", "0004_whatsappbusinessaccount_template_sync_error_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="message",
            index=models.Index(
                fields=["business", "occurred_at"], name="msg_business_occurred_idx"
            ),
        ),
        AddIndexConcurrently(
            model_name="message",
            index=models.Index(
                condition=models.Q(("campaign__isnull", False)),
                fields=["campaign", "status"],
                name="msg_campaign_status_idx",
            ),
        ),
    ]
