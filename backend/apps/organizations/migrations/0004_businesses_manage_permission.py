"""Add the ``businesses.manage`` permission and grant it to existing owner and
admin roles (new organizations get it from DEFAULT_ROLES)."""

from django.db import migrations

CODE = "businesses.manage"


def forwards(apps, schema_editor):
    from apps.organizations.permissions import PERMISSIONS

    Permission = apps.get_model("organizations", "Permission")
    Role = apps.get_model("organizations", "Role")
    for code, description in PERMISSIONS.items():
        Permission.objects.update_or_create(code=code, defaults={"description": description})
    permission = Permission.objects.get(code=CODE)
    for role in Role.objects.filter(key__in=["owner", "admin"], is_system=True):
        role.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [("organizations", "0003_membership_business_scope")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
