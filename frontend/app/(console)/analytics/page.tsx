"use client";

import { AnalyticsView } from "@/features/analytics/analytics-view";
import { useSession } from "@/stores/session-context";

export default function AnalyticsPage() {
  const { business } = useSession();
  // Remount per business so no state from another business survives a switch.
  return <AnalyticsView key={business?.id ?? "none"} />;
}
