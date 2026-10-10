# Security

## Credentials and secrets

| Secret | Where it lives | Never |
|---|---|---|
| `DJANGO_SECRET_KEY`, DB/Redis passwords | deployment env / secret store | in git, in the frontend |
| `META_APP_SECRET` | backend env (webhook HMAC only) | logged, returned by any API |
| `META_WEBHOOK_VERIFY_TOKEN` | backend env | logged |
| Meta System User tokens | `PROVIDER_SECRET_*` env vars (populated from the secret store) **or** Fernet ciphertext in `ProviderCredential.encrypted_secret` | plain DB columns, serializers, logs, audit metadata |
| `FIELD_ENCRYPTION_KEYS` | backend env | in the DB next to the ciphertext |

- `ProviderCredential` holds a **reference** (`env:PROVIDER_SECRET_X`). A new
  backend (Vault, AWS/GCP/Azure secret managers) is one resolver in
  `core/secrets.py`. No domain changes are needed.
- References are allow-listed to the `PROVIDER_SECRET_` prefix, so a tenant
  admin cannot point a credential at `DATABASE_URL` (tested).
- Secrets are resolved at the point of use (`sender_ref()`), never cached,
  never serialised. The credentials API is **write-only**: it returns an id,
  label, backend and last-4 fingerprint, never the token or the reference.
  Tokens submitted through the API are Fernet-encrypted before storage.
- **Rotation:** Meta tokens: update the env/secret-store value and restart the
  workers (the credential row does not change). Encryption keys: prepend a new
  Fernet key, re-encrypt with `core.crypto.rotate`, then drop the old key
  (tested).
- The frontend receives **no** secrets. Only `BACKEND_INTERNAL_URL` exists
  there, and it is server-side only. There are no `NEXT_PUBLIC_*` secrets.
- `.env` is gitignored. `.env.example` contains placeholders only.

## Transport

TLS terminates at nginx or the load balancer in staging/production. Django
production settings enforce HSTS, secure cookies, `SECURE_PROXY_SSL_HEADER`,
the SSL redirect (except `/health/`), `nosniff`, `DENY` framing, and a
same-origin referrer policy.

## Authentication

- Django sessions: an HttpOnly, `SameSite=Lax` cookie, 12h lifetime, same
  origin through nginx. No tokens in browser storage (ADR-007).
- CSRF is enforced on every unsafe request **including login** (DRF skips it
  for anonymous users; we enforce it explicitly).
- Login is throttled (10/min) and both success and failure are audited.
  Passwords must be at least 12 characters and pass Django's validators.

### Two-step verification (TOTP)

Opt-in per user (Settings > Your account > Two-step verification). Standard
RFC 6238 authenticator-app codes (HMAC-SHA1, 6 digits, 30 s), implemented on
the standard library in `apps/identity/mfa.py`; the implementation is checked
against the RFC's published test vectors.

- **Secrets** are encrypted at rest with the same Fernet keys as provider
  tokens (`FIELD_ENCRYPTION_KEYS`) and shown to the user exactly once, at
  enrolment. Nothing is enforced until the user confirms with a valid code.
- **Sign-in is two steps.** A correct password for a user with MFA does *not*
  log them in; it parks a short-lived (5 min) pending marker in the anonymous
  session and returns `{"mfa_required": true}`. `POST /auth/mfa/verify/`
  promotes it to a real session, which then carries a stamp of the user's
  *current* enrolment.
- **No bypass paths.** `MFASessionAuthentication` (REST), the admin guard
  middleware (`/admin/`) and the WebSocket consumer all require that stamp
  for users who have MFA. A session created any other way (for example the
  Django admin login) is refused with `MFA_REQUIRED`. Because the stamp is
  tied to the enrolment, enabling MFA - or removing and re-adding a device -
  signs out every other existing session of that user.
- **Replay protection.** A code is accepted only for a time-step later than
  the last accepted one, advanced with a conditional `UPDATE`, so a code
  observed on the wire (or two concurrent requests with the same code)
  cannot be used twice.
- **Brute force.** Three independent limits: the `auth` throttle (10/min); the
  pending sign-in dies after 5 wrong codes; and a per-*account* counter
  (10 failures / 15 min) that survives restarting the login or changing IP.
  While locked, even a correct code is refused. Failures and lockouts are
  audited.
- **Recovery codes.** 10 single-use codes (~49 bits each), stored only as
  keyed HMAC-SHA256 hashes, consumed with an atomic update. A new set
  replaces all old ones and needs password plus a current authenticator code.
- **Re-authentication.** Starting enrolment needs the current password
  (a hijacked session cannot enrol an attacker's authenticator). Turning MFA
  off needs password *and* a valid code or recovery code. A wrong password
  returns 403 `REAUTHENTICATION_FAILED`, deliberately not 401, so the client
  does not treat it as an expired session.
- **Rollout (no one is locked out).** MFA is off for everyone until they opt in.
  `MFA_REQUIRED_FOR_STAFF=true` additionally requires platform operators
  (`is_staff`) to have enrolled before they can use `/api/v1/platform/*`
  (403 `MFA_SETUP_REQUIRED`). Suggested order: ship with the flag off, have
  each operator enrol, verify, then switch the flag on. Organization-level
  enforcement for customers is not implemented; it would need an
  organization setting and a grace period, and is a deliberate follow-up.
- **API keys are non-interactive.** A key authenticates as its creator and is not
  subject to the second factor (enabling MFA does not break integrations), but
  `InteractiveSessionOnly` refuses keys on the account-security endpoints (MFA
  management, account deletion): those confirm the password, and a leaked key
  must not become a password oracle or a way to change who can sign in.
- **Invitations** can only create a brand-new account (an existing email is a
  409), so an invitation token can never sign in an existing MFA user.
- **Known limits.** Recovery by email is not offered (it would reintroduce a
  password-only path). A user who loses both device and recovery codes needs an
  operator to run `python manage.py reset_mfa --email <address>` (audited,
  asks for confirmation; verify the person's identity first). There is no QR code in
  the enrolment screen (no extra frontend dependency); the key and an
  `otpauth://` link are shown instead.

## Authorization (RBAC)

- Code checks **permissions** (`messages.send`), never role names. The
  catalogue is `apps/organizations/permissions.py` (26 permissions).
- Default roles are seeded per organization (owner, admin, manager, agent,
  viewer) and are editable data.
- Every tenant endpoint declares `required_permissions` per method.
  `HasOrganizationPermission` **fails closed** when a view forgets to
  declare one.
- Business access: owners and admins see all businesses. Every other role
  sees only the businesses explicitly granted (least privilege by default).
- The UI hides what you cannot do, but the API is the enforcement point.

## Tenant isolation

1. The tenant comes from `X-Organization-ID` and is honoured only with an
   ACTIVE membership in an ACTIVE org. Foreign or unknown orgs return **404**
   (no existence oracle).
2. Querysets are built via `Model.objects.for_organization(org)` in
   `OrganizationScopedMixin.get_queryset`. Detail lookups inherit the scope,
   so a cross-tenant id returns 404 (tested for contacts and numbers).
3. Services assert `ensure_same_organization(...)` when combining entities.
4. Webhook traffic derives its tenant from provider ids. A WABA or number is
   globally unique, so it maps to exactly one tenant, and claiming another
   tenant's WABA fails without disclosing it.
5. **Business isolation (ADR-011):** `X-Business-ID` is validated against
   the membership's business access, and every business-owned queryset is
   restricted to accessible businesses even without the header. Sends
   re-validate the business of the number or conversation. Tests:
   `tests/api/test_business_isolation.py`.
6. WebSocket groups are per business, joined only after membership,
   permission and business-access checks.
7. **Row-level security (defence in depth, always on):** see
   [Row-level security](#row-level-security) below.

### Row-level security

`core/migrations/0001_row_level_security.py` (plus
`0002_notification_preference_rls.py`, added when `NotificationPreference`
was introduced) enables Postgres RLS as a second, database-enforced layer
under the application-layer scoping above — it does not replace it. A
restricted role, `whatsapp_platform_app`, is created `NOLOGIN NOBYPASSRLS`
without table ownership (Postgres RLS is always bypassed by a table's owner
and by superusers, so the owning migration role could never be restricted
this way). Every `TenantScopedModel` table (32 tables) gets a policy
requiring `organization_id` to match the Postgres session variable
`app.current_org_id`; an unset value is `NULL`, which matches no row (fails
closed). `organizations_role`, `organizations_membership` and
`organizations_invitation` are deliberately excluded from the policy —
resolving *which* organization a request belongs to means querying
Membership before any session variable can be set, so a strict policy there
would break authentication itself; these three keep only the existing
app-layer checks. Every table (including non-tenant ones like
`identity_user`, Django's own tables, and future tables via `ALTER DEFAULT
PRIVILEGES`) is granted ordinary CRUD so the app role can function —
isolation comes from the policies, not from withholding grants.

Nobody ever connects to Postgres *as* `whatsapp_platform_app` — it stays
`NOLOGIN` permanently. The app's one connection role is instead granted
**membership** in it (`GRANT whatsapp_platform_app TO <connecting role>`),
and `core.middleware.set_rls_organization` issues `SET LOCAL ROLE
whatsapp_platform_app` together with `SET LOCAL` for `app.current_org_id`,
both scoped to the current transaction only — reverting automatically to
the owning role when the transaction ends, so neither can leak to the next
request on a pooled connection (`CONN_MAX_AGE=60`). `set_rls_organization`
is called from `OrganizationScopedMixin.initial` once a membership's
organization is resolved, so enforcement is **always on** for every
authenticated tenant API request — there is no separate connection string
or environment flag to remember to set. `RowLevelSecurityMiddleware` wraps
requests under `/api/v1/` (where `OrganizationScopedMixin` lives) in
`transaction.atomic()` so there is a transaction for `SET LOCAL` to be
scoped to (Django does not enable `ATOMIC_REQUESTS`); it deliberately
leaves webhook, health, admin and static/media paths unwrapped, since those
never call `set_rls_organization` and some (webhook ingestion) need
unscoped, cross-tenant lookups by provider id that the restricted role
could never satisfy. Migrations, webhook processing, Django admin and
Celery workers/beat all connect as the plain owning role and are completely
unaffected — RLS only ever engages for the one code path that explicitly
opts in.

Enforced by `tests/integration/test_row_level_security.py`, which connects
as the restricted role exactly as a request does and asserts: every
`TenantScopedModel` table has RLS enabled and a policy (so a new tenant model
without one fails CI); each tenant sees only its own rows; no tenant context
returns zero rows (fail closed); cross-tenant `UPDATE`/`DELETE` affect nothing;
inserting into, or moving a row to, another tenant is rejected; and the role is
neither superuser nor `BYPASSRLS`. The notes below describe the earlier manual
verification.

Verified against a real database, twice — with a temporary
`APP_DATABASE_URL`-based design initially (superseded, see git history) and
again with the final `SET LOCAL ROLE` design: with the restricted role
active and no session variable set, tenant tables return zero rows; with
the correct `organization_id`, only that tenant's rows; with a different
`organization_id`, zero rows; and that the role reverts to the owner at
`COMMIT`. Also verified via the E2E suite (`auth.spec.ts`,
`onboarding.spec.ts`, `tenant-isolation.spec.ts`), which exercises
registration, login, business creation and cross-tenant isolation through
the real API with RLS engaged.

## Operational safeguards

Per-organization caps against runaway or hostile volume (`core/limits.py`),
independent of any subscription plan; generous defaults, `0` disables each:
`MAX_ACTIVE_API_KEYS_PER_ORG` (50), `MAX_PENDING_INVITATIONS_PER_ORG` (100,
each invitation is an outbound email), `MAX_ACTIVE_EXPORTS_PER_ORG` (5 queued or
running). Hitting one returns 409 `OPERATIONAL_LIMIT_REACHED` with the limit.
Existing guards: `CAMPAIGN_MAX_RECIPIENTS`, `CAMPAIGN_MAX_RATE_PER_MINUTE`, CSV
import 5,000 rows, webhook body size, nginx body limits, request throttles. The
check-then-create is not serialised, so a burst can overshoot a cap slightly.

The OpenAPI schema (`/api/v1/schema/`) is readable only by signed-in users
outside `DEBUG`.

## Business access management (Cycle 2)

- Grants and revocations go through `apps/businesses/services.py`. A member
  **cannot change their own access** and nobody can change an owner's access
  through the API (`403 ACCESS_CHANGE_FORBIDDEN`). Both are audited
  (`business.access_granted`, `business.access_revoked`,
  `business.access_scope_changed`).
- An assignee must be an active member with access to the conversation's
  business. A tag must belong to the same business as the contact or
  conversation it is attached to. Media assets can only be sent or read
  inside their own business. Every mismatch returns 404 or 422, never data.

## Media safety (Cycle 2)

- Uploads are validated server-side by type, extension, size and file
  signature before storage (`apps/media/rules.py`).
- Media is served only after the org and business checks, with
  `Content-Security-Policy: sandbox; default-src 'none'`, `nosniff`, and
  `attachment` disposition for non-media types.
- Inbound downloads only follow `https` URLs on allow-listed hosts
  (`META_MEDIA_DOWNLOAD_HOSTS`), with a streamed size cap, so a crafted
  webhook cannot make the worker fetch internal URLs (SSRF).

## Campaigns, analytics and exports (Cycle 3)

- Every analytics query is filtered by the caller's accessible businesses
  (server-side). Number, WABA, template and campaign filters are verified to
  belong to them (404 otherwise). A Kiosk user cannot read EPS analytics.
- A campaign's business is the validated `X-Business-ID`. `business_id` is not
  an editable field, and the number, template, audience tags/templates and
  header media must belong to that business. The audience query is always
  constrained to the campaign's business, so a Kiosk campaign cannot target EPS
  contacts.
- Campaign mutations need `campaigns.manage` (drafts) or `campaigns.launch`
  (launch, pause, resume, cancel, retry). Exports need `reports.export` plus
  the view permission of the data exported.
- Exported CSVs are neutralised against formula injection and served only
  after the organization and business checks (`no-store`).
- Audit actions: `campaign.created/updated/audience_changed/template_changed/
  scheduled/launched/paused/resumed/cancelled/retry_requested/deleted`,
  `audience.created/updated/deleted`,
  `report.export_requested/export_downloaded`. No secrets are logged.

## Audit logging

`apps/audit` records: login succeeded/failed, logout, org created, member
added, credential created, WABA connected, numbers synced, manual message
send, webhook replay. Fields: actor, org, action, resource type/id,
metadata (secret-scrubbed), IP (`X-Real-IP` from nginx), user agent,
request_id. Rows are append-only (update, delete and queryset.update raise).

## Logging hygiene

`core/logging.py` redacts keys matching `token|secret|password|authorization|
signature|cookie|credential|api_key` and scrubs `Bearer …` / `EAA…` token
patterns from free text. Provider HTTP logs contain method, path, status and
latency only. They never contain bodies or headers.

## Webhook security

HMAC-SHA256 over the raw body with the App Secret, constant-time comparison,
unsigned bodies never stored, body size cap, nginx rate limit. See
webhook-architecture.md.

<a id="data-retention"></a>
## Data retention

Nothing is deleted automatically: `RETENTION_ENABLED=false` by default.

| Class | Default policy | Knob |
|---|---|---|
| Operational (messages, conversations, contacts) | keep; deletion is a tenant/legal decision | — |
| Raw webhook events | 90 days once processed (replay window) | `RETENTION_WEBHOOK_EVENTS_DAYS` |
| Audit logs | forever (0) | `RETENTION_AUDIT_LOGS_DAYS` |
| Analytics rollups | keep (small) | — |
| Attachments (object storage) | bucket lifecycle rules, per tenant contract | storage config |

The purge job is part of a later phase and is gated on `RETENTION_ENABLED`.
Contact erasure requests (opt-out or GDPR-style) need a dedicated,
audited service. That is also a later phase.
