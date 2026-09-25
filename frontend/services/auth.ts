import { api } from "@/lib/api/client";
import type { User } from "@/types/api";

export const authService = {
  /** Ensures the CSRF cookie exists before the first unsafe request. */
  primeCsrf: () => api.get<void>("/api/v1/auth/csrf/", { unscoped: true }),
  login: async (email: string, password: string) => {
    await authService.primeCsrf();
    return api.post<User>("/api/v1/auth/login/", { email, password }, { unscoped: true });
  },
  logout: () => api.post<void>("/api/v1/auth/logout/", undefined, { unscoped: true }),
  me: () => api.get<User>("/api/v1/auth/me/", { unscoped: true }),
};
