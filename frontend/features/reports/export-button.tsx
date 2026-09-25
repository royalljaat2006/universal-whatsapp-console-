"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import type { ApiError } from "@/lib/api/errors";
import { toApiError } from "@/lib/format";
import { exportService } from "@/services/exports";
import { useSession } from "@/stores/session-context";
import type { ExportJob, ExportKind } from "@/types/api";

/**
 * Requests an asynchronous CSV export and downloads it when the worker has
 * finished. The status shown is the job's real backend state.
 */
export function ExportButton({
  kind,
  params,
  label = "Export CSV",
}: {
  kind: ExportKind;
  params: Record<string, string>;
  label?: string;
}) {
  const { hasPermission } = useSession();
  const [job, setJob] = useState<ExportJob | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    if (!job || job.status === "succeeded" || job.status === "failed") return;
    const timer = setTimeout(() => {
      exportService
        .get(job.id)
        .then((next) => {
          setJob(next);
          if (next.status === "succeeded") void exportService.download(next);
        })
        .catch((cause) => setError(toApiError(cause)));
    }, 1500);
    return () => clearTimeout(timer);
  }, [job]);

  if (!hasPermission("reports.export")) return null;

  async function start() {
    setError(null);
    try {
      const created = await exportService.create(kind, params);
      setJob(created);
      if (created.status === "succeeded") void exportService.download(created);
    } catch (cause) {
      setError(toApiError(cause));
    }
  }

  const busy = job !== null && (job.status === "pending" || job.status === "running");
  return (
    <span className="flex items-center gap-2 text-xs">
      <Button variant="outline" size="sm" onClick={() => void start()} disabled={busy}>
        {busy ? "Preparing…" : label}
      </Button>
      {job?.status === "succeeded" ? (
        <button type="button" className="underline" onClick={() => void exportService.download(job)}>
          Download again ({job.row_count ?? 0} rows)
        </button>
      ) : null}
      {job?.status === "failed" ? <span className="text-destructive">Export failed: {job.error}</span> : null}
      {error ? <span className="text-destructive">{error.message}</span> : null}
    </span>
  );
}
