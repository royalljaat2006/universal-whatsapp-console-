import { api } from "@/lib/api/client";
import type { FailedMessagesResponse, FailureDetail, RetryResponse } from "@/types/api";

export const recoveryService = {
  /** Recent failed messages with a plain explanation and the one safe next step. */
  failures: (query: { days?: number; limit?: number } = {}) =>
    api.get<FailedMessagesResponse>("/api/v1/messages/failures/", { query }),
  /** One failure plus, when a retry is allowed, exactly what a retry would do. */
  detail: (id: string) => api.get<FailureDetail>(`/api/v1/messages/${id}/recovery/`),
  /** Queue ONE retry. Only valid after the person confirmed the preview. */
  retry: (id: string) => api.post<RetryResponse>(`/api/v1/messages/${id}/retry/`, { confirm: true }),
};
