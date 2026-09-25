"""Seed the permission catalogue.

Future permission additions ship as new migrations calling the same
function (it is idempotent and only upserts; it never deletes codes that
roles may still reference).
"""

from django.db import migrations


def sync_catalogue(apps, schema_editor):
    from apps.organizations.permissions import PERMISSIONS

    Permission = apps.get_model("organizations", "Permission")
    for code, description in PERMISSIONS.items():
        Permission.objects.update_or_create(code=code, defaults={"description": description})


class Migration(migrations.Migration):
    dependencies = [("organizations", "0001_initial")]

    operations = [migrations.RunPython(sync_catalogue, migrations.RunPython.noop)]
