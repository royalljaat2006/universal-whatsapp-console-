# ADR-002: Provider abstraction for WhatsApp

- **Status:** Accepted
- **Date:** 2026-09-23

## Context

Meta is the only provider today, but provider lock-in and API-version churn are real risks. Business logic must not know Graph URLs or payloads.

## Decision

`providers/whatsapp/base.py` defines `WhatsAppProvider` (outbound operations) and `WebhookAdapter` (verify + normalise inbound). Domain code exchanges provider-neutral value objects (`types.py`). All Meta specifics live in `providers/whatsapp/meta/`. Sending is a single `send_message(OutboundMessage)` with a typed content union rather than one method per message type. Providers never import domain models. Lookup goes through `registry.py`, which is also the test seam.

## Consequences

- (+) The Graph version, payloads and error codes change in one package.
- (+) Tests replace the HTTP transport, so no real network calls are possible.
- (-) Small mapping cost between Meta shapes and neutral types, accepted.
