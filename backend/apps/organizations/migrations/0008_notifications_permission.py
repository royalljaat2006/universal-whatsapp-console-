"""Add ``notifications.view`` / ``notifications.manage`` and grant both to
every existing system role (not just owner/admin) — notifications are a
personal inbox every member should have regardless of role; new
organizations get this from DEFAULT_ROLES."""

from django.db import migrations

CODES = ["notifications.view", "notifications.manage"]


def forwards(apps, schema_editor):
    from apps.organizations.permissions import PERMISSIONS

    Permission = apps.get_model("organizations", "Permission")
    Role = apps.get_model("organizations", "Role")
    for code, description in PERMISSIONS.items():
        Permission.objects.update_or_create(code=code, defaults={"description": description})
    permissions = Permission.objects.filter(code__in=CODES)
    for role in Role.objects.filter(is_system=True):
        role.permissions.add(*permissions)


class Migration(migrations.Migration):
    dependencies = [("organizations", "0007_api_keys_permission")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
