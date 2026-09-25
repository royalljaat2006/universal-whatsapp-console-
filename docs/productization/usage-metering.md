# Usage metering — partially exists, SaaS metering deferred

**Meta messaging usage** is already metered accurately: `apps.billing.MessageCost`
records one immutable row per message with its pricing snapshot, computed
from `PricingRule` at send time. This is the "Meta WhatsApp charges" category
and needs no new work.

**SaaS subscription usage** (messages sent/received against a plan's monthly
allowance, active contacts, connected numbers, team members, storage, API
requests — the brief's full list) is **not implemented**, because it has
nothing to meter against yet: there is no `Plan`/`Subscription` model (see
`subscription-and-billing.md`, deferred pending the payment-gateway and
pricing decisions).

## What's countable today, without any new model

Everything a future plan-limit check would need already exists as a live
count, not a separate ledger to keep in sync:

| Would-be limit | Where the real number lives today |
|---|---|
| Team members | `Membership.objects.filter(organization=org, status="active").count()` |
| Connected WABAs | `WhatsAppBusinessAccount.objects.for_organization(org).count()` |
| Phone numbers | `WhatsAppPhoneNumber` count, same pattern |
| Messages this period | `apps.billing.MessageCost` rows in the period (already tenant- and time-scoped) |
| Campaign volume | `Campaign`/`CampaignEvent` counts |
| Contacts | `Contact.objects.for_organization(org).count()` |
| API requests | not currently counted — `ApiKey.last_used_at` exists but there is no per-request counter; would need a lightweight counter (Redis, matching the existing rate-limit pattern in `core/ratelimit.py`) if API-usage billing is wanted |

Building SaaS usage metering later is mostly "read these existing counts on
a schedule and compare to `Subscription.plan.limits`" rather than a new
instrumentation project — the one gap is API request counting, which the
existing rate-limiter's Redis pattern already shows how to do cheaply.
