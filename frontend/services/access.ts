import { api } from "@/lib/api/client";
import type { AccessRow, AccountEvent, CursorPage, Notification, NotificationPreferences } from "@/types/api";

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

export const notificationService = {
  list: (unreadOnly?: boolean) =>
    api.get<CursorPage<Notification>>("/api/v1/notifications/", {
      noBusiness: true,
      query: unreadOnly ? { unread: "true" } : undefined,
    }),
  unreadCount: () =>
    api.get<{ unread_count: number }>("/api/v1/notifications/unread-count/", { noBusiness: true }),
  markRead: (id: string) =>
    api.post<Notification>(`/api/v1/notifications/${id}/read/`, undefined, { noBusiness: true }),
  markAllRead: () =>
    api.post<{ marked_read: number }>("/api/v1/notifications/read-all/", undefined, { noBusiness: true }),
  preferences: () =>
    api.get<NotificationPreferences>("/api/v1/notifications/preferences/", { noBusiness: true }),
  updatePreferences: (mutedKinds: string[]) =>
    api.patch<NotificationPreferences>(
      "/api/v1/notifications/preferences/",
      { muted_kinds: mutedKinds },
      { noBusiness: true },
    ),
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
