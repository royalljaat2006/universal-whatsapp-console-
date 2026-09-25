"use client";

import { TemplatesView } from "@/features/templates/templates-view";
import { useSession } from "@/stores/session-context";

export default function TemplatesPage() {
  const { business } = useSession();
  // Remount per business so no state from another business survives a switch.
  return <TemplatesView key={business?.id ?? "none"} />;
}
