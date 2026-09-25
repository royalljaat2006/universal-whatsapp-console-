# ADR-011: Businesses (workspaces) inside an organization

- **Status:** Accepted
- **Date:** 2026-09-23

## Context

One organization runs several lines of business (currently Kiosk with 5
numbers and EPS with 3), each with its own WABA and customers. Staff of one
business must not see another business's numbers, contacts, conversations or
messages. More businesses will be added later, so none can be hard-coded.

## Decision

- New `Business` entity (`apps/businesses`): Organization → Business → WABA →
  PhoneNumber. Kiosk and EPS are rows, created by API or `manage.py create_business`.
- A `business_id` column is denormalised onto every business-owned row
  (WABA, phone number, template, contact, contact identity, conversation,
  message). Models derive it from their parent on save, so it can never
  disagree with the chain.
- **Contacts are per business:** uniqueness is (business, kind, scope, value).
  The same WhatsApp user writing to Kiosk and EPS becomes two contacts.
- Access: `Membership.business_scope` is `all` (owners/admins by default) or
  `selected` with explicit `BusinessAccess` grants. Other roles default to
  **no** business (least privilege).
- API: optional `X-Business-ID` header selects the context. The server
  validates it against the membership (404 otherwise) and, even without it,
  restricts every business-owned queryset to accessible businesses
  (`OrganizationScopedMixin.business_field`). Sends validate the business of
  the number or conversation, never trusting client ids.
- Realtime: channel groups per business (`biz.<id>`). A socket joins only
  after membership, permission and business-access checks.
- Existing rows were backfilled into a per-organization "Default" business
  (migration `businesses.0002`), then the columns were made NOT NULL.

## Consequences

- (+) Isolation is enforced in one place for REST and realtime, and is tested
  end to end (`tests/api/test_business_isolation.py`, `tests/integration/test_realtime.py`).
- (+) Per-business reporting, campaigns and billing can group by `business_id`
  without joins.
- (-) A customer shared by two businesses has two contact records. This is
  intended, and merging across businesses is out of scope.
- (-) A WABA cannot move between businesses (409). Moving it would split
  history, so it needs an explicit migration tool if ever required.
