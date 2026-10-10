"use client";

import { CommandHome } from "@/features/command/command-home";
import { useSession } from "@/stores/session-context";

export default function OverviewPage() {
  const { business } = useSession();
  // Remount per business so no command result from another business survives a switch.
  return <CommandHome key={business?.id ?? "none"} />;
}
