import { api } from "@/lib/api/client";
import type { CursorPage, MessageTemplate, MessageTemplateDetail } from "@/types/api";

export const templateService = {
  list: (
    query: {
      q?: string;
      status?: string;
      category?: string;
      language?: string;
      business_account?: string;
      sendable?: "1";
    } = {},
  ) => api.get<CursorPage<MessageTemplate>>("/api/v1/templates/", { query: { ...query, page_size: 100 } }),
  get: (id: string) => api.get<MessageTemplateDetail>(`/api/v1/templates/${id}/`),
  sync: () => api.post<{ status: string; accounts: number }>("/api/v1/templates/sync/"),
};
