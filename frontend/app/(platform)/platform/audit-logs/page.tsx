import { Suspense } from "react";

import { AuditLogView } from "@/features/platform/audit-log-view";

export default function PlatformAuditLogsPage() {
  return (
    <Suspense>
      <AuditLogView />
    </Suspense>
  );
}
