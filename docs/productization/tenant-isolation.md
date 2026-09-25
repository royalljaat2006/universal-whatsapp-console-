# Tenant isolation

Strategy (ADR-001, `apps/organizations/tenancy.py`): shared database, shared
schema, `organization_id` on every tenant-owned row. This predates Cycle 4 and
is already the enforcement boundary for every existing resource (contacts,
conversations, messages, templates, campaigns, media, analytics, billing,
audit, webhooks). Cycle 4 adds three new tenant-owned resources
(`Invitation`, `ApiKey`, custom `Role` rows) and extends the boundary rather
than building a new one.

## The three layers (unchanged from pre-Cycle-4)

1. **Model**: every tenant-owned model inherits `TenantScopedModel`
   (`organization` FK, `related_name="+"` — no reverse accessor, so nothing
   can accidentally traverse from `Organization` back into tenant data
   without going through `for_organization()`).
2. **API**: every tenant endpoint inherits `OrganizationScopedMixin`, which
   resolves the organization from `X-Organization-ID` (only for an ACTIVE
   membership in an ACTIVE organization — a foreign or unknown id is a 404,
   never a 403, so tenant existence isn't disclosed) and scopes every
   queryset through `for_organization()`.
3. **Services**: cross-entity consistency is asserted with
   `ensure_same_organization` (e.g. a message's conversation and phone number
   must share a tenant).

There is no database-level enforcement (Postgres RLS) — isolation is
application-layer only. This was true before Cycle 4 and remains a known,
documented gap (see architecture-audit.md §1).

## What Cycle 4 added, and how each stays isolated

| Resource | Isolation mechanism |
|---|---|
| `Invitation` | `TenantScopedModel`; management endpoints go through `OrganizationScopedMixin` (create/list/revoke are 404 across orgs — `tests/api/test_invitations.py::test_cannot_revoke_across_tenants`). Redemption (`accept`) is intentionally *not* tenant-scoped — the recipient has no membership yet — but is bound to one specific invitation id + a hashed, single-use token; it can never enumerate or touch any other tenant's data. |
| `ApiKey` | `TenantScopedModel`; authenticates as its creator, so **every existing tenant check applies unchanged** — a key cannot reach an organization its creator isn't an active member of (`tests/api/test_api_keys.py::test_key_cannot_reach_a_different_organization`, 404). Suspending the creator's membership immediately stops the key working, with no separate revocation logic needed (`test_key_stops_working_once_its_creator_is_suspended`). |
| Custom `Role` | `TenantScopedModel`; `roles.manage` endpoints scoped via `OrganizationScopedMixin` (`test_cannot_touch_a_role_in_another_organization`, 404). |
| Platform console | Deliberately cross-tenant by design (`apps/platform/`) — this is the one place in the codebase that reads across organizations, gated on `is_staff` instead of a membership, and restricted to aggregate/operational data (counts, status, audit metadata) rather than message content. |

## Verification

`tests/api/test_business_isolation.py` (pre-existing) and
`tests/api/test_api_foundation.py::TestTenantIsolation` (pre-existing) cover
the original boundary. Cycle 4 adds equivalent cross-tenant checks for each
new resource in its own test file (`test_invitations.py`, `test_api_keys.py`,
`test_roles.py`) plus `test_platform_console.py` for the one place that's
intentionally cross-tenant. All pass as of this cycle (381/381,
`docs/productization/architecture-audit.md`'s companion test run).
