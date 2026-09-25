import { CircleDashedIcon, PlugZapIcon, SettingsIcon } from "lucide-react";

import { StatusBadge } from "@/components/ui/status-badge";

export type CapabilityState = "NOT IMPLEMENTED" | "NOT CONNECTED" | "CONFIGURATION REQUIRED";

const ICONS = {
  "NOT IMPLEMENTED": CircleDashedIcon,
  "NOT CONNECTED": PlugZapIcon,
  "CONFIGURATION REQUIRED": SettingsIcon,
};

/**
 * Honest placeholder for anything the backend cannot do yet (or that needs
 * configuration). The platform never renders mock data to fill the gap.
 */
export function CapabilityStatus({
  title,
  state,
  description,
  action,
}: {
  title: string;
  state: CapabilityState;
  description: string;
  action?: React.ReactNode;
}) {
  const Icon = ICONS[state];
  return (
    <section
      role="status"
      aria-label={`${title}: ${state}`}
      className="bg-surface flex flex-col items-center gap-2 rounded-lg border px-6 py-12 text-center shadow-xs"
    >
      <span className="bg-muted text-muted-foreground mb-1 flex size-10 items-center justify-center rounded-full">
        <Icon aria-hidden className="size-5" />
      </span>
      <div className="flex items-center gap-2">
        <h2 className="text-section-title">{title}</h2>
        <StatusBadge tone={state === "NOT IMPLEMENTED" ? "neutral" : "warning"}>{state}</StatusBadge>
      </div>
      <p className="text-supporting max-w-md">{description}</p>
      <p className="text-caption">No data is shown until the backend capability exists.</p>
      {action ? <div className="mt-2">{action}</div> : null}
    </section>
  );
}
