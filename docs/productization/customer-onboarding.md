# Customer onboarding

## What exists today (Cycle 4)

```
Signup (POST /api/v1/auth/register/)
  -> creates a User + a brand-new Organization the caller owns
  -> logs the caller in (session cookie)
Invite teammates (POST /api/v1/organizations/current/invitations/)
  -> members.manage only; returns a one-time token + accept link
  -> invitee accepts (POST /api/v1/invitations/<id>/accept/), either as a
     brand-new account (email + password) or an already-registered user
     whose email matches
Connect a WhatsApp Business Account (existing, pre-Cycle-4:
  `connect_meta_waba` management command / apps.whatsapp)
Sync numbers and templates (existing, pre-Cycle-4)
Ready to use the inbox
```

Registration and invitation-acceptance both use *actual* backend state — a
membership row either exists with `status=ACTIVE` or it doesn't; there is no
separate "onboarding progress" model to fake or drift out of sync. A
frontend onboarding checklist should read directly from
`GET /api/v1/organizations/current/` (org exists), `GET /api/v1/whatsapp/accounts/`
(WABA connected), and `GET /api/v1/whatsapp/numbers/` (numbers synced) rather
than maintaining its own progress flags.

## What's missing

- **No email is sent.** `create_invitation` returns the raw token in the API
  response for the inviter to copy/share manually; nothing paged through an
  email provider. There is no `EMAIL_BACKEND` configured anywhere in this
  codebase (verified in the architecture audit) — this was an explicit,
  deferred decision for this cycle. See "Enabling email" below for exactly
  what turning it on requires.
- **No frontend pages** for register / invitation-preview / accept-invite
  exist yet — this cycle is the backend API only. The existing frontend
  login page (`frontend/features/auth/`) is the pattern to extend.
- **Plan selection** ("Ready to Use" step in the brief's journey) doesn't
  exist — subscription plans are a deferred decision (see
  `subscription-and-billing.md`).
- **Meta Embedded Signup** was not investigated in this cycle (the existing
  `connect_meta_waba` flow requires a manually-provisioned System User
  token per docs/integrations/meta) — evaluating Embedded Signup is future
  work, not started here.

## Enabling email (when the provider decision is made)

Django's built-in `send_mail` / `EMAIL_BACKEND` mechanism is provider-agnostic
by design — SMTP, Amazon SES, SendGrid, Postmark, etc. all plug in via the
same settings without application code changes:

```python
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env.str("EMAIL_HOST")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env.str("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = env.str("EMAIL_HOST_PASSWORD")  # PROVIDER_SECRET_-style, never logged
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL")
```

Once configured, `apps.organizations.services.create_invitation` is the one
call site to extend: send the accept link (already computed as
`invitation.accept_path` in the API response) via `send_mail` instead of
(or in addition to) returning the raw token. The same applies to password
reset once that's built (see architecture-audit.md §2).
