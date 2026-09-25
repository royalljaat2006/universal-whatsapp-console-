"use client";

import {
  ArrowLeftIcon,
  MessagesSquareIcon,
  PanelRightCloseIcon,
  PanelRightOpenIcon,
  SmartphoneIcon,
  UserIcon,
} from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { InitialsAvatar } from "@/components/ui/initials-avatar";
import { NativeSelect } from "@/components/ui/native-select";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { ConversationSkeleton, EmptyState, ErrorState } from "@/components/ui/states";
import { StatusBadge, toneFor } from "@/components/ui/status-badge";
import { useApiQuery } from "@/hooks/use-api-query";
import { cn } from "@/lib/utils";
import { tagService } from "@/services/contacts";
import { type ConversationFilters as Filters, inboxService } from "@/services/inbox";
import { whatsappService } from "@/services/whatsapp";
import { useSession } from "@/stores/session-context";

import { ConversationFilters } from "./conversation-filters";
import { ConversationList } from "./conversation-list";
import { MessageComposer } from "./message-composer";
import { MessageThread } from "./message-thread";
import { ContactPanel } from "./panel/contact-panel";
import { useInbox } from "./use-inbox";

const CONNECTION = {
  idle: { label: "Offline", tone: "neutral" },
  connecting: { label: "Connecting", tone: "warning" },
  open: { label: "Live", tone: "success" },
  closed: { label: "Reconnecting", tone: "warning" },
  unauthorized: { label: "Not authorised", tone: "danger" },
} as const;

/**
 * Support-desk inbox for the selected business: conversations (left),
 * thread + composer (centre), customer context (right; a drawer on smaller
 * screens). Mobile shows one panel at a time. Everything shown comes from the
 * API and backend-originated realtime events.
 */
export function InboxView() {
  const { active, business, hasPermission } = useSession();
  const orgId = active?.organization.id;
  const businessId = business?.id;
  const [filters, setFilters] = useState<Filters>({});
  const [showPanel, setShowPanel] = useState(true); // xl: inline third column
  const [drawerOpen, setDrawerOpen] = useState(false); // below xl: drawer

  const inbox = useInbox(orgId, businessId, filters);
  const numbers = useApiQuery(
    () => whatsappService.numbers(),
    [orgId, businessId, inbox.numbersVersion],
    Boolean(businessId),
  );
  const tags = useApiQuery(
    () => tagService.list(),
    [businessId],
    Boolean(businessId) && hasPermission("contacts.view"),
  );
  const assignees = useApiQuery(() => inboxService.assignees(), [businessId], Boolean(businessId));

  if (!business) {
    return (
      <EmptyState
        className="h-full"
        icon={MessagesSquareIcon}
        title="No business available"
        description="Ask an administrator to give you access to a business."
      />
    );
  }
  if (numbers.data && numbers.data.results.length === 0) {
    return (
      <EmptyState
        className="h-full"
        icon={SmartphoneIcon}
        title={`${business.name} has no WhatsApp numbers yet`}
        description="Conversations appear here once a WhatsApp Business Account is connected and customers message its numbers."
      />
    );
  }

  const selected = inbox.selected;
  const selectedNumber = numbers.data?.results.find((n) => n.id === selected?.phone_number_id);
  const filtered = Object.values(filters).some(Boolean);
  const connection = CONNECTION[inbox.connection];
  const panel = (key: string) =>
    selected ? (
      <ContactPanel
        key={key}
        conversation={selected}
        onConversationChange={inbox.replaceConversation}
        tags={tags.data ?? []}
        assignees={assignees.data}
        contactVersion={inbox.contactVersion}
      />
    ) : null;

  return (
    <div className="flex h-full min-h-0">
      {/* Left: conversation list (hidden on mobile while a thread is open). */}
      <aside
        className={cn(
          "bg-surface flex min-h-0 w-full shrink-0 flex-col border-r md:w-80 lg:w-[22rem]",
          selected && "hidden md:flex",
        )}
      >
        <div className="flex items-center justify-between gap-2 border-b px-3 py-2.5">
          <div className="flex items-center gap-2">
            <h1 className="text-section-title">Inbox</h1>
            <StatusBadge tone={connection.tone} title="Realtime connection">
              {connection.label}
            </StatusBadge>
          </div>
          <NativeSelect
            aria-label="WhatsApp number"
            className="h-7 max-w-40 text-xs"
            value={filters.phone_number ?? ""}
            onChange={(e) => setFilters((f) => ({ ...f, phone_number: e.target.value || undefined }))}
          >
            <option value="">All numbers ({numbers.data?.results.length ?? "…"})</option>
            {numbers.data?.results.map((n) => (
              <option key={n.id} value={n.id}>
                {n.display_phone_number}
              </option>
            ))}
          </NativeSelect>
        </div>
        <ConversationFilters
          value={filters}
          onChange={setFilters}
          tags={tags.data ?? []}
          assignees={assignees.data}
        />
        {inbox.alerts.map((alert) => (
          <div
            key={alert.id}
            role="alert"
            className={cn(
              "flex items-start justify-between gap-2 border-b px-3 py-2 text-xs",
              alert.severity === "critical" ? "bg-danger-soft" : "bg-warning-soft",
            )}
          >
            <span>
              <strong className="mr-1">{alert.severity === "critical" ? "Critical:" : "Warning:"}</strong>
              {alert.summary || alert.event}
            </span>
            <Button variant="ghost" size="xs" onClick={() => inbox.dismissAlert(alert.id)}>
              Dismiss
            </Button>
          </div>
        ))}
        {inbox.error ? (
          <ErrorState error={inbox.error} onRetry={inbox.reload} inline className="m-3" />
        ) : null}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {!inbox.conversationsLoaded && !inbox.error ? (
            <ConversationSkeleton />
          ) : (
            <ConversationList
              conversations={inbox.conversations}
              selectedId={selected?.id ?? null}
              onSelect={inbox.selectConversation}
              hasMore={inbox.hasMoreConversations}
              onLoadMore={inbox.loadMoreConversations}
              filtered={filtered}
            />
          )}
        </div>
      </aside>

      {/* Centre: thread + composer. */}
      <section
        className={cn("bg-surface-muted flex min-h-0 min-w-0 flex-1 flex-col", !selected && "hidden md:flex")}
      >
        {selected ? (
          <>
            <header className="bg-surface flex items-center gap-3 border-b px-3 py-2.5 md:px-5">
              <Button
                variant="ghost"
                size="icon"
                className="md:hidden"
                aria-label="Back to conversations"
                onClick={() => inbox.selectConversation(null)}
              >
                <ArrowLeftIcon aria-hidden />
              </Button>
              <InitialsAvatar name={selected.contact_name} seed={selected.contact_id} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold">{selected.contact_name}</p>
                <p className="text-caption flex flex-wrap items-center gap-x-2 truncate">
                  <span className="tabular-nums">
                    {selected.contact_phone_number || "Phone hidden (username)"}
                  </span>
                  <span aria-hidden>·</span>
                  <span>via {selected.phone_number_display}</span>
                  <span aria-hidden>·</span>
                  <span className="inline-flex items-center gap-1">
                    <UserIcon aria-hidden className="size-3" />
                    {selected.assignee ? selected.assignee.name : "Unassigned"}
                  </span>
                </p>
              </div>
              <StatusBadge tone={toneFor(selected.status === "resolved" ? "succeeded" : selected.status)}>
                {selected.status}
              </StatusBadge>
              <Button
                variant="ghost"
                size="icon"
                className="hidden xl:inline-flex"
                aria-label={showPanel ? "Hide customer details" : "Show customer details"}
                aria-pressed={showPanel}
                onClick={() => setShowPanel((v) => !v)}
              >
                {showPanel ? <PanelRightCloseIcon aria-hidden /> : <PanelRightOpenIcon aria-hidden />}
              </Button>
              <Button variant="outline" size="sm" className="xl:hidden" onClick={() => setDrawerOpen(true)}>
                Details
              </Button>
            </header>
            <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
              <MessageThread
                messages={inbox.messages}
                hasOlder={inbox.hasOlderMessages}
                onLoadOlder={inbox.loadOlderMessages}
              />
            </div>
            <MessageComposer
              conversation={selected}
              businessAccountId={selectedNumber?.business_account_id}
              canSend={hasPermission("messages.send")}
              onQueued={inbox.addMessage}
            />
          </>
        ) : (
          <EmptyState
            className="m-auto"
            icon={MessagesSquareIcon}
            title="Select a conversation"
            description="Choose a conversation on the left to read the thread and reply."
          />
        )}
      </section>

      {/* Right: customer context. */}
      {selected && showPanel ? (
        <aside
          className="bg-surface hidden w-80 shrink-0 overflow-y-auto border-l xl:block"
          aria-label="Customer details"
        >
          {panel(selected.id)}
        </aside>
      ) : null}
      <Sheet open={Boolean(selected) && drawerOpen} onOpenChange={setDrawerOpen}>
        <SheetContent className="w-full overflow-y-auto p-0 sm:max-w-sm xl:hidden">
          <SheetTitle className="border-b px-4 py-3 text-sm">Customer details</SheetTitle>
          {selected ? panel(selected.id) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}
