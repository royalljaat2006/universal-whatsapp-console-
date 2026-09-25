import { api } from "@/lib/api/client";
import type { Business } from "@/types/api";

export const businessService = {
  /** Businesses the current member may access (server-filtered). */
  list: () => api.get<Business[]>("/api/v1/businesses/", { noBusiness: true }),
};
