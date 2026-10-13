# Billing architecture

Two separate financial domains (ADR-008):

| Domain | What | Status |
|---|---|---|
| **Provider usage cost** | what Meta charges us per delivered message | models + pricing engine built |
| **SaaS subscription billing** | what tenants pay us (plans, invoices) | not started; its own module later |

## Provider usage cost

```
status webhook (pricing{billable, category, type, pricing_model})
   → Message.billable / pricing_category / pricing_type
   → on commit: billing.calculate_message_cost            (Cycle 2)
        billable=false            → cost_status=free (no MessageCost)
        billable + delivered      → market_for_phone() → find_rule()
            no rule               → cost_status=pending_rate (re-priced hourly)
            rule                  → MessageCost (immutable snapshot) + cost_status=priced
   → beat: billing-price-pending (hourly :15) retries pending_rate once rate cards exist
   → cost rollups in MessageMetricDaily                   (analytics phase)
```

`Message.cost_status` ∈ `none | free | pending_rate | priced` is shown on
every message (API `cost_status` + `cost`). No amount is ever shown until a
loaded rate card produced it.

- **Billable-ness comes from Meta** (`pricing.billable`), not from our
  inference. Meta owns the free customer-service-window and free-entry-point
  rules, and those change (see the pricing conflict in
  integrations/meta/api-assumptions.md).
- **PricingRule**: `provider, market (ISO-2 or "*"), category, currency, rate,
  billing_unit, effective_from, effective_to, metadata (tiers, source)` and an
  optional `organization` for negotiated overrides. Lookup order: tenant
  override → platform rule; exact market → `*`; latest `effective_from`
  (`apps/billing/pricing.py`, tested).
- **MessageCost** is created once per message and never updated (`save()`
  raises on update). A rate change creates a new rule with a new
  `effective_from`, and history keeps its original rate snapshot.
- Nothing about Meta's prices is hard-coded. **No rate card ships with the
  code**: until an operator loads one from Meta's official rate-card files,
  billable messages stay `pending_rate`.
- **Markets** (`PricingMarket`): `code, name, calling_codes[]` (GIN index),
  effective-dated. `market_for_phone()` picks the longest calling-code prefix
  valid at the message time, so the 2026-10-01 split of nine markets out of
  "Rest of …" regions is a data load, not a code change.
- **Rule specificity** (`find_rule`): WABA → business → organization →
  platform; exact market before `*`; volume tier (`volume_tier_min/max`,
  counted as delivered billable messages of that category in the calendar
  month for the business portfolio); latest `effective_from`.
- `MessageCost.pricing_snapshot` records the rule id, card version and source,
  market, tier and volume used, so a historical cost can always be explained.

## Pricing adjustment (`apps/billing/markup.py`)

The rate card holds **the provider's published rates, unmodified**. An optional,
operator-configured percentage can be added when a message is priced; it is never
written into the card. It is **off by default**, and its values are deployment
configuration (environment variables), not repository content.

- `PRICING_MARKUP_PERCENT` (`0` = off) and `PRICING_MARKUP_EXEMPT_EMAIL_DOMAINS`
  (comma separated, no `@`) are read from the environment.
- An organization whose active owner has a **verified** email in an exempt domain
  pays the platform rate exactly; every other organization pays it plus the
  percentage (rounded half-up to the rate's six decimal places).
- Negotiated rules (organization, business or WABA specific) are never adjusted.
- The exemption needs a verified email and an exact domain match, so look-alike
  domains and unverified addresses are not exempt.
- `MessageCost.pricing_snapshot` records `meta_rate`, `markup_percent`,
  `markup_reason` and the charged `unit_rate` so any cost can be reconciled against
  the provider's own rate. A cost that was already priced is never recomputed when
  the setting changes; previews (`estimate_cost`) use the same amounts.

## Loading rate cards (operator)

```bash
# 1. markets: CSV code,name,calling_codes   (calling codes separated by ";", e.g. IN,India,91)
docker compose exec backend python manage.py load_pricing_markets --file markets.csv \
    --effective-from 2026-10-01 --source "Meta rate card 2026-10"
# 2. rates: CSV market,category,rate[,volume_tier_min,volume_tier_max]
docker compose exec backend python manage.py load_rate_card --file rates_inr.csv \
    --currency INR --effective-from 2026-10-01 --source "Meta rate card 2026-10" --card-version 2026-10
```

CSV files need a header row, and the path must be readable inside the
container (copy the file with `docker compose cp`, or mount it). Both commands
close the previous open rows (`effective_to`) instead of
editing them, so older costs keep pointing at the rates that applied. Rule
changes are audited (`billing.pricing_rule_changed`). Take the values only from
Meta's published rate-card files for the currency your portfolio is billed
in.
