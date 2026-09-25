"use client";

import { SettingsView } from "@/features/settings/settings-view";
import { useSession } from "@/stores/session-context";

export default function SettingsPage() {
  const { business } = useSession();
  // Remount per business so no state from another business survives a switch.
  return <SettingsView key={business?.id ?? "none"} />;
}
