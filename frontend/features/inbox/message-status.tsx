import { AlertCircleIcon, CheckCheckIcon, CheckIcon, ClockIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import type { Message } from "@/types/api";

const LABELS: Record<Message["status"], string> = {
  queued: "Queued",
  sending: "Sending",
  accepted: "Accepted by WhatsApp",
  sent: "Sent",
  delivered: "Delivered",
  read: "Read",
  failed: "Failed",
  received: "Received",
};

/** Status exactly as stored by the backend; no optimistic ticks. */
export function MessageStatus({ message }: { message: Message }) {
  if (message.direction === "inbound") return null;
  const s = message.status;
  const Icon =
    s === "read" || s === "delivered"
      ? CheckCheckIcon
      : s === "sent" || s === "accepted"
        ? CheckIcon
        : s === "failed"
          ? AlertCircleIcon
          : ClockIcon;
  return (
    <span
      title={message.error_message || LABELS[s]}
      className={cn(
        "inline-flex items-center gap-1",
        s === "read" ? "text-info" : s === "failed" ? "text-destructive" : "text-muted-foreground",
      )}
    >
      <Icon aria-hidden className="size-3.5" />
      <span>
        {LABELS[s]}
        {s === "failed" && message.error_code ? ` (${message.error_code})` : ""}
      </span>
    </span>
  );
}
