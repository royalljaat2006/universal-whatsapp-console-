"use client";

import { InboxView } from "@/features/inbox/inbox-view";
import { useSession } from "@/stores/session-context";

export default function InboxPage() {
  const { business } = useSession();
  // Remount per business so no state from another business can leak into view.
  return <InboxView key={business?.id ?? "none"} />;
}
