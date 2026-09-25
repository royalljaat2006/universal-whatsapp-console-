# RBAC matrix

Permissions are checked by code (`apps.organizations.permissions.PERMISSIONS`),
never by role name — a view declares `required_permissions` and
`HasOrganizationPermission` checks the caller's role against it
(`apps/organizations/api/scoping.py`). Roles are per-organization, seeded from
`DEFAULT_ROLES` on creation, and fully editable afterwards — including by the
organization itself, via `apps/organizations/api/roles.py` (Cycle 4).

## Seeded default roles

| Permission | Owner | Admin | Manager | Agent | Viewer |
|---|:-:|:-:|:-:|:-:|:-:|
| organization.view | ✅ | ✅ | ✅ | ✅ | ✅ |
| organization.manage | ✅ | ✅ | | | |
| members.view | ✅ | ✅ | ✅ | | |
| members.manage | ✅ | ✅ | | | |
| roles.manage | ✅ | ✅ | | | |
| businesses.manage | ✅ | ✅ | | | |
| teams.view | ✅ | ✅ | ✅ | ✅ | |
| teams.manage | ✅ | ✅ | ✅ | | |
| numbers.view | ✅ | ✅ | ✅ | ✅ | ✅ |
| numbers.manage | ✅ | ✅ | | | |
| integrations.manage | ✅ | ✅ | | | |
| templates.view | ✅ | ✅ | ✅ | ✅ | ✅ |
| templates.manage | ✅ | ✅ | ✅ | | |
| contacts.view | ✅ | ✅ | ✅ | ✅ | ✅ |
| contacts.manage | ✅ | ✅ | ✅ | ✅ | |
| conversations.view | ✅ | ✅ | ✅ | ✅ | ✅ |
| conversations.assign | ✅ | ✅ | ✅ | | |
| messages.send | ✅ | ✅ | ✅ | ✅ | |
| campaigns.view | ✅ | ✅ | ✅ | | ✅ |
| campaigns.manage | ✅ | ✅ | ✅ | | |
| campaigns.launch | ✅ | ✅ | ✅ | | |
| analytics.view | ✅ | ✅ | ✅ | | ✅ |
| reports.export | ✅ | ✅ | ✅ | | |
| billing.view | ✅ | ✅ | ✅ | | |
| billing.manage | ✅ | | | | |
| audit.view | ✅ | ✅ | | | |
| webhooks.view | ✅ | ✅ | | | |
| webhooks.replay | ✅ | ✅ | | | |
| api_keys.manage | ✅ | ✅ | | | |

Owner = every permission (`set(PERMISSIONS)`); Admin = every permission except
`billing.manage` — both computed, not hand-listed, so a new permission is
automatically granted to Owner/Admin the moment it's added to `PERMISSIONS`
(see the migration pattern in `apps/organizations/migrations/0007_api_keys_permission.py`).

## Custom roles (Cycle 4)

An organization admin can create roles beyond the five above with any subset
of the permission catalogue:

```
POST /api/v1/organizations/current/roles/
{"name": "Billing Clerk", "permissions": ["billing.view", "analytics.view"]}
```

A custom role's key collision, unknown permission codes, deleting a role
still assigned to a member, and deleting a seeded (`is_system`) role are all
rejected server-side (`apps/organizations/api/roles.py`, tested in
`tests/api/test_roles.py`).

## Platform-level access

A cross-tenant "super admin" surface exists (`apps/platform/`) gated on
`request.user.is_staff` — the same flag Django's admin site and
`/health/dependencies` already use, rather than a second parallel concept.
It is deliberately **not** part of the organization/role system above: it has
no tenant context and cannot see message content, only aggregate operational
facts (org directory, suspend/reactivate, cross-org audit log, infra health).
See `apps/platform/api.py`.

## API key scopes

An API key (`apps/apikeys/`) authenticates as its creator — their role still
applies — and additionally carries its own `scopes`, a subset of
`PERMISSIONS` checked in `HasOrganizationPermission` alongside the role. A
key can only ever narrow what its creator could already do, never grant more
(enforced positively in `tests/api/test_api_keys.py::TestAuthentication::
test_key_denies_out_of_scope_action_even_though_owner_role_allows_it`).
