# ADR-008: Separate provider usage cost from SaaS billing

- **Status:** Accepted
- **Date:** 2026-09-23

## Context

Meta charges us per delivered message; tenants will pay us subscription fees. Mixing the two corrupts both.

## Decision

`apps/billing` covers provider usage cost only: effective-dated `PricingRule` data, immutable `MessageCost` snapshots, billable-ness taken from Meta's pricing report. SaaS subscription billing will be a separate module.

## Consequences

- (+) Rate changes never rewrite history; no prices are hard-coded.
- (-) An operator must load rate cards from Meta's published files (`load_pricing_markets`, `load_rate_card`, Cycle 2).
