import { ClockIcon, LockIcon } from "lucide-react";

import { timeLeft } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { SendPolicy } from "@/types/api";

const REASONS: Record<string, string> = {
  SERVICE_WINDOW_OPEN: "Customer service window open — free-form replies allowed.",
  TEMPLATE_REQUIRED_WINDOW_CLOSED:
    "The 24-hour customer service window has closed. Only an approved template can be sent.",
  TEMPLATE_REQUIRED_NO_CUSTOMER_MESSAGE:
    "The customer has not messaged this number yet. Only an approved template can be sent.",
};

/** Mirrors the backend send policy (apps/messaging/policy.py); the server enforces it. */
export function PolicyBanner({ policy }: { policy: SendPolicy }) {
  const left = policy.window_open ? timeLeft(policy.window_expires_at) : null;
  const Icon = policy.free_form_allowed ? ClockIcon : LockIcon;
  return (
    <p
      role="status"
      className={cn(
        "flex items-center gap-1.5 text-xs",
        policy.free_form_allowed
          ? "text-muted-foreground px-4 pt-2"
          : "bg-warning-soft text-warning mx-3 mt-2.5 rounded-md px-3 py-2 font-medium",
      )}
    >
      <Icon aria-hidden className="size-3.5 shrink-0" />
      <span>
        {REASONS[policy.reason] ?? policy.reason}
        {left ? ` (${left})` : ""}
      </span>
    </p>
  );
}
