import { api } from "@/lib/api/client";
import type { Contact, ContactNote, CursorPage, InboxConversation, Tag } from "@/types/api";

export const contactService = {
  list: (query: { q?: string; tag?: string } = {}) =>
    api.get<CursorPage<Contact>>("/api/v1/contacts/", { query }),
  get: (id: string) => api.get<Contact>(`/api/v1/contacts/${id}/`),
  update: (id: string, changes: Partial<Pick<Contact, "display_name" | "attributes" | "opt_in_status">>) =>
    api.patch<Contact>(`/api/v1/contacts/${id}/`, changes),
  notes: (id: string) => api.get<ContactNote[]>(`/api/v1/contacts/${id}/notes/`),
  addNote: (id: string, body: string) => api.post<ContactNote>(`/api/v1/contacts/${id}/notes/`, { body }),
  deleteNote: (id: string, noteId: string) => api.delete<void>(`/api/v1/contacts/${id}/notes/${noteId}/`),
  addTag: (id: string, tagId: string) => api.post<Contact>(`/api/v1/contacts/${id}/tags/`, { tag_id: tagId }),
  removeTag: (id: string, tagId: string) => api.delete<Contact>(`/api/v1/contacts/${id}/tags/${tagId}/`),
  history: (id: string) => api.get<InboxConversation[]>(`/api/v1/contacts/${id}/history/`),
};

export const tagService = {
  list: (q?: string) => api.get<Tag[]>("/api/v1/tags/", { query: { q } }),
  create: (name: string, color = "") => api.post<Tag>("/api/v1/tags/", { name, color }),
  rename: (id: string, name: string) => api.patch<Tag>(`/api/v1/tags/${id}/`, { name }),
  remove: (id: string) => api.delete<void>(`/api/v1/tags/${id}/`),
};
