import { cn } from "@/lib/utils";

export type Tone = "success" | "warning" | "danger" | "info" | "neutral" | "brand";

const TONES: Record<Tone, string> = {
  success: "bg-success-soft text-success",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-destructive",
  info: "bg-info-soft text-info",
  neutral: "bg-muted text-muted-foreground",
  brand: "bg-brand-soft text-primary",
};

const DOTS: Record<Tone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-destructive",
  info: "bg-info",
  neutral: "bg-muted-foreground/60",
  brand: "bg-primary",
};

/**
 * Compact status pill. State is always conveyed by the text label (and a
 * dot), never by colour alone.
 */
export function StatusBadge({
  tone = "neutral",
  children,
  className,
  dot = true,
  title,
}: {
  tone?: Tone;
  children: React.ReactNode;
  className?: string;
  dot?: boolean;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex h-5 shrink-0 items-center gap-1.5 rounded-full px-2 text-[11px] font-medium whitespace-nowrap uppercase tracking-wide",
        TONES[tone],
        className,
      )}
    >
      {dot ? <span aria-hidden className={cn("size-1.5 rounded-full", DOTS[tone])} /> : null}
      {children}
    </span>
  );
}

/** Maps common backend status vocabularies to a tone. */
export function toneFor(status: string | null | undefined): Tone {
  const s = (status ?? "").toLowerCase();
  if (["connected", "approved", "succeeded", "completed", "delivered", "read", "active", "green", "priced", "opted_in", "open"].includes(s))
    return "success";
  if (["pending", "syncing", "scheduled", "queued", "sending", "paused", "yellow", "partial", "pending_rate", "in_appeal"].includes(s))
    return "warning";
  if (["failed", "error", "disconnected", "rejected", "disabled", "flagged", "restricted", "red", "opted_out", "cancelled"].includes(s))
    return "danger";
  if (["running", "accepted", "sent", "info"].includes(s)) return "info";
  return "neutral";
}
