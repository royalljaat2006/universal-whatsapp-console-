"""PostgreSQL Row-Level Security — defense in depth under the existing
app-layer tenant isolation (apps.organizations.tenancy.TenantScopedModel /
OrganizationScopedMixin), not a replacement for it.

Why a separate role: Postgres RLS is bypassed by a table's OWNER and by
superusers, always, regardless of policies (fundamental Postgres behaviour,
not a bug). Since `manage.py migrate` runs as the role that creates these
tables, that role would silently bypass every policy below. A NOLOGIN role
(`whatsapp_platform_app`) is created and granted table privileges without
ownership or BYPASSRLS; the policies are created immediately, but only take
effect for connections that actually switch to the restricted role.

Nobody ever connects to Postgres AS this role directly (it stays NOLOGIN
permanently). Instead the app's one connection role is granted MEMBERSHIP in
it (`GRANT whatsapp_platform_app TO <connecting role>`), and
core.middleware.set_rls_organization issues `SET LOCAL ROLE
whatsapp_platform_app` for the current transaction only — reverting
automatically to the owning role when the transaction ends. This is what
makes enforcement always-on with no separate connection string to configure
per environment: migrations, webhook ingestion, Django admin and background
workers never call set_rls_organization, so they keep connecting (and
querying) as the owning role and are completely unaffected; only a request
that resolves a tenant context (apps.organizations.api.scoping.
OrganizationScopedMixin) ever switches role, and only for that one
transaction. See docs/security.md#row-level-security.

Session variable: `app.current_org_id`, also set with SET LOCAL in the same
call — transaction-scoped, safe under connection pooling / CONN_MAX_AGE,
never leaks to the next request reusing a pooled connection. Set by
core.middleware.RowLevelSecurityMiddleware, which wraps tenant API requests
in one transaction and lets OrganizationScopedMixin set the value once the
membership's organization is resolved. An unset/empty value resolves to
NULL, which matches no row (fail closed, not fail open).

EXCLUDED from RLS, deliberately (verified by testing against a real
database, not assumed): ``organizations_role``, ``organizations_membership``
and ``organizations_invitation``. These are the tables that *determine*
tenant context, not tables protected by it — resolving which organization a
request belongs to means querying Membership (by user + the organization id
from the request header) BEFORE any session variable can be set, so a
strict policy on Membership makes that very query return zero rows and
breaks authentication for every request. Invitation acceptance is public by
design (the token in the request body is the credential, not an
organization header) and must remain queryable with no org context at all.
These three keep exactly today's app-layer enforcement (already correct —
see apps.organizations.services.get_active_membership and the invitation
views' own scoping) with no RLS layered on top. ``organizations_organization``
was never in scope (it is not itself a TenantScopedModel) but needs a plain
grant so the app role can look organizations up at all.

Table PRIVILEGES vs. RLS POLICIES are two separate layers: a GRANT decides
whether the role can touch a table at all; a POLICY (only created for
TENANT_TABLES below) further restricts which ROWS it sees within a table
it's allowed to touch. Every table that isn't tenant-scoped — the User
model (``identity_user``; a user can belong to several organizations via
Membership, so it can't carry a single organization_id), Django's own
auth/session/content-type tables, audit logs, and anything added later —
still needs an ordinary CRUD grant for the app to function, or every query
against it fails outright with "permission denied" (discovered exactly
this way against ``identity_user`` during live E2E testing: registration
returned 500 because the role had never been granted any privilege on that
table at all). Enumerating every such table by hand doesn't scale and is
easy to miss one of, so the app role instead gets a blanket grant on every
table in the schema (``GRANT ... ON ALL TABLES IN SCHEMA public``) plus a
matching ``ALTER DEFAULT PRIVILEGES`` so tables created by future migrations
are covered automatically. Isolation for tenant data still comes entirely
from the RLS POLICIES below, not from withholding table-level grants.
"""

from django.db import migrations

APP_ROLE = "whatsapp_platform_app"

# Every apps.organizations.tenancy.TenantScopedModel table (verified via
# apps.get_models() against TenantScopedModel) EXCEPT the three bootstrap
# tables explained above.
TENANT_TABLES = [
    "apikeys_apikey",
    "businesses_business",
    "businesses_businessaccess",
    "teams_team",
    "teams_teammembership",
    "whatsapp_providercredential",
    "whatsapp_whatsappbusinessaccount",
    "whatsapp_whatsappphonenumber",
    "whatsapp_whatsappaccountevent",
    "contacts_tag",
    "contacts_contact",
    "contacts_contactidentity",
    "contacts_contactimportjob",
    "contacts_contactnote",
    "conversations_conversation",
    "conversations_conversationassignment",
    "templates_messagetemplate",
    "templates_messagetemplateversion",
    "media_mediaasset",
    "messaging_message",
    "messaging_messagestatusevent",
    "messaging_messageattachment",
    "campaigns_audience",
    "campaigns_campaign",
    "campaigns_campaignrecipient",
    "campaigns_campaignevent",
    "analytics_messagemetricdaily",
    "analytics_messagemetrichourly",
    "analytics_costmetricdaily",
    "analytics_contactactivitydaily",
    "reports_exportjob",
    "notifications_notification",
]

# Bootstrap tables: the app role needs ordinary access (already correctly
# scoped in application code), but no RLS policy.
BOOTSTRAP_GRANT_ONLY_TABLES = [
    "organizations_organization",
    "organizations_role",
    "organizations_membership",
    "organizations_invitation",
]

POLICY = "tenant_isolation"


def enable_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return  # sqlite (or any non-Postgres) in some test setups: no RLS.
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                    CREATE ROLE {APP_ROLE} NOLOGIN NOBYPASSRLS;
                END IF;
            END
            $$;
            """
        )
        cursor.execute(f"GRANT {APP_ROLE} TO CURRENT_USER;")
        cursor.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE};")
        # Blanket table-level grant: every table (identity_user, Django's own
        # auth/session/content-type tables, audit logs, the bootstrap tables,
        # tenant tables, anything added later) is reachable at all. Row-level
        # isolation for tenant data comes entirely from the POLICY objects
        # created below, not from withholding this grant.
        cursor.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE};")
        cursor.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};")
        cursor.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE};"
        )
        cursor.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE};"
        )
        for table in BOOTSTRAP_GRANT_ONLY_TABLES:
            cursor.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO {APP_ROLE};')
        for table in TENANT_TABLES:
            cursor.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;')
            cursor.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{table}";')
            cursor.execute(
                f"""
                CREATE POLICY {POLICY} ON "{table}"
                USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
                WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid);
                """
            )


def disable_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in TENANT_TABLES:
            cursor.execute(f'DROP POLICY IF EXISTS {POLICY} ON "{table}";')
            cursor.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY;')


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("organizations", "0007_api_keys_permission"),
        ("apikeys", "0001_initial"),
        ("businesses", "0002_backfill_business_scope"),
        ("teams", "0001_initial"),
        ("whatsapp", "0004_whatsappbusinessaccount_template_sync_error_and_more"),
        ("contacts", "0009_contact_erased_at"),
        ("conversations", "0005_conversationassignment"),
        ("templates", "0004_messagetemplate_missing_from_provider"),
        ("media", "0001_initial"),
        ("messaging", "0006_message_msg_business_occurred_idx_and_more"),
        ("campaigns", "0004_campaign_business_required"),
        ("analytics", "0005_cycle3_aggregates"),
        ("reports", "0002_alter_exportjob_kind"),
        ("notifications", "0001_initial"),
    ]

    operations = [migrations.RunPython(enable_rls, disable_rls)]
