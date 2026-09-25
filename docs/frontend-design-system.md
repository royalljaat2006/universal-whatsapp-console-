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
