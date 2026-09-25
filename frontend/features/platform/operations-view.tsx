"use client";

import { PageHeader, Kpi, Panel } from "@/components/ui/page";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { useApiQuery } from "@/hooks/use-api-query";
import { platformService } from "@/services/platform";

const CHECK_LABELS: Record<string, string> = {
  database: "Database",
  redis_cache: "Redis cache",
  celery_workers: "Celery workers",
};

export function OperationsView() {
  const ops = useApiQuery(() => platformService.operations(), []);

  if (ops.error) return <ErrorState error={ops.error} onRetry={ops.reload} />;
  if (!ops.data) return <PageSkeleton />;

  const { organizations, webhooks_last_24h, infrastructure } = ops.data;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Operations" description="A live snapshot — not a historical dashboard." />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label="Organizations" value={organizations.total} />
        <Kpi label="Active" value={organizations.active} />
        <Kpi label="Suspended" value={organizations.suspended} />
        <Kpi
          label="Webhooks dead (24h)"
          value={webhooks_last_24h.dead}
          hint={webhooks_last_24h.dead > 0 ? "Needs attention" : "All clear"}
        />
      </div>

      <Panel title="Webhook processing (last 24h)" bodyClassName="grid grid-cols-2 gap-3 p-4 sm:grid-cols-4">
        <Kpi label="Processed" value={webhooks_last_24h.processed} />
        <Kpi label="Partial" value={webhooks_last_24h.partial} />
        <Kpi label="Failed (retrying)" value={webhooks_last_24h.failed} />
        <Kpi label="Dead" value={webhooks_last_24h.dead} />
      </Panel>

      <Panel title="Infrastructure" bodyClassName="flex flex-col divide-y">
        {Object.entries(infrastructure).map(([name, check]) => (
          <div key={name} className="flex items-center justify-between px-4 py-3">
            <div>
              <p className="text-sm font-medium">{CHECK_LABELS[name] ?? name}</p>
              {check.detail ? <p className="text-caption">{check.detail}</p> : null}
            </div>
            <div className="flex items-center gap-3">
              <span className="text-caption tabular-nums">{check.duration_ms}ms</span>
              <StatusBadge tone={check.ok ? "success" : check.critical ? "danger" : "warning"}>
                {check.ok ? "ok" : "unavailable"}
              </StatusBadge>
            </div>
          </div>
        ))}
      </Panel>
    </div>
  );
}
