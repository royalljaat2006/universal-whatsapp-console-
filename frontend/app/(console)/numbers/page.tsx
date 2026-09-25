"use client";

import { NumbersView } from "@/features/numbers/numbers-view";
import { useSession } from "@/stores/session-context";

export default function NumbersPage() {
  const { business } = useSession();
  // Remount per business so no state from another business survives a switch.
  return <NumbersView key={business?.id ?? "none"} />;
}
