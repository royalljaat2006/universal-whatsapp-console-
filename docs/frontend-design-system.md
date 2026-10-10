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
