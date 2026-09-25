"use client";

import { MegaphoneIcon, PlusIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { PageHeader, Panel, Toolbar } from "@/components/ui/page";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApiQuery } from "@/hooks/use-api-query";
import { useBusinessRealtime } from "@/hooks/use-business-realtime";
import { presetRange, shiftDate } from "@/lib/analytics";
import { analyticsService } from "@/services/analytics";
import { campaignService } from "@/services/campaigns";
import { useSession } from "@/stores/session-context";
import type { Campaign, CampaignProgress } from "@/types/api";

import { CampaignDetail } from "./campaign-detail";
import { CampaignEditor } from "./campaign-editor";
import { CampaignProgressBar, CampaignStatusBadge, STATUS_LABEL } from "./campaign-progress";

type Mode = { kind: "list" } | { kind: "edit"; campaign: Campaign | null } | { kind: "detail"; id: string };

function n(value: number): string {
  return value.toLocaleString();
}

/** Campaigns of the selected business — everything from persisted backend state. */
export function CampaignsView() {
  const { active, business, hasPermission } = useSession();
  const [mode, setMode] = useState<Mode>({ kind: "list" });
  const [status, setStatus] = useState("");
  const [live, setLive] = useState<
    Record<string, { status: Campaign["status"]; progress: CampaignProgress }>
  >({});
  const isList = mode.kind === "list";
  const list = useApiQuery(
    () => campaignService.list({ status: status || undefined }),
    [business?.id, status],
    Boolean(business) && isList,
  );
  // Real cost per campaign = MessageCost rollups grouped by campaign (last 400 days).
  const today = presetRange("today", business?.timezone || undefined).date_to;
  const costRange = { date_from: shiftDate(today, -399), date_to: today };
  const costs = useApiQuery(
    () => analyticsService.costs(costRange, "campaign"),
    [business?.id, costRange],
    Boolean(business) && isList && hasPermission("analytics.view"),
  );
  const costByCampaign = useMemo(() => {
    const map = new Map<string, string>();
    for (const row of costs.data?.rows ?? []) {
      if (!row.key) continue;
      const amount = `${row.currency} ${Number(row.amount).toLocaleString(undefined, { maximumFractionDigits: 4 })}`;
      map.set(row.key, map.has(row.key) ? `${map.get(row.key)} · ${amount}` : amount);
    }
    return map;
  }, [costs.data]);

  useBusinessRealtime(active?.organization.id, business?.id, {
    onEvent: (event) => {
      if (!isList || !event.event.startsWith("campaign.")) return;
      const data = event.data as
        { id: string; status: Campaign["status"]; progress: CampaignProgress } | undefined;
      if (data?.id)
        setLive((prev) => ({ ...prev, [data.id]: { status: data.status, progress: data.progress } }));
    },
    onResync: () => list.reload(),
  });

  if (!business) return null;
  if (mode.kind === "edit") {
    return (
      <CampaignEditor
        campaign={mode.campaign}
        onSaved={() => undefined}
        onLaunched={(c) => setMode({ kind: "detail", id: c.id })}
        onCancel={() => setMode({ kind: "list" })}
      />
    );
  }
  if (mode.kind === "detail") {
    return (
      <CampaignDetail
        campaignId={mode.id}
        onEdit={(c) => setMode({ kind: "edit", campaign: c })}
        onBack={() => {
          setMode({ kind: "list" });
          list.reload();
        }}
      />
    );
  }

  const rows = list.data?.results ?? [];
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Campaigns"
        description={`Template campaigns sent from ${business.name}'s WhatsApp numbers.`}
        actions={
          hasPermission("campaigns.manage") ? (
            <Button size="sm" onClick={() => setMode({ kind: "edit", campaign: null })}>
              <PlusIcon aria-hidden /> New campaign
            </Button>
          ) : null
        }
      />
      <Panel bodyClassName="overflow-x-auto">
        <Toolbar>
          <NativeSelect
            aria-label="Filter by status"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="">All statuses</option>
            {Object.entries(STATUS_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </NativeSelect>
          {list.data ? <span className="text-caption ml-auto">{rows.length} campaign(s)</span> : null}
        </Toolbar>
        {list.error ? <ErrorState error={list.error} onRetry={list.reload} inline className="m-4" /> : null}
        {!list.data && !list.error ? (
          <TableSkeleton columns={8} />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={MegaphoneIcon}
            title={status ? "No campaigns with this status" : "No campaigns yet"}
            description={
              status
                ? "Choose another status to see more."
                : "Create a campaign to send an approved template to an audience of this business's contacts."
            }
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Campaign</TableHead>
                <TableHead className="hidden xl:table-cell">Business</TableHead>
                <TableHead className="hidden md:table-cell">Template</TableHead>
                <TableHead className="text-right">Audience</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="hidden text-right lg:table-cell">Sent</TableHead>
                <TableHead className="hidden text-right lg:table-cell">Delivered</TableHead>
                <TableHead className="hidden text-right lg:table-cell">Read</TableHead>
                <TableHead className="hidden text-right sm:table-cell">Failed</TableHead>
                <TableHead className="hidden text-right xl:table-cell">Cost</TableHead>
                <TableHead className="hidden lg:table-cell">Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((c) => {
                const state = live[c.id] ?? { status: c.status, progress: c.progress };
                const p = state.progress;
                return (
                  <TableRow
                    key={c.id}
                    className="cursor-pointer"
                    onClick={() => setMode({ kind: "detail", id: c.id })}
                  >
                    <TableCell className="min-w-52">
                      <button type="button" className="text-left font-medium">
                        {c.name}
                      </button>
                      <div className="mt-1 max-w-56">
                        <CampaignProgressBar progress={p} compact />
                      </div>
                    </TableCell>
                    <TableCell className="hidden xl:table-cell">{business.name}</TableCell>
                    <TableCell className="hidden md:table-cell">
                      <span className="block max-w-48 truncate">{c.template_name}</span>
                      <span className="text-caption">{c.phone_number_display}</span>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {c.audience_count === null ? (
                        <span className="text-caption">at launch</span>
                      ) : (
                        n(c.audience_count)
                      )}
                    </TableCell>
                    <TableCell>
                      <CampaignStatusBadge status={state.status} />
                    </TableCell>
                    <TableCell className="hidden text-right tabular-nums lg:table-cell">
                      {n(p.sent_or_later)}
                    </TableCell>
                    <TableCell className="hidden text-right tabular-nums lg:table-cell">
                      {n(p.delivered_or_later)}
                    </TableCell>
                    <TableCell className="hidden text-right tabular-nums lg:table-cell">
                      {n(p.read)}
                    </TableCell>
                    <TableCell className="hidden text-right tabular-nums sm:table-cell">
                      {n(p.failed)}
                    </TableCell>
                    <TableCell className="text-caption hidden text-right xl:table-cell">
                      {hasPermission("analytics.view") ? (costByCampaign.get(c.id) ?? "—") : "—"}
                    </TableCell>
                    <TableCell className="text-caption hidden lg:table-cell">
                      {new Date(c.created_at).toLocaleDateString()}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </Panel>
    </div>
  );
}
