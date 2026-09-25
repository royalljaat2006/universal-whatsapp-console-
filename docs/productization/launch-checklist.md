# Cycle 4 launch-readiness checklist

Classification key: **IMPLEMENTED** (code exists), **TESTED** (automated test
coverage exists and passes), **LIVE VERIFIED** (exercised against a real
external provider/credential), **BLOCKED** (needs a decision or credential
not available in this cycle), **N/A**.

| Requirement (brief §1–30) | Status | Notes |
|---|---|---|
| Multi-tenant org → business → WABA → number hierarchy | IMPLEMENTED, TESTED | Pre-existing (Cycle 1–3), unchanged |
| Tenant isolation across API/jobs/WebSockets/exports/media | IMPLEMENTED, TESTED | Pre-existing boundary; extended to 3 new resources this cycle — see `tenant-isolation.md` |
| Customer registration | IMPLEMENTED, TESTED | `POST /api/v1/auth/register/`; no frontend page yet |
| Organization creation | IMPLEMENTED, TESTED | Via registration or pre-existing `create_organization` |
| Team invitations | IMPLEMENTED, TESTED | Fixes a real pre-existing gap (see architecture-audit.md); no email sent (deferred) |
| RBAC (named roles + permission catalogue) | IMPLEMENTED, TESTED | Catalogue and enforcement pre-existing; custom-role CRUD added this cycle |
| Custom roles | IMPLEMENTED, TESTED | New this cycle |
| Connect WABA / sync numbers / sync templates | IMPLEMENTED, TESTED | Pre-existing (Cycle 1–3), unchanged |
| Meta Embedded Signup investigation | NOT DONE | Not started this cycle |
| Send messages / campaigns | IMPLEMENTED, TESTED | Pre-existing, unchanged |
| Usage/cost/analytics tracking (Meta charges) | IMPLEMENTED, TESTED | Pre-existing `apps.billing` (Meta cost only) |
| SaaS subscription plans | BLOCKED | Pricing/tier decision deferred by request — see `subscription-and-billing.md` |
| Plan enforcement / entitlements | BLOCKED | Depends on the above |
| Payment integration (Razorpay or other) | BLOCKED | Gateway decision deferred by request |
| Invoicing | BLOCKED | Depends on the above |
| SaaS usage metering | BLOCKED (partially N/A) | Meta usage already metered; subscription usage has nothing to meter against yet — see `usage-metering.md` |
| Super admin console | IMPLEMENTED, TESTED | Non-billing panels: org directory, suspend/reactivate, cross-org audit, operations summary. Billing/plan panels blocked with the above |
| API keys | IMPLEMENTED, TESTED | Full lifecycle, scoped, tenant-isolated |
| Audit logging | IMPLEMENTED, TESTED | Pre-existing model/API; new actions added for every Cycle 4 mutation |
| Security hardening review | IMPLEMENTED (partial) | New-surface review done (`security-review.md`); dependency scan not run |
| Data retention / privacy exports | NOT DONE | Not started this cycle |
| Background job reliability | IMPLEMENTED, TESTED | Pre-existing (webhook outbox/retry/dead-letter), unchanged |
| Monitoring and alerting | IMPLEMENTED (partial) | New read-only operations endpoint; no metrics pipeline or real alert delivery (no credentials) — see `monitoring.md` |
| Backup and disaster recovery | BLOCKED / NOT DONE | No automation exists; documented as a gap, not closed — see `backup-and-recovery.md` |
| Performance/scalability review | NOT DONE | Not attempted this cycle |
| Frontend polish for new flows | NOT DONE | Backend-only cycle; no register/invite/API-key/admin-console UI yet |
| Support/customer-ops tooling | NOT DONE | Not started this cycle |
| Documentation | IMPLEMENTED | This directory |
| Database migrations preserve existing data | TESTED | All migrations additive; `makemigrations --check` clean; full existing suite (325 tests) still passes unchanged |
| All tests and checks pass | TESTED | 381/381 backend tests, ruff, black, mypy all clean |
| Live integration validation (Meta, payment) | NOT DONE | No new Meta calls introduced this cycle; payment blocked entirely |
| Production deployment of this cycle's code | NOT DONE | Committed and pushed to `main` (commit `fffaafd`); **not yet deployed to the live R730 instance** — deploying requires the same approval-gated server process used for the initial deployment |

## Explicitly not claimed

This cycle does **not** make the product ready to sell. Billing, payment,
and plan enforcement — the mechanisms that would actually let a customer pay
— are entirely blocked pending your decisions. What this cycle does
establish: the multi-tenant foundation a SaaS product needs (self-serve
signup, team invitation, custom roles, programmatic API access, and platform
operations) is now real, tested, and isolated correctly — the parts that
were missing are the commercial ones, which are business decisions, not
engineering gaps.

## Immediate next steps, in order

1. Decide payment gateway, plan tiers, and email provider (unblocks §10–13
   and the email-dependent parts of §5/§8).
2. Deploy this cycle's backend changes to the R730 (new migrations, no
   downtime expected — see `production-runbook.md`).
3. Build the frontend pages for register/invite/accept/API-keys/admin
   console — currently backend-API-only.
4. Only after 1: implement subscription/billing per
   `subscription-and-billing.md`'s reuse plan.
