"use client";

import { CampaignsView } from "@/features/campaigns/campaigns-view";
import { useSession } from "@/stores/session-context";

export default function CampaignsPage() {
  const { business } = useSession();
  // Remount per business so no state from another business survives a switch.
  return <CampaignsView key={business?.id ?? "none"} />;
}
