import { StatusBadge, type Tone } from "@/components/ui/status-badge";
import type { CampaignProgress, CampaignStatus } from "@/types/api";

export const STATUS_LABEL: Record<CampaignStatus, string> = {
  draft: "Draft",
  scheduled: "Scheduled",
  queued: "Queued",
  running: "Running",
  paused: "Paused",
  completed: "Completed",
  cancelled: "Cancelled",
  failed: "Failed",
};

const STATUS_TONE: Record<CampaignStatus, Tone> = {
  draft: "neutral",
  scheduled: "info",
  queued: "warning",
  running: "brand",
  paused: "warning",
  completed: "success",
  cancelled: "neutral",
  failed: "danger",
};

export function CampaignStatusBadge({ status }: { status: CampaignStatus }) {
  return <StatusBadge tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</StatusBadge>;
}

/** What the backend allows per status (the server enforces the same rules). */
export const CONTROLS: Record<CampaignStatus, ("pause" | "resume" | "cancel" | "retry")[]> = {
  draft: [],
  scheduled: ["cancel"],
  queued: ["pause", "cancel"],
  running: ["pause", "cancel", "retry"],
  paused: ["resume", "cancel", "retry"],
  completed: ["retry"],
  cancelled: [],
  failed: ["resume", "cancel", "retry"],
};

/**
 * Progress exactly as persisted: processed/total recipients from the
 * backend counters. No animation, no estimate.
 */
export function CampaignProgressBar({
  progress,
  compact = false,
}: {
  progress: CampaignProgress;
  compact?: boolean;
}) {
  const { total, processed } = progress;
  if (!total) {
    return (
      <span className="text-muted-foreground text-xs">
        No recipients yet (audience is snapshotted at launch)
      </span>
    );
  }
  const pct = (n: number) => `${(n / total) * 100}%`;
  const done = progress.accepted_or_later;
  return (
    <div className="flex flex-col gap-1" aria-label={`${processed} of ${total} recipients processed`}>
      <div className="bg-muted flex h-2 overflow-hidden rounded">
        <span
          className="bg-primary h-full"
          style={{ width: pct(done) }}
          title={`${done} accepted or later`}
        />
        <span
          className="bg-destructive h-full"
          style={{ width: pct(progress.failed) }}
          title={`${progress.failed} failed`}
        />
        <span
          className="bg-muted-foreground/40 h-full"
          style={{ width: pct(progress.skipped + progress.cancelled) }}
          title={`${progress.skipped + progress.cancelled} skipped/cancelled`}
        />
      </div>
      <span className="text-muted-foreground text-[11px] tabular-nums">
        {processed.toLocaleString()} / {total.toLocaleString()} processed
        {compact
          ? ""
          : ` · ${progress.in_flight} in flight · ${progress.pending} pending · ${progress.delivered_or_later} delivered · ${progress.read} read · ${progress.failed} failed · ${progress.skipped} skipped`}
      </span>
    </div>
  );
}
