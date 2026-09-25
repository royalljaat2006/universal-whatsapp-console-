# Architecture audit — Cycle 4 (SaaS productization)

Baseline inventory taken before any Cycle 4 work, so existing implementations are
extended rather than duplicated. Method: direct code/migration inspection, not
assumption. Cited paths are relative to `backend/` unless stated otherwise.

## 1. Tenancy model

**EXISTS**
- Hierarchy `Organization → Business → WhatsAppBusinessAccount (WABA) → WhatsAppPhoneNumber`
  (`apps/organizations/models.py`, `apps/businesses/models.py`, `apps/whatsapp/models.py`).
- `TenantScopedModel` abstract base (`apps/organizations/tenancy.py`, ADR-001): every
  tenant row carries `organization` FK; `TenantQuerySet.for_organization()` is the
  single scoping primitive.
- `Membership` (user ↔ organization, `role`, `status`: invited/active/suspended,
  `business_scope`: all/selected) and `BusinessAccess` (selected-scope grants) in
  `apps/organizations/models.py` / `apps/businesses/models.py`.
- `Team` / `TeamMembership` (`apps/teams/models.py`) — already used for conversation
  routing/assignment, org-scoped unique slug.
- Enforcement: `OrganizationScopedMixin` + `HasOrganizationPermission`
  (`apps/organizations/api/scoping.py`) resolve org/business from
  `X-Organization-ID` / `X-Business-ID` headers, 404 (not 403) on foreign/unknown
  ids, restrict every queryset via `for_organization()` + `restrict_to_businesses()`.
  All viewsets are expected to inherit this — a single audited chokepoint.

**PARTIAL**: cross-entity consistency (`ensure_same_organization`) is app-layer only,
no Postgres RLS. No plan/tier field on `Organization` yet.

**MISSING**: DB-level tenant isolation (RLS).

## 2. Authentication

**EXISTS**: session-cookie auth (`apps/identity/api.py`: `LoginView`/`LogoutView`/
`MeView`/`CsrfView`), explicit login-CSRF protection (ADR-007). UUID-keyed,
email-normalized `User` (`apps/identity/models.py`). Login throttled
(`ScopedRateThrottle`, `THROTTLE_AUTH=10/min`, `config/settings/base.py`).
Password validators, session cookie flags (`HTTPONLY`/`SAMESITE`/`AGE`), CSRF
cookie flags all set in `config/settings/base.py`. Login success/failure is
audit-logged.

**MISSING**: password reset, email verification, MFA/2FA — no code found anywhere
under `apps/identity/`.

## 3. RBAC / permissions

**EXISTS — already production-grade, extend rather than rebuild.**
- Central catalogue: `PERMISSIONS` + `DEFAULT_ROLES` (owner/admin/manager/agent/
  viewer) in `apps/organizations/permissions.py`.
- `Permission` (global catalogue) and `Role` (per-org, M2M to `Permission`) models
  in `apps/organizations/models.py`; migrations `0002_seed_permission_catalogue`,
  `0004_businesses_manage_permission`, `0005_reports_export_permission` show the
  catalogue is actively maintained via `sync_permission_catalogue`.
- Enforcement: `HasOrganizationPermission` checks `view.required_permissions[action]`
  against the membership's role, fails closed if undeclared.
- 26 named permissions spanning org/members/roles/businesses/teams/numbers/
  templates/contacts/conversations/messages/campaigns/analytics/reports/billing/
  audit/webhooks.

**PARTIAL**: roles are per-organization and editable beyond the 5 seeded defaults —
good foundation for custom roles; CRUD surface for custom roles not independently
verified in this pass.

**MISSING**: a cross-org "platform super admin" concept distinct from Django
`is_staff` (see §10).

## 4. Billing / cost engine

**EXISTS**: `apps/billing/` — explicitly scoped to **Meta provider usage cost**,
not subscription billing (`models.py` docstring, ADR-008, states subscription
billing is deliberately deferred to its own module). `PricingMarket`,
`PricingRule` (effective-dated, org/business/WABA overrides, volume tiers),
`MessageCost` (immutable per-message snapshot). No `api.py`/`urls.py` — no HTTP
surface of its own yet.

**MISSING**: subscription/plan model, entitlement model, payment gateway
integration (`grep -rniI razorpay` across the whole repo: zero hits). This is
greenfield; the pricing-rule/immutable-snapshot pattern here is the template to
reuse for subscription pricing history.

## 5. Audit logging

**EXISTS**: `apps/audit/models.py` — `AuditLog` is append-only (raises on
update/delete), fields: `organization` (nullable for platform events),
`actor_type` (user/system/provider), `actor`, `action`, `resource_type`/
`resource_id`, `metadata` (JSON, secrets scrubbed by `apps/audit/services.py`),
`ip_address`, `user_agent`, `request_id`. Indexed on (org, created_at) and
(org, resource). Exposed via `AuditLogListView` (`apps/audit/api.py`), tenant-scoped,
gated by `audit.view`, filterable by `action` — matches the frontend's
`services/access.ts` → `/api/v1/audit-logs/`. `actions.py` has a constants
catalogue (e.g. `AUTH_LOGIN_FAILED`, `MEMBER_ADDED`).

**PARTIAL**: only endpoints that explicitly call `audit()` are captured; full
mutating-endpoint coverage not exhaustively verified.

**MISSING**: export/retention policy, tamper-evident hash chaining.

## 6. API keys

**MISSING entirely.** No model, no auth mechanism, no scoping anywhere in the
backend. Build from scratch on `TenantScopedModel`.

## 7. Notifications / email

**PARTIAL**: `apps/notifications/models.py` has a `Notification` model
(tenant-scoped, recipient=Membership, kind/title/body/data/read_at) —
explicitly "schema only in this phase"; no `api.py`/`tasks.py`/service populates
or serves it yet.

**MISSING**: email sending entirely — no `EMAIL_BACKEND`, no `send_mail` call,
no configured provider anywhere in `apps/` or `config/`. Blocks: invitation
emails, password reset, billing receipts, alert delivery.

## 8. Webhook reliability

**EXISTS — mature, production-grade.** `apps/webhooks/models.py`: `WebhookEvent`
status machine (pending/processing/processed/partial/failed/dead),
`body_sha256` unique constraint (redelivery idempotency), `attempts`,
`next_attempt_at` backoff, partial index for the due-sweeper query.
`WebhookProcessingAttempt` gives a full per-attempt trail. `tasks.py`:
`process_webhook_event` (`acks_late=True`) + `requeue_stale_webhook_events`
sweeper (outbox pattern), dead-letter via `status=dead`. Reusable pattern for
other async reliability needs (e.g. a future payment-gateway webhook).

## 9. Celery tasks

**EXISTS**: task files across `analytics`, `billing`, `campaigns`, `messaging`,
`reports`, `templates`, `webhooks`, `whatsapp`. Retry patterns already in use:
`acks_late=True` (campaigns, messaging, reports, webhooks), `bind=True` +
`max_retries=3–5` (campaigns, templates, whatsapp), `max_retries=None` with
custom backoff (messaging). No `autoretry_for`/`retry_backoff` kwargs — retry
logic is hand-rolled per task.

## 10. Docker / nginx / monitoring

**EXISTS**: `infrastructure/docker/{backend,frontend}` Dockerfiles,
`infrastructure/nginx/nginx.conf.template` (subpath-deployable via `BASE_PATH`).
Documented topology in `docs/deployment.md`: nginx → backend (uvicorn ASGI) +
per-queue Celery workers (webhooks isolated; outbound/campaigns isolated;
sync/analytics/default) + beat, Postgres + Redis. Health probes: `/health/live`,
`/health/ready` (DB, pending migrations, Redis), `/health/dependencies`
(staff-only; Celery + Meta config presence). `infrastructure/monitoring/README.md`
documents phase-1 monitoring: structured JSON logs correlated end-to-end
(nginx → Django → Celery) via `request_id`/`organization_id`/`user_id`, with an
explicit table of signals to alert on today (webhook failures, dead events,
send failures by error code, provider auth failures) — currently log/SQL-based,
no metrics pipeline.

**DEFERRED BY DESIGN (not overlooked)**: Prometheus/Grafana/Sentry are named in
the README as a proposed next step pending an ADR. No backup/DR automation or
runbook exists yet — `docs/deployment.md`'s go-live checklist only requires a
manual "backups verified by a restore test."

## 11. Tests

**EXISTS**: `backend/tests/` (not per-app), 19 files: `unit/` (5 — state machine,
Meta webhook adapter, Meta provider, cycle2 units, core), `integration/`
(10 — webhook pipeline, realtime, outbound messaging, domain services, and
cycle1/2/3 flows: campaigns, analytics, exports, events+billing, messaging),
`api/` (4 — management API, cycle2 management, **business isolation**, api
foundation). `test_business_isolation.py` confirms tenant-isolation testing
already exists.

**PARTIAL**: no dedicated per-app suites for billing/audit/RBAC — covered
indirectly via cycle2/cycle3 integration tests. No tests reference API keys,
subscriptions, a payment gateway, or MFA (expected — none exist yet).

## Bottom line for Cycle 4 planning

**Extend, do not rebuild**: tenancy scoping, the RBAC/permission catalogue,
webhook reliability, Celery retry patterns.

**Build from scratch**: API keys, subscription plans + entitlements, payment
gateway integration and invoicing, email/notification delivery, password reset,
MFA, a platform super-admin surface distinct from `is_staff`, and a metrics/
alerting pipeline (already scoped by the team as a deferred next step).

**One correction needed to an existing flow**: team invitations have an
`INVITED` status enum value, but `apps/organizations/services.py`'s
`add_member()` currently creates memberships as immediately `ACTIVE` — there is
no token-based invite/accept flow and no email is sent. This needs building,
and it depends on the email-delivery decision below.

## Decisions this audit surfaces (not engineering-only)

These block real estate in §4 (billing) and part of §2/§7 (invites, password
reset) and are business/credential decisions, not code:

1. **Payment gateway**: Razorpay is the brief's suggestion for an India-focused
   SaaS — needs a real merchant account, API keys, and GST/billing-entity
   details before checkout can be wired up for real.
2. **Plan names/pricing**: the brief explicitly says not to invent final
   commercial prices — needs the actual tier names, limits, and price points.
3. **Email provider**: nothing is configured today (no SMTP/SES/SendGrid
   credentials) — needed for invitations, password reset, and billing receipts.
   Until configured, the code will fail closed and log rather than pretend to
   send.
