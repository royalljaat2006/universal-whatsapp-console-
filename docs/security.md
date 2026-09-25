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
7. Planned defence in depth: PostgreSQL row-level security (ADR-001).

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
