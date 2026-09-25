import { api, apiBlob } from "@/lib/api/client";
import type { CursorPage, ExportJob, ExportKind } from "@/types/api";

export const exportService = {
  create: (kind: ExportKind, params: Record<string, string>) =>
    api.post<ExportJob>("/api/v1/exports/", { kind, params }),
  get: (id: string) => api.get<ExportJob>(`/api/v1/exports/${id}/`),
  list: () => api.get<CursorPage<ExportJob>>("/api/v1/exports/", { query: { page_size: 20 } }),
  /** Downloads through the API client (tenant headers), then saves the file. */
  download: async (job: ExportJob) => {
    if (!job.download_url) return;
    const blob = await apiBlob(job.download_url);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = job.filename || "export.csv";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
};
