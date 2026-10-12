# Frontend design system

The console's UI is built from one token set (`frontend/app/globals.css`) and a
small library of shared components (`frontend/components/ui/`). Pages compose
these components rather than styling themselves.

## Tokens

| Group | Tokens | Notes |
|---|---|---|
| Surfaces | `background` (app), `surface` (panels, tables), `surface-muted` (thread), `popover` | near-white / white; charcoal text (`foreground`) |
| Accent | `primary` (green, AA on white), `brand`, `brand-soft` | primary actions, active navigation, focus ring, outbound bubbles. Never a page background |
| Neutrals | `muted`, `muted-foreground`, `border`, `input` | |
| Semantic | `success`, `warning`, `destructive`/`danger`, `info`, each with a `-soft` background | always paired with a text label or icon |
| Charts | `chart-1 … chart-5` | |
| Radius | base `0.5rem` (`rounded-md/lg`) | restrained |
| Elevation | `shadow-xs` (panels), `shadow-md` (menus), `shadow-lg` (toasts, drawers) | |

A dark palette is defined (`.dark`) but not switchable in the UI yet.

## Typography (Geist)

`text-display` (login headline), `text-page-title`, `text-section-title`,
`text-body` (default), `text-supporting` (muted 13px), `text-caption` (muted 12px).
Numbers in tables and KPIs use `tabular-nums`.

## Components

| Component | File | Use |
|---|---|---|
| Button, Input, Badge, Card, Label | `button.tsx`, … | shadcn base-nova (Base UI) |
| Dialog, Sheet (drawer), DropdownMenu, Popover, Tooltip, Tabs, Table, Avatar, Skeleton, ScrollArea | shadcn base-nova | |
| `StatusBadge` + `toneFor()` | `status-badge.tsx` | every status pill (dot + text) |
| `EmptyState`, `ErrorState`, `TableSkeleton`, `ConversationSkeleton`, `PageSkeleton` | `states.tsx` | honest empty/error/loading states. `ErrorState` maps network/401/403/404/config errors to plain titles and shows the backend's safe message and request id |
| `Spinner`, `LoadingButton` | `spinner.tsx` | loading tied to a real request |
| `PageHeader`, `Panel`, `Toolbar`, `SearchInput`, `Kpi`, `DetailList` | `page.tsx` | page scaffolding |
| `InitialsAvatar` | `initials-avatar.tsx` | contacts and users (no photos are stored) |
| `ToastProvider`/`useToast` | `toast.tsx` | outcome of mutations (aria-live) |
| `NativeSelect`, `Textarea` | `native-select.tsx` | accessible native controls |

## Shell

`components/layout/app-shell.tsx` provides the collapsible sidebar (the
preference is stored locally), the mobile drawer, and a header showing the
business context, contact/conversation search, account notifications (real
Meta account events) and the account menu. The business switcher lists only
the businesses the API returns, with their real number counts. Navigation
comes from `features/registry.ts` (live sections the member has permission
for).

## Rules

- Show backend data only. Empty data means an empty state, never sample
  content. Rates the API returns as `null` render as "—" / "No data
  available", not 0 %.
- Animation is reserved for real events: new messages that arrive after a
  thread is opened, drawers, toasts. `prefers-reduced-motion` is honoured.
- One realtime socket per business, shared by all subscribers
  (`lib/realtime/client.ts#acquireRealtime`).
- Icons: lucide only, `aria-hidden` when decorative; icon-only buttons carry
  `aria-label`.

## Command home (`features/command/`)

The home route (`/`) is intent-first: one command input, suggestions built from
real business data (counts only appear when the API reports them), and the
result rendered in place. The detailed pages are unchanged and one click away
in the sidebar; the old overview is behind a "Business overview" disclosure
(remembered per browser).

- `parse.ts` is deterministic: explicit rules map text to a fixed intent set
  (delivery performance, failed messages, costs incl. cross-business
  comparison, running campaigns, templates needing attention, unread
  conversations, search, navigate, launch/pause campaign). There is no model.
  Imperative verbs it does not implement ("delete", "send", "create"...) are
  refused as `unsupported` with the list of what does work, and a request that
  is only a name becomes a search that says so.
- `run.ts` calls the same typed services as the pages. Nothing is privileged:
  tenant headers come from the API client, and the server's 403/404 is
  final. Client-side permission checks only avoid pointless round trips and
  give a clear message.
- Consequential actions (launch, pause) never run from the command text. They
  return a confirmation built from the server's own preview (recipients, rate,
  failed checks); the write happens only on the explicit confirm click, uses the
  existing audited endpoints, and a failed launch check disables confirmation.
- Cross-business comparison reads each business with an explicit
  `X-Business-ID` (`apiRequest({ businessId })`); the server still verifies the
  member may access it.

## Simple and Advanced mode (`stores/ui-mode.tsx`)

A per-browser *presentation* preference (default Simple; switch in the account
menu). It never changes what a person may do: the permission check is applied
first in both modes and the server stays the authority, so Simple can only show
less than Advanced (`features/registry.ts#visibleSections`, covered by tests that
assert the subset property for arbitrary permission sets).

- **Simple:** Home, Inbox, Contacts, Templates, Campaigns, WhatsApp numbers,
  Settings (plainer labels where useful); technical settings sections (roles,
  business access, API keys, WhatsApp setup, audit log) and dense detail (WABA
  ids, provider fields, the business-overview disclosure) are left out, with a
  plain pointer to Advanced.
- **Advanced:** exactly the navigation and pages that existed before.

## Connection health (`features/health/`)

`HealthPanel` (Numbers page) and `HealthSummaryCard` (Home) render the
`/whatsapp/health/` response: problems first, "fine" items collapsed, each issue
with a plain explanation, a next step, and where to fix it ("Fix here" or "Fix in
Meta's WhatsApp Manager"). Nothing is shown as healthy without a signal; loading,
error and nothing-connected are distinct states. "Refresh from Meta" asks Meta
(verify + sync) and then polls until every account and number reports a later
"last checked" time; it says "Updated" only when that happened, otherwise
"Still waiting". The command box answers "Is everything healthy with my WhatsApp
numbers?" from the same endpoint, and offers that suggestion only when real data
shows something needs attention.

## Failed messages (`features/recovery/`)

`/failed-messages` (Simple and Advanced; needs `conversations.view`) renders
`/messages/failures/`: each failure shows who, when, a plain explanation, what
WhatsApp reported, one next step, and a kind badge (Safe to retry / Fix first /
Outcome unclear / Fix in Meta). "Review retry" appears only when the server says
`can_retry` and the person has `messages.send`; it opens a dialog built from the
server's preview (to, from, message, duplicate risk, cost) and sends nothing
until "Confirm and retry". After confirming the UI says the retry was *queued*;
the retry's status is then read from the server (polling every 5 s only while a
retry is in flight) and "Delivered" appears only on WhatsApp's delivered or read
report. Meta-only fixes show an "Open WhatsApp Manager" link instead of a retry.
Advanced additionally shows the category and provider code. The command box
answers "Which messages failed and can I retry them?" from the same endpoint;
it never retries.

## Set up WhatsApp (`features/setup/`)

`/setup` (Simple and Advanced; needs `numbers.view`) is a step list rendered from
`/whatsapp/setup/`. Progress is never stored in the browser: it is what is really
set up, so leaving and returning resumes at the first unfinished step. Each step
shows a state in words (Done, Not yet, Needs fixing, Not available, Can't tell
yet), a plain explanation and next step, and where the fix lives. Advanced adds a
"What this is based on" evidence list. Connect uses Meta's guided sign-up, which
first asks the server for a one-time `state`; if the deployment does not support
it, the step says so instead of showing a dead button. "Refresh from Meta" says
"Updated" only when Meta's last-checked time actually moved.

The test message is three separate stages: a form (sends nothing), a preview
(recipient, sender, exact content, type, known cost, restrictions) and an approval
gate (an unchecked "I approve sending exactly this message" box; Send stays
disabled until ticked, and the server requires the single-use token plus
`approve: true`). After sending, the UI says only "queued", then follows the
message's own stored status: "Accepted" and "Sent" are shown as not delivered yet;
only the provider's delivered/read report is shown as delivered; a failure links to
Failed messages. The command box answers "Is my WhatsApp set up?" from the same
endpoint and points "send a test message" to this page; it never sends.

## Template Studio (`features/templates/studio/`)

"New template" on the Templates page opens a five-step guide: purpose (plain
questions instead of Meta's category names; unsupported purposes are shown and
explained, not hidden), name and language, write it (variables via "Add a
variable", an example for each, character counters, buttons), check it (the
server's list of problems and good-to-know notes, with a live preview), and
review and send. The checks come from the server on every change, so the preview,
the checks and what is submitted cannot drift. "Save draft" never sends. Sending
needs the review step's ticked confirmation, saves the draft, then submits with the
hash of exactly what was reviewed; afterwards the UI says only "Sent to Meta" and
shows Meta's reported status, labelled "Not yet confirmed by Meta" until a sync or
status update after the submit confirms it. A draft that was sent is read-only with
"Check with Meta". A rejected template's drawer shows Meta's reason code verbatim,
what it means, how to fix it, an official link, and "Create a corrected copy".
Advanced additionally shows the variable style choice and the exact components that
would be sent. The command box answers "I want to create a template" by pointing to
the Studio (`/templates?new=1`) and listing real drafts; it never creates or sends.
There is no AI-generated approval: starter examples are plain editable text.
