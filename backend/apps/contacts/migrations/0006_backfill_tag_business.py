"""Tags become business-scoped. Existing (organization-level) tags are moved
to the organization's oldest business; if the organization has none, a
"Default" business is created. No tag is deleted."""

from django.db import migrations


def backfill(apps, schema_editor):
    Tag = apps.get_model("contacts", "Tag")
    Business = apps.get_model("businesses", "Business")
    for tag in Tag.objects.filter(business__isnull=True):
        business = (
            Business.objects.filter(organization_id=tag.organization_id).order_by("created_at").first()
        )
        if business is None:
            business, _ = Business.objects.get_or_create(
                organization_id=tag.organization_id, slug="default", defaults={"name": "Default"}
            )
        Tag.objects.filter(pk=tag.pk).update(business=business)


class Migration(migrations.Migration):
    dependencies = [
        ("contacts", "0005_contactnote_remove_tag_tag_org_name_unique_and_more"),
        ("businesses", "0002_backfill_business_scope"),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
