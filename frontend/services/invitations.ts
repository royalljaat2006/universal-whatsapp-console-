import { api } from "@/lib/api/client";
import { authService } from "@/services/auth";
import type { CreatedInvitation, Invitation, OrgRoleKey } from "@/types/api";

export interface InvitationPreview {
  organization_name: string;
  role_name: string;
  email: string;
  status: "pending" | "accepted" | "revoked";
  expires_at: string;
}

export interface AcceptedInvitation {
  organization_id: string;
  role: OrgRoleKey;
  joined_at: string | null;
}

/** Public (no X-Organization-ID — the invitee has no membership yet). */
export const invitationService = {
  preview: (id: string) => api.get<InvitationPreview>(`/api/v1/invitations/${id}/`, { unscoped: true }),
  accept: async (
    id: string,
    input: { token: string; password?: string; full_name?: string },
  ): Promise<AcceptedInvitation> => {
    // Mirrors authService.login(): login-CSRF applies here too, since
    // accepting can create a brand-new session (see apps.organizations.api.invitations).
    await authService.primeCsrf();
    return api.post<AcceptedInvitation>(`/api/v1/invitations/${id}/accept/`, input, {
      unscoped: true,
    });
  },
};

/** Tenant-scoped (members.view/members.manage) — inviting/managing teammates
 * of the caller's own organization, as opposed to the public preview/accept
 * flow above. No email is sent server-side; share the returned link manually. */
export const teamInvitationService = {
  list: () => api.get<Invitation[]>("/api/v1/organizations/current/invitations/", { noBusiness: true }),
  create: (email: string, roleKey: OrgRoleKey) =>
    api.post<CreatedInvitation>(
      "/api/v1/organizations/current/invitations/",
      { email, role_key: roleKey },
      { noBusiness: true },
    ),
  revoke: (id: string) =>
    api.delete<void>(`/api/v1/organizations/current/invitations/${id}/`, { noBusiness: true }),
};
