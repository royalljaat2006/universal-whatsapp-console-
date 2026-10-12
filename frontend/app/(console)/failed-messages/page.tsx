"use client";

import { FailedMessagesView } from "@/features/recovery/failed-messages-view";
import { useSession } from "@/stores/session-context";

export default function FailedMessagesPage() {
  const { business } = useSession();
  // Remount per business so no state from another business survives a switch.
  return <FailedMessagesView key={business?.id ?? "none"} />;
}
