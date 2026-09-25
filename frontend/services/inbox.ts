import { api } from "@/lib/api/client";
import type {
  AssigneeOptions,
  AssignmentEntry,
  ConversationStatus,
  CursorPage,
  InboxConversation,
  InboxMessage,
} from "@/types/api";

export interface ConversationFilters {
  phone_number?: string;
  status?: ConversationStatus | "";
  unread?: "1";
  assigned?: "me" | "none" | string;
  team?: string;
  tag?: string;
  q?: string;
  date_from?: string;
  date_to?: string;
}

export type TemplateParameters = {
  header?: { text?: string; media_asset_id?: string };
  body?: Record<string, string>;
  buttons?: Record<string, string>;
};

/** Follow a server-issued cursor link (same-origin path + query). */
function nextPath(url: string): string {
  const parsed = new URL(url, window.location.origin);
  return parsed.pathname + parsed.search;
}

export const inboxService = {
  conversations: (filters: ConversationFilters = {}) =>
    api.get<CursorPage<InboxConversation>>("/api/v1/conversations/", {
      query: { ...filters, page_size: 30 } as Record<string, string | number | undefined>,
    }),
  conversationsPage: (next: string) => api.get<CursorPage<InboxConversation>>(nextPath(next)),
  conversation: (id: string) => api.get<InboxConversation>(`/api/v1/conversations/${id}/`),
  markRead: (id: string) => api.post<InboxConversation>(`/api/v1/conversations/${id}/read/`),
  setStatus: (id: string, status: ConversationStatus) =>
    api.patch<InboxConversation>(`/api/v1/conversations/${id}/`, { status }),
  assign: (id: string, membership_id: string | null, team_id: string | null) =>
    api.post<InboxConversation>(`/api/v1/conversations/${id}/assign/`, { membership_id, team_id }),
  assignments: (id: string) => api.get<AssignmentEntry[]>(`/api/v1/conversations/${id}/assignments/`),
  assignees: () => api.get<AssigneeOptions>("/api/v1/conversations/assignees/"),
  addTag: (id: string, tagId: string) =>
    api.post<InboxConversation>(`/api/v1/conversations/${id}/tags/`, { tag_id: tagId }),
  removeTag: (id: string, tagId: string) =>
    api.delete<InboxConversation>(`/api/v1/conversations/${id}/tags/${tagId}/`),
  messages: (conversationId: string) =>
    api.get<CursorPage<InboxMessage>>("/api/v1/messages/", {
      query: { conversation: conversationId, page_size: 50 },
    }),
  messagesPage: (next: string) => api.get<CursorPage<InboxMessage>>(nextPath(next)),
  message: (id: string) => api.get<InboxMessage>(`/api/v1/messages/${id}/`),
  sendText: (conversationId: string, body: string, idempotencyKey: string) =>
    api.post<InboxMessage>(
      "/api/v1/messages/",
      { conversation_id: conversationId, type: "text", text: { body } },
      { headers: { "Idempotency-Key": idempotencyKey } },
    ),
  sendTemplate: (conversationId: string, templateId: string, parameters: TemplateParameters, key: string) =>
    api.post<InboxMessage>(
      "/api/v1/messages/",
      {
        conversation_id: conversationId,
        type: "template",
        template: { template_id: templateId, parameters },
      },
      { headers: { "Idempotency-Key": key } },
    ),
  sendMedia: (conversationId: string, mediaAssetId: string, caption: string, key: string) =>
    api.post<InboxMessage>(
      "/api/v1/messages/",
      {
        conversation_id: conversationId,
        type: "media",
        media: { media_asset_id: mediaAssetId, caption: caption || undefined },
      },
      { headers: { "Idempotency-Key": key } },
    ),
};
