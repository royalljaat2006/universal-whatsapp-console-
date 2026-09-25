"""Campaigns belong to a business (the sending number's). Any pre-existing
campaign row is backfilled from its phone number before the column becomes
NOT NULL."""

import django.db.models.deletion
from django.db import migrations, models


def backfill(apps, schema_editor):
    Campaign = apps.get_model("campaigns", "Campaign")
    for campaign in Campaign.objects.filter(business__isnull=True).select_related("phone_number"):
        campaign.business_id = campaign.phone_number.business_id
        campaign.save(update_fields=["business"])


class Migration(migrations.Migration):

    dependencies = [
        ("businesses", "0002_backfill_business_scope"),
        ("campaigns", "0003_audience_campaignevent_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="campaign",
            name="business",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="campaigns",
                to="businesses.business",
            ),
        ),
    ]
