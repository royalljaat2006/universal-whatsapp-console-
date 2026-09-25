# Meta integration

| Document | Contents |
|---|---|
| [api-assumptions.md](api-assumptions.md) | Every Meta behaviour the code relies on: API version, endpoints, fields, BSUIDs, limits, error codes, pricing, media, the service window, template sending, account webhooks, and the resolved pricing conflict |
| [webhooks.md](webhooks.md) | Handshake, signature, delivery semantics, payload shapes, fields to subscribe |
| [onboarding-runbook.md](onboarding-runbook.md) | Operator steps to connect a real WABA and number to this platform |
| [live-verification.md](live-verification.md) | Live tests A–F (real inbound, outbound, statuses, template, media) and their prerequisites |

Code map: everything Meta-specific lives in `backend/providers/whatsapp/meta/`
(`config.py`, `client.py`, `errors.py`, `payloads.py`, `provider.py`,
`webhooks.py`). Domain code only uses `providers/whatsapp/base.py` and
`types.py`.
