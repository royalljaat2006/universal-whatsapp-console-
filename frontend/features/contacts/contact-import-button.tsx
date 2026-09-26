"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Panel } from "@/components/ui/page";
import { LoadingButton } from "@/components/ui/spinner";
import { ErrorState } from "@/components/ui/states";
import type { ApiError } from "@/lib/api/errors";
import { toApiError } from "@/lib/format";
import { contactImportService } from "@/services/contacts";
import { useSession } from "@/stores/session-context";
import type { ContactImportJob } from "@/types/api";

const TERMINAL = new Set<ContactImportJob["status"]>(["succeeded", "succeeded_with_errors", "failed"]);

/**
 * Toggles an inline "Import contacts" panel: pick a CSV, optionally tag every
 * imported contact, upload, then poll the job (same pattern as ExportButton)
 * until it finishes and show the real created/updated/failed counts.
 */
export function ContactImportButton({ onImported }: { onImported: () => void }) {
  const { hasPermission } = useSession();
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [tagName, setTagName] = useState("");
  const [job, setJob] = useState<ContactImportJob | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [uploading, setUploading] = useState(false);

  const running = job !== null && !TERMINAL.has(job.status);

  useEffect(() => {
    if (!job || TERMINAL.has(job.status)) return;
    const timer = setTimeout(() => {
      contactImportService
        .get(job.id)
        .then((next) => {
          setJob(next);
          if (TERMINAL.has(next.status)) onImported();
        })
        .catch((cause) => setError(toApiError(cause)));
    }, 1500);
    return () => clearTimeout(timer);
  }, [job, onImported]);

  if (!hasPermission("contacts.manage")) return null;

  async function upload() {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const created = await contactImportService.upload(file, tagName.trim() || undefined);
      setJob(created);
    } catch (cause) {
      setError(toApiError(cause));
    } finally {
      setUploading(false);
    }
  }

  if (!open) {
    return (
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        Import CSV
      </Button>
    );
  }

  return (
    <Panel title="Import contacts" bodyClassName="flex flex-col gap-3 p-4" className="w-full">
      <p className="text-caption">
        A CSV with a <code className="font-mono">phone_number</code> column (required). Optional
        columns: <code className="font-mono">display_name</code>, <code className="font-mono">tags</code>{" "}
        (comma-separated), <code className="font-mono">opt_in_status</code>. Any other column is stored
        as a contact attribute.
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="import-file">CSV file</Label>
          <input
            id="import-file"
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="text-sm"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="import-tag">Tag everyone imported (optional)</Label>
          <Input
            id="import-tag"
            className="w-48"
            value={tagName}
            onChange={(e) => setTagName(e.target.value)}
          />
        </div>
        <LoadingButton loading={uploading || running} disabled={!file || uploading || running} onClick={() => void upload()}>
          {running ? "Processing…" : "Upload"}
        </LoadingButton>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setOpen(false);
            setJob(null);
            setFile(null);
            setError(null);
          }}
        >
          Close
        </Button>
      </div>
      {error ? <ErrorState error={error} inline /> : null}
      {job && TERMINAL.has(job.status) ? (
        <div className="text-sm">
          {job.status === "failed" ? (
            <p className="text-destructive">Import failed: {job.error}</p>
          ) : (
            <p>
              {job.created_count} created, {job.updated_count} updated
              {job.failed_count ? `, ${job.failed_count} failed` : ""} of {job.total_rows} row(s).
            </p>
          )}
          {job.row_errors.length ? (
            <ul className="text-caption mt-1 max-h-32 list-disc overflow-y-auto pl-4">
              {job.row_errors.slice(0, 10).map((e) => (
                <li key={e.row}>
                  Row {e.row}: {e.error}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </Panel>
  );
}
