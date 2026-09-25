import { api } from "@/lib/api/client";
import type {
  AudienceDefinition,
  AudiencePreview,
  Campaign,
  CampaignEventRow,
  CampaignFailures,
  CampaignRecipientRow,
  CampaignReport,
  CursorPage,
  LaunchPreview,
  SavedAudience,
  VariableMapping,
} from "@/types/api";

export interface CampaignInput {
  name?: string;
  description?: string;
  phone_number_id?: string;
  template_id?: string;
  audience_id?: string | null;
  audience_definition?: AudienceDefinition;
  variable_mapping?: VariableMapping;
  max_messages_per_minute?: number;
  scheduled_at?: string | null;
  timezone?: string;
}

function nextPath(url: string): string {
  const parsed = new URL(url, window.location.origin);
  return parsed.pathname + parsed.search;
}

export const campaignService = {
  list: (query: { status?: string; q?: string } = {}) =>
    api.get<CursorPage<Campaign>>("/api/v1/campaigns/", { query: { ...query, page_size: 25 } }),
  page: (next: string) => api.get<CursorPage<Campaign>>(nextPath(next)),
  get: (id: string) => api.get<Campaign>(`/api/v1/campaigns/${id}/`),
  create: (input: CampaignInput) => api.post<Campaign>("/api/v1/campaigns/", input),
  update: (id: string, input: CampaignInput) => api.patch<Campaign>(`/api/v1/campaigns/${id}/`, input),
  remove: (id: string) => api.delete<void>(`/api/v1/campaigns/${id}/`),
  preview: (id: string) => api.get<LaunchPreview>(`/api/v1/campaigns/${id}/preview/`),
  launch: (id: string) =>
    api.post<{ campaign: Campaign; launch: LaunchPreview }>(`/api/v1/campaigns/${id}/launch/`, {
      confirm: true,
    }),
  pause: (id: string) => api.post<{ campaign: Campaign }>(`/api/v1/campaigns/${id}/pause/`),
  resume: (id: string) => api.post<{ campaign: Campaign }>(`/api/v1/campaigns/${id}/resume/`),
  cancel: (id: string) =>
    api.post<{ campaign: Campaign; pending_cancelled: number; queued_stopped: number }>(
      `/api/v1/campaigns/${id}/cancel/`,
    ),
  retryFailed: (id: string) =>
    api.post<{ campaign: Campaign; recipients: number }>(`/api/v1/campaigns/${id}/retry-failed/`),
  report: (id: string) => api.get<CampaignReport>(`/api/v1/campaigns/${id}/report/`),
  failures: (id: string) => api.get<CampaignFailures>(`/api/v1/campaigns/${id}/failures/`),
  recipients: (id: string, query: { status?: string; failure_category?: string } = {}) =>
    api.get<CursorPage<CampaignRecipientRow>>(`/api/v1/campaigns/${id}/recipients/`, {
      query: { ...query, page_size: 50 },
    }),
  recipientsPage: (next: string) => api.get<CursorPage<CampaignRecipientRow>>(nextPath(next)),
  events: (id: string) => api.get<CampaignEventRow[]>(`/api/v1/campaigns/${id}/events/`),
  audiencePreview: (definition: AudienceDefinition) =>
    api.post<AudiencePreview>("/api/v1/campaigns/audience-preview/", { definition }),
};

export const audienceService = {
  list: () => api.get<SavedAudience[]>("/api/v1/audiences/"),
  create: (name: string, definition: AudienceDefinition, description = "") =>
    api.post<SavedAudience>("/api/v1/audiences/", { name, definition, description }),
  remove: (id: string) => api.delete<void>(`/api/v1/audiences/${id}/`),
};
