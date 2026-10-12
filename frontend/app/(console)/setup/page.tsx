"use client";

import { SetupView } from "@/features/setup/setup-view";
import { useSession } from "@/stores/session-context";

export default function SetupPage() {
  const { business } = useSession();
  // Remount per business so no state from another business survives a switch.
  return <SetupView key={business?.id ?? "none"} />;
}
