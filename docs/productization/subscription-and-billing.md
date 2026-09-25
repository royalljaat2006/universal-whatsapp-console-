# Subscription and billing — deferred

**Status: not implemented in this cycle.** Three decisions block this area,
each requiring a business owner, not an engineering default (see
`architecture-audit.md`'s closing section):

1. **Payment gateway.** The brief suggests Razorpay for an India-focused
   product; no merchant account or API credentials exist yet.
2. **Plan names, limits and prices.** The brief explicitly instructs not to
   invent final commercial prices.
3. **Email delivery** (for receipts/invoices) — see `customer-onboarding.md`.

## What already exists and should be reused, not rebuilt

`apps/billing/` tracks **Meta provider messaging cost** — `PricingRule`
(effective-dated, org/business/WABA overrides, volume tiers) and
`MessageCost` (immutable per-message snapshot). Its own docstring already
states subscription billing is a deliberately separate concern
(`apps/billing/models.py`, ADR-008). This is the right pattern to copy for
subscription pricing history: effective-dated rules, immutable charge
snapshots, never mutating a historical price.

## What building this later will need (once the decisions above are made)

- A `Plan` model (name, limits, price, effective dates) — configurable, not
  hardcoded, per the brief.
- A `Subscription` model per organization (plan, status, trial/renewal
  dates, cancellation) — a new module, following `apps/billing/`'s pattern
  of keeping subscription billing separate from Meta usage cost.
- A payment-provider abstraction (checkout, webhook verification,
  idempotent webhook handling — `apps/webhooks/` already has a mature,
  reusable outbox/idempotency/dead-letter pattern to copy for a payment
  gateway's webhooks specifically).
- Entitlement enforcement reusing the same `HasOrganizationPermission`
  chokepoint style already used for RBAC (a plan-limit check is
  structurally the same shape as a permission check).

None of this was started in this cycle beyond identifying the pattern to
follow — building the models and provider integration before the plan
structure and gateway are chosen would mean re-doing it once real
requirements land.
