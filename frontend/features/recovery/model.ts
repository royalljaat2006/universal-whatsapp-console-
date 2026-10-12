import type { Tone } from "@/components/ui/status-badge";
import type { FailedMessage, RecoveryKind, RetryAttempt } from "@/types/api";

export const KIND_LABEL: Record<RecoveryKind, string> = {
  retryable: "Safe to retry",
  not_retryable: "Fix first",
  unknown: "Outcome unclear",
  external: "Fix in Meta",
};

export const KIND_TONE: Record<RecoveryKind, Tone> = {
  retryable: "info",
  not_retryable: "danger",
  unknown: "warning",
  external: "warning",
};

const PENDING = new Set(["queued", "sending", "accepted", "sent"]);

export function isPending(attempt: RetryAttempt | null): boolean {
  return !!attempt && PENDING.has(attempt.status);
}

export function hasPending(rows: FailedMessage[]): boolean {
  return rows.some((row) => isPending(row.recovery.retried_as));
}

export interface AttemptView {
  label: string;
  tone: Tone;
  text: string;
}

/**
 * Plain-language state of a retry, from the stored status only. "Accepted" and
 * "sent" are never described as delivered; only the provider's own delivered /
 * read report is.
 */
export function describeAttempt(attempt: RetryAttempt): AttemptView {
  switch (attempt.status) {
    case "queued":
      return { label: "Waiting to be sent", tone: "info", text: "The retry is queued. Nothing has reached WhatsApp yet." };
    case "sending":
      return { label: "Sending", tone: "info", text: "The retry is being handed to WhatsApp." };
    case "accepted":
      return {
        label: "Accepted, not delivered yet",
        tone: "info",
        text: "WhatsApp accepted the retry. Delivery has not been confirmed yet.",
      };
    case "sent":
      return {
        label: "Sent, not delivered yet",
        tone: "info",
        text: "WhatsApp sent the retry. Waiting for the delivery report.",
      };
    case "delivered":
      return { label: "Delivered", tone: "success", text: "WhatsApp reports the retry was delivered." };
    case "read":
      return { label: "Delivered and read", tone: "success", text: "WhatsApp reports the retry was read." };
    case "failed":
      return {
        label: "Retry failed",
        tone: "danger",
        text: attempt.failure?.safe_error
          ? `The retry failed too: ${attempt.failure.safe_error}`
          : "The retry failed too.",
      };
    default:
      return { label: attempt.status_label || attempt.status, tone: "neutral", text: "" };
  }
}

export function recipientLabel(row: FailedMessage): string {
  return row.recipient.name || row.recipient.phone_number || "Unknown recipient";
}

export function costLine(cost: { status: string; amount?: string; currency?: string; note: string }): string {
  if (cost.status === "estimate" && cost.amount) {
    return `Estimated ${cost.currency ?? ""} ${cost.amount}. ${cost.note}`.replace(/\s+/g, " ").trim();
  }
  if (cost.status === "free") return `No charge. ${cost.note}`;
  return `Cost unknown. ${cost.note}`;
}
