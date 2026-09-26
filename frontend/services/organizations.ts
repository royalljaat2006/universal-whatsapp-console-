import { api } from "@/lib/api/client";
import type { CursorPage, Member, MyMembership, UsageSummary } from "@/types/api";

export const organizationService = {
  myMemberships: () => api.get<MyMembership[]>("/api/v1/organizations/", { unscoped: true }),
  /** Requires ``members.view``; callers must check ``hasPermission`` first. */
  members: () =>
    api.get<CursorPage<Member>>("/api/v1/organizations/current/members/", { noBusiness: true }),
  /** Requires ``billing.view``; callers must check ``hasPermission`` first.
   * Real, currently-countable numbers — no plan/quota model exists yet. */
  usage: () => api.get<UsageSummary>("/api/v1/organizations/current/usage/", { noBusiness: true }),
};
