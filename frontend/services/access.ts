import { api } from "@/lib/api/client";
import type { AccessRow, AccountEvent, CursorPage } from "@/types/api";

export const accessService = {
  list: (businessId: string) =>
    api.get<{ business: { id: string; name: string }; members: AccessRow[] }>(
      `/api/v1/businesses/${businessId}/access/`,
      { noBusiness: true },
    ),
  grant: (businessId: string, membershipId: string) =>
    api.post<void>(
      `/api/v1/businesses/${businessId}/access/`,
      { membership_id: membershipId },
      { noBusiness: true },
    ),
  revoke: (businessId: string, membershipId: string) =>
    api.delete<void>(`/api/v1/businesses/${businessId}/access/${membershipId}/`, { noBusiness: true }),
};

export const eventService = {
  list: () => api.get<CursorPage<AccountEvent>>("/api/v1/whatsapp/events/"),
};

export const memberScopeService = {
  setScope: (membershipId: string, businessScope: "all" | "selected") =>
    api.patch<{ membership_id: string; business_scope: string }>(
      `/api/v1/organizations/current/members/${membershipId}/`,
      { business_scope: businessScope },
      { noBusiness: true },
    ),
};

export interface Team {
  id: string;
  name: string;
  slug: string;
  description: string;
  member_ids: string[];
  created_at: string;
}

export const teamService = {
  list: () => api.get<Team[]>("/api/v1/teams/", { noBusiness: true }),
  create: (name: string, description = "") =>
    api.post<Team>("/api/v1/teams/", { name, description }, { noBusiness: true }),
  addMember: (teamId: string, membershipId: string) =>
    api.post<Team>(`/api/v1/teams/${teamId}/members/`, { membership_id: membershipId }, { noBusiness: true }),
  removeMember: (teamId: string, membershipId: string) =>
    api.delete<void>(`/api/v1/teams/${teamId}/members/${membershipId}/`, { noBusiness: true }),
};
