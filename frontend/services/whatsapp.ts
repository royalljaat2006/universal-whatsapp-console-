import { api } from "@/lib/api/client";
import type {
  BusinessAccount,
  CursorPage,
  EmbeddedSignupConfig,
  ConnectionHealth,
  PhoneNumber,
  SetupStatus,
  TestMessagePreview,
  TestMessageSent,
  TestMessageStatus,
} from "@/types/api";

export interface CompleteEmbeddedSignupInput {
  business_id: string;
  waba_id: string;
  phone_number_id: string;
  code: string;
  /** One-time value from ``embeddedSignupSession`` (CSRF / replay protection). */
  state: string;
}

export interface TestMessageRequest {
  phone_number_id: string;
  contact_id?: string;
  to?: string;
  attest_consent?: boolean;
  template_id?: string;
}

export const whatsappService = {
  accounts: () => api.get<CursorPage<BusinessAccount>>("/api/v1/whatsapp/accounts/"),
  numbers: (query: { business_account?: string; status?: string } = {}) =>
    api.get<CursorPage<PhoneNumber>>("/api/v1/whatsapp/numbers/", { query }),
  /** Plain-language health from stored Meta signals (never a live Meta call). */
  health: () => api.get<ConnectionHealth>("/api/v1/whatsapp/health/"),
  /** Real readiness from stored evidence (never a live Meta call). */
  setup: () => api.get<SetupStatus>("/api/v1/whatsapp/setup/"),
  /** What a test message WOULD send. Sends nothing. */
  previewTestMessage: (body: TestMessageRequest) =>
    api.post<TestMessagePreview>("/api/v1/whatsapp/test-message/preview/", body),
  /** The separate approval gate: the only call that really sends. */
  sendTestMessage: (approvalToken: string) =>
    api.post<TestMessageSent>("/api/v1/whatsapp/test-message/send/", {
      approval_token: approvalToken,
      approve: true,
    }),
  cancelTestMessage: (approvalToken: string) =>
    api.post<{ cancelled: boolean }>("/api/v1/whatsapp/test-message/cancel/", {
      approval_token: approvalToken,
    }),
  /** The message's own stored status, exactly as WhatsApp reported it. */
  testMessageStatus: (id: string) => api.get<TestMessageStatus>(`/api/v1/messages/${id}/`),
  requestSync: (accountId: string) =>
    api.post<{ status: string }>(`/api/v1/whatsapp/accounts/${accountId}/sync/`),
  requestVerify: (accountId: string) =>
    api.post<{ status: string }>(`/api/v1/whatsapp/accounts/${accountId}/verify/`),
  /** Non-secret Facebook JS SDK launch config for Embedded Signup. */
  embeddedSignupConfig: () =>
    api.get<EmbeddedSignupConfig>("/api/v1/whatsapp/embedded-signup/config/", { noBusiness: true }),
  /** Server-issued one-time ``state`` to send back when the sign-up completes. */
  embeddedSignupSession: (businessId: string) =>
    api.post<{ state: string; expires_in: number }>(
      "/api/v1/whatsapp/embedded-signup/session/",
      { business_id: businessId },
      { noBusiness: true },
    ),
  /** Exchanges the Embedded Signup code for a token and connects the WABA. */
  completeEmbeddedSignup: (input: CompleteEmbeddedSignupInput) =>
    api.post<BusinessAccount>("/api/v1/whatsapp/embedded-signup/complete/", input, {
      noBusiness: true,
    }),
};
