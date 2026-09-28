"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { PageHeader, Panel } from "@/components/ui/page";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge, type Tone } from "@/components/ui/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApiQuery } from "@/hooks/use-api-query";
import { toApiError } from "@/lib/format";
import { platformService } from "@/services/platform";
import type { PlatformWebhookEvent } from "@/types/api";

const STATUS_TONE: Record<PlatformWebhookEvent["status"], Tone> = {
  pending: "neutral",
  processing: "info",
  processed: "success",
  partial: "warning",
  failed: "warning",
  dead: "danger",
};

function FailedJobsPanel() {
  const jobs = useApiQuery(() => platformService.failedJobs(), []);
  if (jobs.error) return <ErrorState error={jobs.error} onRetry={jobs.reload} inline className="m-4" />;
  if (!jobs.data) return <PageSkeleton />;

  const { campaigns, contact_imports, exports } = jobs.data;
  const total = campaigns.length + contact_imports.length + exports.length;

  if (total === 0) {
    return <p className="text-supporting p-6 text-center">No failed campaigns, imports or exports recently.</p>;
  }

  return (
    <div className="flex flex-col divide-y">
      {campaigns.map((c) => (
        <div key={c.id} className="flex flex-col gap-0.5 px-4 py-3">
          <span className="flex items-center justify-between gap-2">
            <span className="text-sm font-medium">Campaign: {c.name}</span>
            <span className="text-caption">{new Date(c.updated_at).toLocaleString()}</span>
          </span>
          <span className="text-caption">
            {c.failure_code} — {c.failure_reason}
          </span>
          <span className="text-caption font-mono">
            org {c.organization_id.slice(0, 8)}… · business {c.business_id.slice(0, 8)}…
          </span>
        </div>
      ))}
      {contact_imports.map((j) => (
        <div key={j.id} className="flex flex-col gap-0.5 px-4 py-3">
          <span className="flex items-center justify-between gap-2">
            <span className="text-sm font-medium">Contact import: {j.filename || "(untitled)"}</span>
            <span className="text-caption">{new Date(j.updated_at).toLocaleString()}</span>
          </span>
          {j.error ? <span className="text-caption">{j.error}</span> : null}
          <span className="text-caption font-mono">
            org {j.organization_id.slice(0, 8)}… · business {j.business_id.slice(0, 8)}…
          </span>
        </div>
      ))}
      {exports.map((e) => (
        <div key={e.id} className="flex flex-col gap-0.5 px-4 py-3">
          <span className="flex items-center justify-between gap-2">
            <span className="text-sm font-medium">Export: {e.kind}</span>
            <span className="text-caption">{new Date(e.updated_at).toLocaleString()}</span>
          </span>
          {e.error ? <span className="text-caption">{e.error}</span> : null}
          <span className="text-caption font-mono">
            org {e.organization_id.slice(0, 8)}… · business {e.business_id.slice(0, 8)}…
          </span>
        </div>
      ))}
    </div>
  );
}

function WebhookEventsPanel() {
  const [status, setStatus] = useState("");
  const events = useApiQuery(() => platformService.webhookEvents({ status: status || undefined }), [status]);
  const [replaying, setReplaying] = useState<string | null>(null);
  const [replayError, setReplayError] = useState<string | null>(null);

  async function replay(id: string) {
    setReplaying(id);
    setReplayError(null);
    try {
      await platformService.replayWebhookEvent(id);
      events.reload();
    } catch (cause) {
      setReplayError(toApiError(cause).message);
    } finally {
      setReplaying(null);
    }
  }

  return (
    <Panel
      title="Webhook deliveries"
      description="Delivery and processing metadata only — message content is never shown here."
      actions={
        <NativeSelect value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
          <option value="">All statuses</option>
          <option value="failed">Failed (retrying)</option>
          <option value="dead">Dead</option>
          <option value="partial">Partial</option>
          <option value="processed">Processed</option>
          <option value="pending">Pending</option>
        </NativeSelect>
      }
      bodyClassName="overflow-x-auto"
    >
      {events.error ? <ErrorState error={events.error} onRetry={events.reload} inline className="m-4" /> : null}
      {replayError ? <p className="text-destructive px-4 pt-3 text-xs">{replayError}</p> : null}
      {!events.data ? (
        <div className="p-4">
          <PageSkeleton />
        </div>
      ) : events.data.results.length === 0 ? (
        <p className="text-supporting p-6 text-center">No webhook events for this filter.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Received</TableHead>
              <TableHead>Provider</TableHead>
              <TableHead>Fields</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Attempts</TableHead>
              <TableHead className="hidden md:table-cell">Last error</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {events.data.results.map((e) => (
              <TableRow key={e.id}>
                <TableCell className="text-caption whitespace-nowrap">
                  {new Date(e.received_at).toLocaleString()}
                </TableCell>
                <TableCell>{e.provider}</TableCell>
                <TableCell className="text-caption font-mono">{e.fields.join(", ") || "—"}</TableCell>
                <TableCell>
                  <StatusBadge tone={STATUS_TONE[e.status]}>{e.status}</StatusBadge>
                </TableCell>
                <TableCell>{e.attempts}</TableCell>
                <TableCell className="text-caption hidden max-w-xs truncate md:table-cell">
                  {e.last_error || "—"}
                </TableCell>
                <TableCell>
                  {e.status === "failed" || e.status === "dead" ? (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={replaying === e.id}
                      onClick={() => void replay(e.id)}
                    >
                      {replaying === e.id ? "Replaying…" : "Replay"}
                    </Button>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Panel>
  );
}

export function FailedJobsView() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Failed jobs"
        description="Cross-tenant triage for failed background work — the full history stays in each app's own record."
      />
      <Panel title="Campaigns, imports and exports">
        <FailedJobsPanel />
      </Panel>
      <WebhookEventsPanel />
    </div>
  );
}
