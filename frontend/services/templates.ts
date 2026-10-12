import { api } from "@/lib/api/client";
import type {
  CursorPage,
  MessageTemplate,
  MessageTemplateDetail,
  StudioContent,
  StudioOptions,
  StudioValidation,
  TemplateDraft,
  TemplateGuidance,
} from "@/types/api";

export interface DraftInput {
  business_account_id: string;
  name: string;
  language: string;
  category: string;
  parameter_format: "POSITIONAL" | "NAMED";
  content: StudioContent;
}

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
  /** Plain-language guidance for a template's status (what Meta reported, and whether it is confirmed). */
  guidance: (id: string) => api.get<TemplateGuidance>(`/api/v1/templates/${id}/guidance/`),
  /** A corrected copy of a rejected template as a new local draft. Sends nothing. */
  copyToDraft: (id: string) => api.post<TemplateDraft>(`/api/v1/templates/${id}/copy-to-draft/`),
};

export const templateStudioService = {
  options: () => api.get<StudioOptions>("/api/v1/templates/studio/options/"),
  /** Checks only: nothing is saved and nothing is sent to Meta. */
  validate: (input: DraftInput) => api.post<StudioValidation>("/api/v1/templates/studio/validate/", input),
  drafts: () => api.get<{ results: TemplateDraft[] }>("/api/v1/templates/drafts/"),
  draft: (id: string) => api.get<TemplateDraft>(`/api/v1/templates/drafts/${id}/`),
  saveDraft: (input: DraftInput) => api.post<TemplateDraft>("/api/v1/templates/drafts/", input),
  updateDraft: (id: string, changes: Partial<Omit<DraftInput, "business_account_id">>) =>
    api.patch<TemplateDraft>(`/api/v1/templates/drafts/${id}/`, changes),
  deleteDraft: (id: string) => api.delete<void>(`/api/v1/templates/drafts/${id}/`),
  /** The only call that sends a template to Meta; needs the hash of the content the person reviewed. */
  submit: (id: string, contentHash: string) =>
    api.post<TemplateDraft>(`/api/v1/templates/drafts/${id}/submit/`, {
      confirm: true,
      content_hash: contentHash,
    }),
  /** Ask Meta again (template sync). */
  check: (id: string) => api.post<{ status: string; note: string }>(`/api/v1/templates/drafts/${id}/check/`),
};
