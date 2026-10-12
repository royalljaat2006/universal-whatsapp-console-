import type { Tone } from "@/components/ui/status-badge";
import type { SetupOverall, SetupState, SetupStep } from "@/types/api";

export const STATE_LABEL: Record<SetupState, string> = {
  verified: "Done",
  pending: "Not yet",
  failed: "Needs fixing",
  unsupported: "Not available",
  unknown: "Can't tell yet",
};

export const STATE_TONE: Record<SetupState, Tone> = {
  verified: "success",
  pending: "info",
  failed: "danger",
  unsupported: "neutral",
  unknown: "neutral",
};

export const OVERALL_LABEL: Record<SetupOverall, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  blocked: "Needs attention",
  ready: "Ready",
};

export const OVERALL_TONE: Record<SetupOverall, Tone> = {
  not_started: "neutral",
  in_progress: "info",
  blocked: "danger",
  ready: "success",
};

export function fixWhere(step: SetupStep): string {
  if (step.fix_in === "meta") return "Fix in Meta's WhatsApp Manager";
  if (step.fix_in === "administrator") return "Needs your administrator";
  return "";
}

export function progressOf(steps: SetupStep[]): { done: number; total: number } {
  return { done: steps.filter((s) => s.state === "verified").length, total: steps.length };
}

/** The first step that is not done, for "continue where you left off". */
export function currentStep(steps: SetupStep[], nextId: string): SetupStep | undefined {
  return steps.find((s) => s.id === nextId) ?? steps.find((s) => s.state !== "verified");
}

export interface MessageProgress {
  label: string;
  tone: Tone;
  text: string;
  final: boolean;
}

/**
 * What the test message is, from its stored status only. "Accepted" and "sent"
 * are never described as delivered; only WhatsApp's own delivered / read report is.
 */
export function describeTestStatus(status: string, error = ""): MessageProgress {
  switch (status) {
    case "queued":
      return { label: "Waiting to be sent", tone: "info", text: "Queued. Nothing has reached WhatsApp yet.", final: false };
    case "sending":
      return { label: "Sending", tone: "info", text: "Being handed to WhatsApp.", final: false };
    case "accepted":
      return {
        label: "Accepted, not delivered yet",
        tone: "info",
        text: "WhatsApp accepted the message. Delivery has not been confirmed yet.",
        final: false,
      };
    case "sent":
      return {
        label: "Sent, not delivered yet",
        tone: "info",
        text: "WhatsApp sent the message. Waiting for the delivery report.",
        final: false,
      };
    case "delivered":
      return { label: "Delivered", tone: "success", text: "WhatsApp reports the message was delivered.", final: true };
    case "read":
      return { label: "Delivered and read", tone: "success", text: "WhatsApp reports the message was read.", final: true };
    case "failed":
      return {
        label: "Failed",
        tone: "danger",
        text: error ? `WhatsApp could not deliver it: ${error}` : "WhatsApp could not deliver it.",
        final: true,
      };
    default:
      return { label: status, tone: "neutral", text: "", final: false };
  }
}

const EVIDENCE_LABELS: Record<string, string> = {
  accounts: "Accounts",
  connected: "Connected",
  last_verified_at: "Last checked with Meta",
  numbers: "Numbers",
  statuses: "Meta status",
  subscribed: "Subscribed",
  last_checked_at: "Last checked with Meta",
  events_received: "Updates received",
  last_received_at: "Last update received",
  events_failed: "Updates that failed",
  approved: "Approved templates",
  sent: "Test messages sent",
  delivered: "Delivered",
  failed: "Failed",
  last_status: "Last status",
  last_at: "Last sent",
  callback_url: "Callback address",
  verify_token_configured: "Verify token set",
  app_secret_configured: "App secret set",
  public_https: "Public https address",
};

/** Plain rows for the Advanced "evidence" view. Empty values are skipped, never invented. */
export function evidenceRows(evidence: Record<string, unknown>): { label: string; value: string }[] {
  return Object.entries(evidence)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([key, value]) => ({
      label: EVIDENCE_LABELS[key] ?? key.replace(/_/g, " "),
      value:
        typeof value === "boolean"
          ? value
            ? "Yes"
            : "No"
          : Array.isArray(value)
            ? value.join(", ")
            : /_at$/.test(key) && typeof value === "string"
              ? new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })
              : String(value),
    }));
}
