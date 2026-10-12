"""Extend row-level security (0001_row_level_security) to
``templates_templatedraft`` (Template Studio drafts), added after the initial RLS
migration was written. Same role, same policy shape — see 0001 for the full
design rationale."""

from django.db import migrations

APP_ROLE = "whatsapp_platform_app"
TABLE = "templates_templatedraft"
POLICY = "tenant_isolation"


def enable_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY;')
        cursor.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{TABLE}";')
        cursor.execute(
            f"""
            CREATE POLICY {POLICY} ON "{TABLE}"
            USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
            WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid);
            """
        )
        cursor.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{TABLE}" TO {APP_ROLE};')


def disable_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{TABLE}";')
        cursor.execute(f'ALTER TABLE "{TABLE}" DISABLE ROW LEVEL SECURITY;')


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_backuprecord"),
        ("templates", "0005_templatedraft"),
    ]

    operations = [migrations.RunPython(enable_rls, disable_rls)]
