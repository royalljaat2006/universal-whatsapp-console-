# ADR-007: Session authentication for the first-party web app

- **Status:** Accepted
- **Date:** 2026-09-23

## Context

The console is a same-origin SPA behind nginx. Bearer tokens in browser storage are exposed to XSS.

## Decision

Django sessions (HttpOnly, SameSite=Lax, Secure in production) with CSRF on every unsafe method, including login. API credentials for machine clients are a later addition (separate authentication class).

## Consequences

- (+) No tokens in JavaScript; revocation is server-side.
- (-) Cross-origin clients will need another mechanism later (API keys / OAuth).
