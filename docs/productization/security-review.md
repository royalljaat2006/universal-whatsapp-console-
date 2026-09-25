# Security review — Cycle 4 additions

The pre-existing security posture (secrets, transport, session auth, CSRF,
credential encryption) is documented in `docs/security.md` and was not
changed by this cycle. This document covers only the new surfaces Cycle 4
introduced: registration, invitations, API keys, custom roles, and the
platform console.

## Registration and invitation-acceptance (both can call `login()`)

Both can result in a brand-new session being created for an anonymous
caller — the exact scenario `docs/security.md`'s login-CSRF protection exists
for (an attacker's page could otherwise auto-submit a form that signs the
victim into an account the attacker controls). Both explicitly call the same
`enforce_csrf()` helper `LoginView` uses, verified in
`test_registration.py::test_requires_csrf` and
`test_invitations.py::TestAccept::test_accept_requires_csrf`.

## Secrets: invitation tokens and API keys follow the same discipline

Neither a raw invitation token nor a raw API key is ever persisted — only a
SHA-256 hash (`Invitation.token_hash`, `ApiKey.key_hash`), compared with
`hmac.compare_digest` (constant-time, avoiding a timing side-channel). Both
are shown to the caller exactly once, in the creation response, matching the
existing convention for `ProviderCredential` (write-only, never re-readable).
Verified: `test_api_keys.py::test_owner_creates_key_shown_once_and_list_never_exposes_it`
asserts the list endpoint exposes neither the key nor its hash.

## IDOR / cross-tenant access

Every new resource is covered by an explicit cross-tenant test asserting 404
(not a data leak, not a 403 that would confirm existence) — see
`tenant-isolation.md` for the full table. The one exception is the invitation
*preview* endpoint (`GET /api/v1/invitations/<id>/`), which is deliberately
public and requires no token: it discloses the organization name, role name
and invited email for a *specific, already-known* invitation id (a UUIDv7,
not guessable) — the same amount of information the id itself already
implies someone is asking about. It cannot be used to enumerate invitations
or reach any other tenant's data, and never reveals whether an *unknown* id
exists versus not (both are a plain 404).

## API key scope escalation

An API key's `scopes` are checked in addition to (never instead of) its
creator's role permission — verified with a positive test that a key scoped
to `contacts.view` is rejected for `audit.view` even though its creator (an
organization owner) has every permission
(`test_key_denies_out_of_scope_action_even_though_owner_role_allows_it`).
Unknown scope codes are rejected at creation time
(`InvalidScopeError`/400), so a key can never be created with a scope that
doesn't correspond to a real permission.

## Platform console: blast radius if `is_staff` is compromised

The platform console (`apps/platform/`) is the one deliberately cross-tenant
surface in the codebase. It was scoped narrowly on purpose:

- No message, conversation or contact content is ever returned — only
  counts, status, and audit *metadata* (action, actor, resource type/id,
  timestamps — never message bodies, since `AuditLog.metadata` itself is
  scrubbed of secrets at write time, per `apps/audit/services.py`).
- The only state-changing action is organization suspend/reactivate, which
  is itself audit-logged (`organization.suspended` / `organization.reactivated`,
  tested in `test_suspend_is_audit_logged`) — a compromised staff account's
  actions here are traceable, not silent.
- Suspension takes effect immediately for every member with no separate
  enforcement path to keep in sync: `get_active_membership` already filters
  on `organization__status=Organization.Status.ACTIVE` (pre-existing code,
  unchanged), so flipping one field locks out the whole tenant instantly
  (`test_suspend_locks_out_members_immediately`).

`is_staff` is Django's own flag, already gating the admin site and
`/health/dependencies` before this cycle — granting it remains an infra-level
decision (Django admin / `createsuperuser` / direct DB), not something this
cycle added a new grant path for.

## Not covered by this review (pre-existing, out of Cycle 4's scope)

SQL injection, XSS, SSRF, file-upload validation, webhook signature
verification, rate limiting, CORS, and security headers are unchanged from
before this cycle and remain as documented in `docs/security.md`. A
dependency vulnerability scan (`pip-audit` / `npm audit`) was not run as part
of this cycle — recommended as a routine CI addition, not attempted here.
