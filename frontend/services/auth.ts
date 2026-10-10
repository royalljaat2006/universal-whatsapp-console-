import { api } from "@/lib/api/client";
import type { MfaChallenge, MfaEnrolment, MfaStatus, User } from "@/types/api";

export const authService = {
  /** Ensures the CSRF cookie exists before the first unsafe request. */
  primeCsrf: () => api.get<void>("/api/v1/auth/csrf/", { unscoped: true }),
  login: async (email: string, password: string) => {
    await authService.primeCsrf();
    return api.post<User | MfaChallenge>(
      "/api/v1/auth/login/",
      { email, password },
      { unscoped: true },
    );
  },
  /** Second sign-in step: exactly one of `code` (authenticator) or `recovery_code`. */
  verifyMfa: async (input: { code: string } | { recovery_code: string }) => {
    await authService.primeCsrf();
    return api.post<User & { recovery_codes_remaining?: number }>(
      "/api/v1/auth/mfa/verify/",
      input,
      { unscoped: true },
    );
  },
  mfaStatus: () => api.get<MfaStatus>("/api/v1/auth/mfa/", { unscoped: true }),
  mfaSetup: (password: string) =>
    api.post<MfaEnrolment>("/api/v1/auth/mfa/setup/", { password }, { unscoped: true }),
  mfaConfirm: (code: string) =>
    api.post<{ recovery_codes: string[] }>("/api/v1/auth/mfa/confirm/", { code }, { unscoped: true }),
  mfaDisable: (input: { password: string; code?: string; recovery_code?: string }) =>
    api.post<void>("/api/v1/auth/mfa/disable/", input, { unscoped: true }),
  mfaRegenerateRecoveryCodes: (input: { password: string; code: string }) =>
    api.post<{ recovery_codes: string[] }>("/api/v1/auth/mfa/recovery-codes/", input, {
      unscoped: true,
    }),
  register: async (input: {
    email: string;
    password: string;
    full_name?: string;
    organization_name: string;
  }) => {
    await authService.primeCsrf();
    return api.post<User>("/api/v1/auth/register/", input, { unscoped: true });
  },
  logout: () => api.post<void>("/api/v1/auth/logout/", undefined, { unscoped: true }),
  me: () => api.get<User>("/api/v1/auth/me/", { unscoped: true }),
  requestPasswordReset: (email: string) =>
    api.post<void>("/api/v1/auth/password-reset/", { email }, { unscoped: true }),
  confirmPasswordReset: async (input: { uid: string; token: string; password: string }) => {
    await authService.primeCsrf();
    return api.post<void>("/api/v1/auth/password-reset/confirm/", input, { unscoped: true });
  },
  confirmEmailVerification: (input: { uid: string; token: string }) =>
    api.post<User>("/api/v1/auth/verify-email/confirm/", input, { unscoped: true }),
  resendEmailVerification: () =>
    api.post<void>("/api/v1/auth/verify-email/resend/", undefined, { unscoped: true }),
  deleteAccount: (password: string) =>
    api.post<void>("/api/v1/auth/account/", { password }, { unscoped: true }),
};
