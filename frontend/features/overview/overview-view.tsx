"use client";

import {
  ActivityIcon,
  ArrowRightIcon,
  InboxIcon,
  MegaphoneIcon,
  ServerIcon,
  SmartphoneIcon,
} from "lucide-react";
import Link from "next/link";

import { InitialsAvatar } from "@/components/ui/initials-avatar";
import { Kpi, PageHeader, Panel } from "@/components/ui/page";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import { StatusBadge, toneFor } from "@/components/ui/status-badge";
import { useApiQuery } from "@/hooks/use-api-query";
import { count, money, percent, presetRange } from "@/lib/analytics";
import { shortTime } from "@/lib/format";
import { analyticsService } from "@/services/analytics";
import { campaignService } from "@/services/campaigns";
import { fetchReadiness } from "@/services/health";
import { inboxService } from "@/services/inbox";
import { whatsappService } from "@/services/whatsapp";
import { useSession } from "@/stores/session-context";

import { OnboardingChecklist } from "./onboarding-checklist";

const HEALTH_LABELS: Record<string, string> = {
  database: "PostgreSQL",
  migrations: "Database migrations",
  redis_cache: "Redis",
};

function SeeAll({ href, label }: { href: string; label: string }) {
  return (
    <Link
      href={href}
      className="text-primary inline-flex items-center gap-1 text-xs font-medium hover:underline"
    >
      {label} <ArrowRightIcon aria-hidden className="size-3" />
    </Link>
  );
}

/** Business overview composed only of real backend reads. */
export function OverviewView() {
  const { business, hasPermission, user } = useSession();
  const enabled = Boolean(business);
  const today = presetRange("today", business?.timezone || undefined);
  const numbers = useApiQuery(
    () => whatsappService.numbers(),
    [business?.id],
    enabled && hasPermission("numbers.view"),
  );
  const overview = useApiQuery(
    () => analyticsService.overview(today),
    [business?.id, today],
    enabled && hasPermission("analytics.view"),
  );
  const unread = useApiQuery(
    () => inboxService.conversations({ unread: "1" }),
    [business?.id],
    enabled && hasPermission("conversations.view"),
  );
  const campaigns = useApiQuery(
    () => campaignService.list(),
    [business?.id],
    enabled && hasPermission("campaigns.view"),
  );
  const health = useApiQuery((signal) => fetchReadiness(signal), []);

  const numberRows = numbers.data?.results ?? [];
  const connected = numberRows.filter((n) => n.status === "connected").length;
  const o = overview.data;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={business ? business.name : "Overview"}
        description={`Welcome back${user?.full_name ? `, ${user.full_name.split(" ")[0]}` : ""}. Here is what is happening today.`}
      />

      <OnboardingChecklist numbers={numbers.data?.results} />

      {hasPermission("analytics.view") ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Kpi
            label="Messages today"
            value={o ? count(o.totals.inbound + o.totals.outbound) : "…"}
            hint={o ? `${count(o.totals.outbound)} sent · ${count(o.totals.inbound)} received` : undefined}
          />
          <Kpi
            label="Delivery rate"
            value={o ? percent(o.rates.delivery_rate) : "…"}
            hint={o && o.rates.delivery_rate === null ? "No outbound messages yet" : undefined}
          />
          <Kpi label="Read rate" value={o ? percent(o.rates.read_rate) : "…"} />
          <Kpi label="Failed" value={o ? count(o.totals.failed) : "…"} />
          <Kpi
            label="Cost today"
            value={o ? (o.cost.by_currency.length ? money(o.cost.by_currency) : "—") : "…"}
            hint={o?.cost.state === "pending_rate" ? "Awaiting a rate card" : undefined}
          />
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-3">
        {hasPermission("numbers.view") ? (
          <Panel
            title="WhatsApp numbers"
            actions={<SeeAll href="/numbers" label="Manage" />}
            className="lg:col-span-1"
          >
            {numbers.error ? (
              <ErrorState error={numbers.error} onRetry={numbers.reload} inline className="m-4" />
            ) : null}
            {!numbers.data && !numbers.error ? <TableSkeleton rows={3} columns={2} /> : null}
            {numbers.data && numberRows.length === 0 ? (
              <EmptyState
                compact
                icon={SmartphoneIcon}
                title="No numbers connected"
                description="Connect a WhatsApp Business Account to start messaging."
              />
            ) : null}
            {numberRows.length ? (
              <>
                <p className="text-supporting px-4 pt-3">
                  {connected} of {numberRows.length} connected
                </p>
                <ul className="divide-y">
                  {numberRows.slice(0, 6).map((n) => (
                    <li key={n.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium tabular-nums">
                          {n.display_phone_number}
                        </span>
                        <span className="text-caption block truncate">
                          {n.verified_name || n.label || "—"}
                        </span>
                      </span>
                      <StatusBadge tone={toneFor(n.status)}>{n.status}</StatusBadge>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </Panel>
        ) : null}

        {hasPermission("conversations.view") ? (
          <Panel title="Unread conversations" actions={<SeeAll href="/inbox" label="Open inbox" />}>
            {unread.error ? (
              <ErrorState error={unread.error} onRetry={unread.reload} inline className="m-4" />
            ) : null}
            {!unread.data && !unread.error ? <TableSkeleton rows={3} columns={2} /> : null}
            {unread.data && unread.data.results.length === 0 ? (
              <EmptyState
                compact
                icon={InboxIcon}
                title="You're all caught up"
                description="No conversations are waiting for a reply."
              />
            ) : null}
            <ul className="divide-y">
              {unread.data?.results.slice(0, 6).map((c) => (
                <li key={c.id}>
                  <Link href="/inbox" className="hover:bg-muted/50 flex items-center gap-3 px-4 py-2.5">
                    <InitialsAvatar name={c.contact_name} seed={c.contact_id} size="sm" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{c.contact_name}</span>
                      <span className="text-caption block truncate">{c.last_message_preview}</span>
                    </span>
                    <span className="flex flex-col items-end gap-1">
                      <span className="text-caption">{shortTime(c.last_message_at)}</span>
                      <span className="bg-primary text-primary-foreground rounded-full px-1.5 text-[10px] font-semibold tabular-nums">
                        {c.unread_count}
                      </span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}

        {hasPermission("campaigns.view") ? (
          <Panel title="Recent campaigns" actions={<SeeAll href="/campaigns" label="All campaigns" />}>
            {campaigns.error ? (
              <ErrorState error={campaigns.error} onRetry={campaigns.reload} inline className="m-4" />
            ) : null}
            {!campaigns.data && !campaigns.error ? <TableSkeleton rows={3} columns={2} /> : null}
            {campaigns.data && campaigns.data.results.length === 0 ? (
              <EmptyState
                compact
                icon={MegaphoneIcon}
                title="No campaigns yet"
                description="Campaigns you create for this business appear here."
              />
            ) : null}
            <ul className="divide-y">
              {campaigns.data?.results.slice(0, 6).map((c) => (
                <li key={c.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium">{c.name}</span>
                    <span className="text-caption block">
                      {c.progress.processed.toLocaleString()} / {c.progress.total.toLocaleString()} processed
                    </span>
                  </span>
                  <StatusBadge tone={toneFor(c.status)}>{c.status}</StatusBadge>
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}
      </div>

      <Panel
        title="Platform status"
        description="Checked live against the backend."
        actions={
          health.data ? (
            <StatusBadge tone={health.data.status === "ok" ? "success" : "danger"}>
              {health.data.status === "ok" ? "operational" : "unavailable"}
            </StatusBadge>
          ) : null
        }
      >
        {health.error ? (
          <ErrorState error={health.error} onRetry={health.reload} inline className="m-4" />
        ) : null}
        {!health.data && !health.error ? <TableSkeleton rows={3} columns={2} /> : null}
        {health.data ? (
          <ul className="grid divide-y sm:grid-cols-3 sm:divide-x sm:divide-y-0">
            {Object.entries(health.data.checks).map(([name, check]) => (
              <li key={name} className="flex items-center justify-between gap-3 px-4 py-3">
                <span className="flex items-center gap-2 text-sm">
                  {name === "database" ? (
                    <ServerIcon aria-hidden className="text-muted-foreground size-4" />
                  ) : (
                    <ActivityIcon aria-hidden className="text-muted-foreground size-4" />
                  )}
                  {HEALTH_LABELS[name] ?? name}
                </span>
                <span className="flex items-center gap-2">
                  <span className="text-caption font-mono">{check.duration_ms} ms</span>
                  <StatusBadge tone={check.ok ? "success" : "danger"}>
                    {check.ok ? "ok" : "failed"}
                  </StatusBadge>
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </Panel>
    </div>
  );
}
