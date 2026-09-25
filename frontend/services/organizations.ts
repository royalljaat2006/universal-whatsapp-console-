import { api } from "@/lib/api/client";
import type { MyMembership } from "@/types/api";

export const organizationService = {
  myMemberships: () => api.get<MyMembership[]>("/api/v1/organizations/", { unscoped: true }),
};
