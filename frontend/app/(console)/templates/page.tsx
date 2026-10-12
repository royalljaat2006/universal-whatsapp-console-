"use client";

import { Suspense } from "react";

import { TemplatesView } from "@/features/templates/templates-view";
import { useSession } from "@/stores/session-context";

export default function TemplatesPage() {
  const { business } = useSession();
  // Remount per business so no state from another business survives a switch.
  // Suspense: the view reads ?new=1 (open the studio) via useSearchParams.
  return (
    <Suspense>
      <TemplatesView key={business?.id ?? "none"} />
    </Suspense>
  );
}
