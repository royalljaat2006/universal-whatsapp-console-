import { api } from "@/lib/api/client";
import type {
  CursorPage,
  OperationsSummary,
  PlatformAuditEntry,
  PlatformOrganization,
} from "@/types/api";

/** Cross-tenant, is_staff-only endpoints (apps.platform on the backend). None
 * of these take X-Organization-ID — they are outside the tenant boundary by
 * design (see docs/productization/tenant-isolation.md). */
export const platformService = {
  organizations: {
    list: () =>
      api.get<PlatformOrganization[]>("/api/v1/platform/organizations/", { unscoped: true }),
    get: (id: string) =>
      api.get<PlatformOrganization>(`/api/v1/platform/organizations/${id}/`, { unscoped: true }),
    setStatus: (id: string, status: "active" | "suspended") =>
      api.patch<PlatformOrganization>(
        `/api/v1/platform/organizations/${id}/`,
        { status },
        { unscoped: true },
      ),
  },
  auditLogs: (query: { organization_id?: string; action?: string }) =>
    api.get<CursorPage<PlatformAuditEntry>>("/api/v1/platform/audit-logs/", {
      unscoped: true,
      query,
    }),
  operations: () => api.get<OperationsSummary>("/api/v1/platform/operations/", { unscoped: true }),
};
