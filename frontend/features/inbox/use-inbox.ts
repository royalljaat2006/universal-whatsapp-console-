"use client";

import { useCallback, useEffect, useState } from "react";

import { useBusinessRealtime } from "@/hooks/use-business-realtime";
import { ApiError } from "@/lib/api/errors";
import { type ConversationFilters, inboxService } from "@/services/inbox";
import type {
  AccountEvent,
  InboxConversation,
  InboxMessage,
  RealtimeConversationEvent,
  RealtimeEvent,
  RealtimeMessageEvent,
} from "@/types/api";

function upsert<T extends { id: string }>(items: T[], item: T): T[] {
  const index = items.findIndex((existing) => existing.id === item.id);
  if (index === -1) return [...items, item];
  const copy = items.slice();
  copy[index] = item;
  return copy;
}

const byActivity = (a: InboxConversation, b: InboxConversation) =>
  (b.last_message_at ?? "").localeCompare(a.last_message_at ?? "");
const byTime = (a: InboxMessage, b: InboxMessage) => a.occurred_at.localeCompare(b.occurred_at);

const CONVERSATION_EVENTS = new Set([
  "conversation.created",
  "conversation.updated",
  "conversation.assigned",
  "conversation.tags_changed",
]);
const MESSAGE_EVENTS = new Set(["message.created", "message.status_changed", "message.media_ready"]);

const toError = (cause: unknown) =>
  cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause));

/**
 * Inbox state for one business. Initial data and every page come from REST
 * (server-side search/filters, cursor pagination). Afterwards all changes are
 * driven by backend realtime events: an event carries ids, and the affected
 * resource is re-read over REST so permissions and business scope are
 * re-applied by the server. After a reconnect everything is resynchronised.
 */
export function useInbox(organizationId?: string, businessId?: string, filters: ConversationFilters = {}) {
  const [conversations, setConversations] = useState<InboxConversation[]>([]);
  const [nextConversations, setNextConversations] = useState<string | null>(null);
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<InboxMessage[]>([]);
  const [olderMessages, setOlderMessages] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [version, setVersion] = useState(0);
  const [contactVersion, setContactVersion] = useState(0);
  const [numbersVersion, setNumbersVersion] = useState(0);
  const [alerts, setAlerts] = useState<Pick<AccountEvent, "id" | "event" | "severity" | "summary">[]>([]);
  const filterKey = JSON.stringify(filters);

  useEffect(() => {
    if (!businessId) return;
    let cancelled = false;
    inboxService
      .conversations(JSON.parse(filterKey) as ConversationFilters)
      .then((page) => {
        if (cancelled) return;
        setConversations(page.results);
        setNextConversations(page.next);
        setLoadedKey(filterKey);
        setError(null);
      })
      .catch((cause) => !cancelled && setError(toError(cause)));
    return () => {
      cancelled = true;
    };
  }, [businessId, filterKey, version]);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    inboxService
      .messages(selectedId)
      .then((page) => {
        if (cancelled) return;
        setMessages(page.results.slice().sort(byTime));
        setOlderMessages(page.next);
      })
      .catch((cause) => !cancelled && setError(toError(cause)));
    inboxService
      .markRead(selectedId)
      .then((c) => !cancelled && setConversations((items) => upsert(items, c)))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [selectedId, version]);

  const refreshConversation = useCallback(
    (id: string) => {
      const parsed = JSON.parse(filterKey) as ConversationFilters;
      inboxService
        .conversation(id)
        .then((conversation) => {
          if (parsed.phone_number && conversation.phone_number_id !== parsed.phone_number) return;
          setConversations((items) => upsert(items, conversation).sort(byActivity));
        })
        .catch(() => undefined);
    },
    [filterKey],
  );

  const onEvent = useCallback(
    (event: RealtimeEvent) => {
      if (CONVERSATION_EVENTS.has(event.event)) {
        refreshConversation((event.data as unknown as RealtimeConversationEvent).id);
      } else if (MESSAGE_EVENTS.has(event.event)) {
        const data = event.data as unknown as RealtimeMessageEvent;
        if (data.conversation_id !== selectedId) return;
        inboxService
          .message(data.id)
          .then((message) => setMessages((items) => upsert(items, message).sort(byTime)))
          .catch(() => undefined);
        if (event.event === "message.created" && data.direction === "inbound") {
          inboxService.markRead(data.conversation_id).catch(() => undefined);
        }
      } else if (event.event === "contact.updated") {
        setContactVersion((v) => v + 1);
      } else if (event.event === "number.status_changed") {
        setNumbersVersion((v) => v + 1);
      } else if (event.event === "account.alert") {
        const alert = event.data as unknown as AccountEvent;
        if (alert.severity !== "info") setAlerts((items) => upsert(items, alert).slice(-3));
      }
    },
    [refreshConversation, selectedId],
  );

  const connection = useBusinessRealtime(organizationId, businessId, {
    onEvent,
    onResync: () => {
      setVersion((v) => v + 1);
      setContactVersion((v) => v + 1);
      setNumbersVersion((v) => v + 1);
    },
  });

  const loadMoreConversations = useCallback(() => {
    if (!nextConversations) return;
    inboxService
      .conversationsPage(nextConversations)
      .then((page) => {
        setConversations((items) => page.results.reduce(upsert, items));
        setNextConversations(page.next);
      })
      .catch((cause) => setError(toError(cause)));
  }, [nextConversations]);

  const loadOlderMessages = useCallback(() => {
    if (!olderMessages) return;
    inboxService
      .messagesPage(olderMessages)
      .then((page) => {
        setMessages((items) => page.results.reduce(upsert, items).sort(byTime));
        setOlderMessages(page.next);
      })
      .catch((cause) => setError(toError(cause)));
  }, [olderMessages]);

  const selectConversation = useCallback((id: string | null) => {
    setMessages([]);
    setOlderMessages(null);
    setSelectedId(id);
  }, []);

  return {
    conversations,
    conversationsLoaded: loadedKey === filterKey,
    hasMoreConversations: Boolean(nextConversations),
    loadMoreConversations,
    selected: conversations.find((c) => c.id === selectedId) ?? null,
    selectConversation,
    replaceConversation: (c: InboxConversation) => setConversations((items) => upsert(items, c)),
    messages,
    hasOlderMessages: Boolean(olderMessages),
    loadOlderMessages,
    addMessage: (m: InboxMessage) => setMessages((items) => upsert(items, m).sort(byTime)),
    contactVersion,
    numbersVersion,
    alerts,
    dismissAlert: (id: string) => setAlerts((items) => items.filter((a) => a.id !== id)),
    error,
    connection,
    reload: () => setVersion((v) => v + 1),
  };
}
