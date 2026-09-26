import { api } from "@/lib/api/client";
import type { PermissionCatalogueEntry, Role } from "@/types/api";

export interface WriteRoleInput {
  name?: string;
  description?: string;
  permissions?: string[];
}

export const roleService = {
  list: () => api.get<Role[]>("/api/v1/organizations/current/roles/", { noBusiness: true }),
  permissions: () =>
    api.get<PermissionCatalogueEntry[]>("/api/v1/organizations/current/permissions/", {
      noBusiness: true,
    }),
  create: (input: WriteRoleInput) =>
    api.post<Role>("/api/v1/organizations/current/roles/", input, { noBusiness: true }),
  update: (id: string, changes: WriteRoleInput) =>
    api.patch<Role>(`/api/v1/organizations/current/roles/${id}/`, changes, { noBusiness: true }),
  remove: (id: string) =>
    api.delete<void>(`/api/v1/organizations/current/roles/${id}/`, { noBusiness: true }),
};
