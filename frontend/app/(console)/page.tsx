"use client";

import { OverviewView } from "@/features/overview/overview-view";
import { useSession } from "@/stores/session-context";

export default function OverviewPage() {
  const { business } = useSession();
  return <OverviewView key={business?.id ?? "none"} />;
}
