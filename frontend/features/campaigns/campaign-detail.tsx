"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiErrorNotice } from "@/components/status/api-error-notice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/native-select";
import { ExportButton } from "@/features/reports/export-button";
import { useApiQuery } from "@/hooks/use-api-query";
import { useBusinessRealtime } from "@/hooks/use-business-realtime";
import { durationLabel, money } from "@/lib/analytics";
import type { ApiError } from "@/lib/api/errors";
import { toApiError } from "@/lib/format";
import { campaignService } from "@/services/campaigns";
import { useSession } from "@/stores/session-context";
import type { Campaign, CampaignRecipientRow, RecipientStatus } from "@/types/api";

import { CampaignProgressBar, CampaignStatusBadge, CONTROLS } from "./campaign-progress";

const RECIPIENT_STATUSES: RecipientStatus[] = [
  "pending",
  "queued",
  "sending",
  "accepted",
  "sent",
  "delivered",
  "read",
  "failed",
  "skipped",
  "cancelled",
];

/**
 * Live campaign monitor. State changes arrive as backend realtime events
 * (campaign.updated / campaign.progress carry persisted counters); the page
 * also re-reads over REST after reconnects and every 15 s while active.
 */
export function CampaignDetail({
  campaignId,
  onEdit,
  onBack,
}: {
  campaignId: string;
  onEdit: (c: Campaign) => void;
  onBack: () => void;
}) {
  const { active, business, hasPermission } = useSession();
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [version, setVersion] = useState(0);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    let cancelled = false;
    campaignService
      .get(campaignId)
      .then((c) => !cancelled && setCampaign(c))
      .catch((cause) => !cancelled && setError(toApiError(cause)));
    return () => {
      cancelled = true;
    };
  }, [campaignId, version]);

  const running = campaign && ["queued", "running", "scheduled"].includes(campaign.status);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(refresh, 15000);
    return () => clearInterval(timer);
  }, [running, refresh]);

  useBusinessRealtime(active?.organization.id, business?.id, {
    onEvent: (event) => {
      if (!event.event.startsWith("campaign.")) return;
      const data = event.data as
        { id?: string; status?: Campaign["status"]; progress?: Campaign["progress"] } | undefined;
      if (data?.id !== campaignId) return;
      setCampaign((c) =>
        c && data.progress ? { ...c, status: data.status ?? c.status, progress: data.progress } : c,
      );
      if (event.event === "campaign.updated") refresh();
    },
    onResync: refresh,
  });

  const report = useApiQuery(
    () => campaignService.report(campaignId),
    [campaignId, version, campaign?.status],
    Boolean(campaign && campaign.status !== "draft"),
  );
  const events = useApiQuery(
    () => campaignService.events(campaignId),
    [campaignId, version, campaign?.status],
  );
  const recipients = useApiQuery(
    () => campaignService.recipients(campaignId, { status: statusFilter || undefined }),
    [campaignId, statusFilter, version],
    Boolean(campaign?.snapshot_built_at),
  );
  const [more, setMore] = useState<{ key: string; rows: CampaignRecipientRow[]; next: string | null } | null>(
    null,
  );
  const recipientKey = `${statusFilter}-${version}`;

  async function control(action: "pause" | "resume" | "cancel" | "retry") {
    if (
      action === "cancel" &&
      !window.confirm("Cancel this campaign? Recipients not yet sent will not receive it.")
    )
      return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (action === "cancel") {
        const result = await campaignService.cancel(campaignId);
        setCampaign(result.campaign);
        setNotice(
          `${result.pending_cancelled} pending recipient(s) cancelled; ${result.queued_stopped} queued message(s) stopped.`,
        );
      } else if (action === "retry") {
        const result = await campaignService.retryFailed(campaignId);
        setCampaign(result.campaign);
        setNotice(`${result.recipients} failed recipient(s) re-queued (safe failures only).`);
      } else {
        const result = await (action === "pause"
          ? campaignService.pause(campaignId)
          : campaignService.resume(campaignId));
        setCampaign(result.campaign);
      }
      refresh();
    } catch (cause) {
      setError(toApiError(cause));
    } finally {
      setBusy(false);
    }
  }

  if (!campaign)
    return error ? (
      <ApiErrorNotice error={error} />
    ) : (
      <p className="text-muted-foreground text-sm">Loading…</p>
    );

  const r = report.data;
  const rows = [...(recipients.data?.results ?? []), ...(more?.key === recipientKey ? more.rows : [])];
  const nextRecipients = more?.key === recipientKey ? more.next : (recipients.data?.next ?? null);
  const canRun = hasPermission("campaigns.launch");

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Button variant="ghost" size="xs" onClick={onBack}>
            ← Campaigns
          </Button>
          <h2 className="text-xl font-semibold">{campaign.name}</h2>
          <p className="text-muted-foreground text-xs">
            {campaign.template_name} ({campaign.template_language}) · {campaign.phone_number_display} ·{" "}
            {campaign.max_messages_per_minute}/min
            {campaign.scheduled_at
              ? ` · scheduled ${new Date(campaign.scheduled_at).toLocaleString()} (${campaign.timezone || "UTC"})`
              : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <CampaignStatusBadge status={campaign.status} />
          {campaign.status === "draft" && hasPermission("campaigns.manage") ? (
            <Button size="sm" onClick={() => onEdit(campaign)}>
              Edit / launch
            </Button>
          ) : null}
          {canRun
            ? CONTROLS[campaign.status].map((action) => (
                <Button
                  key={action}
                  size="sm"
                  variant={action === "cancel" ? "destructive" : "outline"}
                  disabled={busy || (action === "retry" && !(r?.failures.retryable_recipients ?? 0))}
                  onClick={() => void control(action)}
                >
                  {action === "retry"
                    ? `Retry safe failures (${r?.failures.retryable_recipients ?? 0})`
                    : action[0].toUpperCase() + action.slice(1)}
                </Button>
              ))
            : null}
        </div>
      </div>
      {error ? <ApiErrorNotice error={error} /> : null}
      {notice ? <p className="text-sm">{notice}</p> : null}
      {campaign.failure_reason ? (
        <p role="alert" className="border-destructive/40 bg-destructive/5 rounded-md border p-3 text-sm">
          Stopped: {campaign.failure_reason}
        </p>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Progress</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <CampaignProgressBar progress={campaign.progress} />
          <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-5">
            {RECIPIENT_STATUSES.map((s) => (
              <div key={s} className="rounded border p-2">
                <span className="text-muted-foreground block">{s}</span>
                <span className="text-base font-semibold tabular-nums">
                  {campaign.progress.by_status[s] ?? 0}
                </span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {r ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="text-sm">Report</CardTitle>
              <ExportButton
                kind="campaign_summary"
                params={{ campaign_id: campaign.id }}
                label="Export summary"
              />
            </CardHeader>
            <CardContent>
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                <dt className="text-muted-foreground">Business</dt>
                <dd>{r.campaign.business}</dd>
                <dt className="text-muted-foreground">Started</dt>
                <dd>{r.timing.started_at ? new Date(r.timing.started_at).toLocaleString() : "—"}</dd>
                <dt className="text-muted-foreground">Finished</dt>
                <dd>
                  {r.timing.finished_at
                    ? new Date(r.timing.finished_at).toLocaleString()
                    : r.timing.running
                      ? "running"
                      : "—"}
                </dd>
                <dt className="text-muted-foreground">Duration</dt>
                <dd>{durationLabel(r.timing.duration_seconds)}</dd>
                <dt className="text-muted-foreground">Messages created</dt>
                <dd className="tabular-nums">{r.messages_total}</dd>
                <dt className="text-muted-foreground">Total cost</dt>
                <dd>
                  {r.cost.by_currency.length
                    ? money(r.cost.by_currency)
                    : r.cost.state === "pending_rate"
                      ? `Pending — ${r.cost.pending_rate_messages} billable message(s) await a rate card`
                      : "No priced messages yet"}
                </dd>
                <dt className="text-muted-foreground">Average cost</dt>
                <dd>
                  {r.cost.by_currency.length
                    ? r.cost.by_currency.map((c) => `${c.currency} ${c.average_per_message}`).join(" · ")
                    : "—"}
                </dd>
              </dl>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Failures</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2 text-sm">
              {r.failures.groups.length === 0 && r.failures.skipped.length === 0 ? (
                <p className="text-muted-foreground">No failed or skipped recipients.</p>
              ) : null}
              {r.failures.groups.map((g) => (
                <details key={g.category} className="rounded border p-2">
                  <summary className="flex cursor-pointer justify-between">
                    <span>
                      {g.label}{" "}
                      {g.retryable ? (
                        <Badge variant="outline">safe to retry</Badge>
                      ) : (
                        <Badge variant="secondary">not retryable</Badge>
                      )}
                    </span>
                    <span className="tabular-nums">{g.count}</span>
                  </summary>
                  <ul className="mt-1 text-xs">
                    {g.reasons?.map((reason) => (
                      <li key={`${reason.code}-${reason.message}`}>
                        <span className="font-mono">{reason.code}</span> {reason.message} ({reason.count})
                      </li>
                    ))}
                  </ul>
                </details>
              ))}
              {r.failures.skipped.map((s) => (
                <p key={s.reason} className="flex justify-between text-xs">
                  <span>Skipped · {s.reason}</span>
                  <span className="tabular-nums">{s.count}</span>
                </p>
              ))}
            </CardContent>
          </Card>
        </div>
      ) : null}

      {campaign.snapshot_built_at ? (
        <Card>
          <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
            <CardTitle className="text-sm">Recipients</CardTitle>
            <span className="flex items-center gap-2">
              <NativeSelect
                aria-label="Recipient status"
                className="h-7 text-xs"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                <option value="">All statuses</option>
                {RECIPIENT_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </NativeSelect>
              <ExportButton
                kind="campaign_recipients"
                params={{ campaign_id: campaign.id }}
                label="Export recipients"
              />
            </span>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted-foreground text-left">
                <tr>
                  <th className="py-1 pr-2">Contact</th>
                  <th className="pr-2">Phone</th>
                  <th className="pr-2">Status</th>
                  <th className="pr-2">Attempt</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td className="py-1 pr-2">{row.contact_name || "—"}</td>
                    <td className="pr-2 font-mono">{row.phone_number || "BSUID"}</td>
                    <td className="pr-2">{row.status}</td>
                    <td className="pr-2 tabular-nums">{row.attempts}</td>
                    <td>{row.error_message || row.skip_reason || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {nextRecipients ? (
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() =>
                  void campaignService
                    .recipientsPage(nextRecipients)
                    .then((page) =>
                      setMore({
                        key: recipientKey,
                        rows: [...(more?.key === recipientKey ? more.rows : []), ...page.results],
                        next: page.next,
                      }),
                    )
                    .catch((cause) => setError(toApiError(cause)))
                }
              >
                Load more
              </Button>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Timeline</CardTitle>
        </CardHeader>
        <CardContent>
          <ol className="flex flex-col gap-1 text-xs">
            {events.data?.map((e) => (
              <li key={e.id} className="flex flex-wrap gap-2">
                <span className="text-muted-foreground">{new Date(e.created_at).toLocaleString()}</span>
                <span className="font-medium">{e.event}</span>
                {e.actor_email ? <span>by {e.actor_email}</span> : null}
                {Object.keys(e.data).length ? (
                  <span className="text-muted-foreground font-mono">{JSON.stringify(e.data)}</span>
                ) : null}
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>
    </div>
  );
}
