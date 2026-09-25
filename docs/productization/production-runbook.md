# Production runbook (Cycle 4 additions)

This supplements `docs/deployment.md` (images, topology, release procedure,
probes) — read that first. This document covers only what Cycle 4 changed
operationally.

## New environment variables

None required. Registration, invitations and API keys use existing settings
(`DJANGO_SECRET_KEY`, the database, `FIELD_ENCRYPTION_KEYS` is *not* used by
them — invitation/API-key secrets are hashed, not encrypted, since they only
ever need to be verified, never recovered). If email is enabled later
(`customer-onboarding.md`), `EMAIL_*` settings will be added then.

## New migrations to apply

```
organizations.0006_invitation
organizations.0007_api_keys_permission   # backfills api_keys.manage onto
                                          # existing owner/admin roles
apikeys.0001_initial
```

Apply via the existing release procedure (`docs/deployment.md`'s "Migrate as
a separate one-off step") — nothing here is destructive or requires
downtime; `0007` only adds rows to the `Role.permissions` M2M for existing
organizations, it does not alter any existing table's shape.

## Granting platform-admin access

The platform console (`/api/v1/platform/*`) is gated on `is_staff`, the same
flag Django's admin site already uses. Grant it the same way an operator
already gets admin access today:

```
python manage.py shell -c "
from apps.identity.models import User
u = User.objects.get(email='ops@example.com')
u.is_staff = True
u.save(update_fields=['is_staff'])
"
```

There is no new grant path, no separate password, and nothing exposed in the
frontend for this yet (Cycle 4 is the backend API only — see
`architecture-audit.md`'s note on remaining frontend work).

## Operational checks specific to this cycle

- `GET /api/v1/platform/operations/` (staff-only) — the fastest way to see
  webhook health, org counts and infra checks in one call after a deploy.
- After the `0007` migration, spot-check that `api_keys.manage` actually
  reached existing organizations' owner/admin roles:
  `Role.objects.filter(key="owner", permissions__code="api_keys.manage").count()`
  should equal the number of organizations.
- Invitation tokens and API keys are shown exactly once, in the creation
  response — if a client loses one, the only recovery is revoke-and-recreate;
  there is no "show again" endpoint by design (matches the existing
  `ProviderCredential` write-only convention in `docs/security.md`).

## Rollback

Every Cycle 4 change is additive (new app, new endpoints, new permission
code, new migration) — nothing in this cycle altered or removed existing
behavior for Kiosk/EPS or any other existing organization. Rolling back the
code is safe without a corresponding data migration rollback; the new tables
(`organizations_invitation`, `apikeys_apikey`) and the `api_keys.manage`
permission simply become unused, not inconsistent.
