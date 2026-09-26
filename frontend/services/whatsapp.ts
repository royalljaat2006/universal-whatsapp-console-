import { api } from "@/lib/api/client";
import type { BusinessAccount, CursorPage, EmbeddedSignupConfig, PhoneNumber } from "@/types/api";

export interface CompleteEmbeddedSignupInput {
  business_id: string;
  waba_id: string;
  phone_number_id: string;
  code: string;
}

export const whatsappService = {
  accounts: () => api.get<CursorPage<BusinessAccount>>("/api/v1/whatsapp/accounts/"),
  numbers: (query: { business_account?: string; status?: string } = {}) =>
    api.get<CursorPage<PhoneNumber>>("/api/v1/whatsapp/numbers/", { query }),
  requestSync: (accountId: string) =>
    api.post<{ status: string }>(`/api/v1/whatsapp/accounts/${accountId}/sync/`),
  requestVerify: (accountId: string) =>
    api.post<{ status: string }>(`/api/v1/whatsapp/accounts/${accountId}/verify/`),
  /** Non-secret Facebook JS SDK launch config for Embedded Signup. */
  embeddedSignupConfig: () =>
    api.get<EmbeddedSignupConfig>("/api/v1/whatsapp/embedded-signup/config/", { noBusiness: true }),
  /** Exchanges the Embedded Signup code for a token and connects the WABA. */
  completeEmbeddedSignup: (input: CompleteEmbeddedSignupInput) =>
    api.post<BusinessAccount>("/api/v1/whatsapp/embedded-signup/complete/", input, {
      noBusiness: true,
    }),
};
