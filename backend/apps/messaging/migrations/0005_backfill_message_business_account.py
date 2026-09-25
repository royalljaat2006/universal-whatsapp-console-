"""Fill Message.business_account from the sending/receiving phone number."""

from django.db import migrations
from django.db.models import OuterRef, Subquery


def backfill(apps, schema_editor):
    Message = apps.get_model("messaging", "Message")
    Phone = apps.get_model("whatsapp", "WhatsAppPhoneNumber")
    Message.objects.filter(business_account__isnull=True).update(
        business_account=Subquery(
            Phone.objects.filter(pk=OuterRef("phone_number_id")).values("business_account_id")[:1]
        )
    )


class Migration(migrations.Migration):
    dependencies = [("messaging", "0004_message_business_account_message_campaign_and_more")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
