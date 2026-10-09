import { api } from "@/lib/api/client";
import type { CursorPage, MessageTemplate, MessageTemplateDetail } from "@/types/api";

export interface CreateTemplateInput {
  business_account_id: string;
  name: string;
  language: string;
  category: "MARKETING" | "UTILITY" | "AUTHENTICATION";
  components: Record<string, unknown>[];
  parameter_format?: "POSITIONAL" | "NAMED";
}

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
  create: (input: CreateTemplateInput) =>
    api.post<MessageTemplateDetail>("/api/v1/templates/create/", input),
};
