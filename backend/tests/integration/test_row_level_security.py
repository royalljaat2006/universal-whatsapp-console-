"""PostgreSQL row-level security is the second tenant-isolation layer under the
application-level scoping. These tests talk to the database directly (as the
restricted ``whatsapp_platform_app`` role, exactly as a request does) so they
fail if a policy is missing, wrong, or silently bypassed - app-level tests
cannot see that."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from django.apps import apps
from django.db import ProgrammingError, connection, transaction

from apps.contacts.models import Contact
from apps.organizations.tenancy import TenantScopedModel
from core.middleware import RLS_ROLE
from tests import factories

pytestmark = pytest.mark.django_db

# Tables that DETERMINE tenant context and so carry app-layer enforcement only
# (core/migrations/0001_row_level_security.py explains why).
BOOTSTRAP_TABLES = {"organizations_role", "organizations_membership", "organizations_invitation"}


def _tenant_tables() -> list[str]:
    return sorted(
        model._meta.db_table
        for model in apps.get_models()
        if issubclass(model, TenantScopedModel)
        and not model._meta.abstract
        and model._meta.db_table not in BOOTSTRAP_TABLES
    )


@contextmanager
def as_app_role(organization_id: Any | None) -> Iterator[None]:
    """Mirror core.middleware.set_rls_organization for one block."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL ROLE {RLS_ROLE}")
            cursor.execute(
                "SELECT set_config('app.current_org_id', %s, true)",
                ["" if organization_id is None else str(organization_id)],
            )
        yield
        # Only reached when the block succeeded. On an error the savepoint is
        # rolled back, which reverts the SET LOCALs by itself (and the aborted
        # transaction would reject further commands anyway). On success they
        # would otherwise outlive the savepoint inside the test transaction.
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
            cursor.execute("SELECT set_config('app.current_org_id', '', true)")


def _scalar(sql: str, params: list[Any] | None = None) -> Any:
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        return cursor.fetchone()[0]


def test_every_tenant_model_has_rls_enabled_and_a_policy() -> None:
    """Fails when someone adds a TenantScopedModel without extending the RLS
    migration - the policy would otherwise be silently absent."""
    missing_rls, missing_policy = [], []
    for table in _tenant_tables():
        enabled = _scalar("SELECT relrowsecurity FROM pg_class WHERE relname = %s", [table])
        policies = _scalar(
            "SELECT count(*) FROM pg_policies WHERE schemaname='public' AND tablename = %s",
            [table],
        )
        if not enabled:
            missing_rls.append(table)
        if not policies:
            missing_policy.append(table)
    assert not missing_rls, f"RLS not enabled on: {missing_rls}"
    assert not missing_policy, f"no RLS policy on: {missing_policy}"


def test_app_role_is_not_a_superuser_and_cannot_bypass_rls() -> None:
    row = _scalar("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = %s", [RLS_ROLE])
    assert row is False


class TestIsolationAsTheRestrictedRole:
    @pytest.fixture
    def two_tenants(self) -> tuple[Any, Any, Any, Any]:
        org_a, org_b = factories.make_organization(), factories.make_organization()
        biz_a, biz_b = factories.make_business(org_a), factories.make_business(org_b)
        Contact.objects.create(organization=org_a, business=biz_a, display_name="A1")
        Contact.objects.create(organization=org_a, business=biz_a, display_name="A2")
        Contact.objects.create(organization=org_b, business=biz_b, display_name="B1")
        return org_a, org_b, biz_a, biz_b

    def test_each_tenant_sees_only_its_own_rows(self, two_tenants: tuple[Any, ...]) -> None:
        org_a, org_b, _, _ = two_tenants
        with as_app_role(org_a.pk):
            assert _scalar("SELECT count(*) FROM contacts_contact") == 2
            assert _scalar("SELECT count(*) FROM businesses_business") == 1
        with as_app_role(org_b.pk):
            assert _scalar("SELECT count(*) FROM contacts_contact") == 1

    def test_no_tenant_context_fails_closed(self, two_tenants: tuple[Any, ...]) -> None:
        with as_app_role(None):
            assert _scalar("SELECT count(*) FROM contacts_contact") == 0
            assert _scalar("SELECT count(*) FROM businesses_business") == 0

    def test_no_tenant_table_leaks_foreign_rows(self, two_tenants: tuple[Any, ...]) -> None:
        org_a, _, _, _ = two_tenants
        with as_app_role(org_a.pk):
            for table in _tenant_tables():
                # `table` comes from Django's own model registry, never from input.
                foreign = _scalar(
                    f'SELECT count(*) FROM "{table}" WHERE organization_id <> %s',  # noqa: S608
                    [str(org_a.pk)],
                )
                assert foreign == 0, f"{table} exposes {foreign} foreign row(s)"

    def test_cannot_update_or_delete_another_tenants_rows(
        self, two_tenants: tuple[Any, ...]
    ) -> None:
        org_a, org_b, _, _ = two_tenants
        with as_app_role(org_a.pk), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE contacts_contact SET display_name = 'pwned' WHERE organization_id = %s",
                [str(org_b.pk)],
            )
            assert cursor.rowcount == 0
            cursor.execute(
                "DELETE FROM contacts_contact WHERE organization_id = %s", [str(org_b.pk)]
            )
            assert cursor.rowcount == 0
        assert Contact.objects.filter(organization=org_b, display_name="B1").exists()

    def test_cannot_insert_a_row_into_another_tenant(self, two_tenants: tuple[Any, ...]) -> None:
        org_a, org_b, _, biz_b = two_tenants
        with (
            pytest.raises(ProgrammingError, match="row-level security"),
            as_app_role(org_a.pk),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO contacts_contact (id, organization_id, business_id, display_name, "
                "profile_name, phone_number, country_code, attributes, opt_in_status, "
                "opt_in_source, notes, metadata, created_at, updated_at) "
                "VALUES (gen_random_uuid(), %s, %s, 'x', '', '', '', '{}', 'unknown', '', '', '{}', now(), now())",
                [str(org_b.pk), str(biz_b.pk)],
            )

    def test_cannot_move_a_row_to_another_tenant_by_update(
        self, two_tenants: tuple[Any, ...]
    ) -> None:
        org_a, org_b, _, _ = two_tenants
        with (
            pytest.raises(ProgrammingError, match="row-level security"),
            as_app_role(org_a.pk),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE contacts_contact SET organization_id = %s WHERE display_name = 'A1'",
                [str(org_b.pk)],
            )
