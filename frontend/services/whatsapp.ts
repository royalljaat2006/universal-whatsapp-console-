import { api } from "@/lib/api/client";
import type { BusinessAccount, CursorPage, PhoneNumber } from "@/types/api";

export const whatsappService = {
  accounts: () => api.get<CursorPage<BusinessAccount>>("/api/v1/whatsapp/accounts/"),
  numbers: (query: { business_account?: string; status?: string } = {}) =>
    api.get<CursorPage<PhoneNumber>>("/api/v1/whatsapp/numbers/", { query }),
  requestSync: (accountId: string) =>
    api.post<{ status: string }>(`/api/v1/whatsapp/accounts/${accountId}/sync/`),
  requestVerify: (accountId: string) =>
    api.post<{ status: string }>(`/api/v1/whatsapp/accounts/${accountId}/verify/`),
};
