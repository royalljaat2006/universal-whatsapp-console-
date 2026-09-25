"use client";

import { ClockIcon, InboxIcon, UserIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { InitialsAvatar } from "@/components/ui/initials-avatar";
import { EmptyState } from "@/components/ui/states";
import { StatusBadge, toneFor } from "@/components/ui/status-badge";
import { shortTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { InboxConversation } from "@/types/api";

export function ConversationList({
  conversations,
  selectedId,
  onSelect,
  hasMore,
  onLoadMore,
  filtered,
}: {
  conversations: InboxConversation[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  hasMore: boolean;
  onLoadMore: () => void;
  filtered: boolean;
}) {
  if (conversations.length === 0) {
    return (
      <EmptyState
        compact
        icon={InboxIcon}
        title={filtered ? "No conversations match these filters." : "No conversations yet."}
        description={
          filtered
            ? "Clear or change the filters to see more."
            : "They appear here when a customer messages one of this business's numbers."
        }
      />
    );
  }
  return (
    <>
      <ul aria-label="Conversations">
        {conversations.map((c) => {
          const unread = c.unread_count > 0;
          const active = selectedId === c.id;
          return (
            <li key={c.id}>
              <button
                type="button"
                onClick={() => onSelect(c.id)}
                aria-current={active ? "true" : undefined}
                className={cn(
                  "focus-visible:ring-ring/50 relative flex w-full gap-3 border-b px-3 py-3 text-left transition-colors outline-none focus-visible:ring-2 focus-visible:ring-inset",
                  active ? "bg-accent" : "hover:bg-muted/60",
                )}
              >
                {active ? <span aria-hidden className="bg-primary absolute inset-y-0 left-0 w-0.5" /> : null}
                <InitialsAvatar name={c.contact_name} seed={c.contact_id} />
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="flex items-center justify-between gap-2">
                    <span className={cn("truncate text-sm", unread ? "font-semibold" : "font-medium")}>
                      {c.contact_name}
                    </span>
                    <span
                      className={cn(
                        "shrink-0 text-[11px]",
                        unread ? "text-primary font-medium" : "text-muted-foreground",
                      )}
                    >
                      {shortTime(c.last_message_at)}
                    </span>
                  </span>
                  <span className="flex items-center justify-between gap-2">
                    <span
                      className={cn("truncate text-xs", unread ? "text-foreground" : "text-muted-foreground")}
                    >
                      {c.last_message_direction === "outbound" ? (
                        <span className="text-muted-foreground">You: </span>
                      ) : null}
                      {c.last_message_preview || "—"}
                    </span>
                    {unread ? (
                      <span
                        aria-label={`${c.unread_count} unread`}
                        className="bg-primary text-primary-foreground flex h-4.5 min-w-4.5 shrink-0 items-center justify-center rounded-full px-1 text-[10px] font-semibold tabular-nums"
                      >
                        {c.unread_count}
                      </span>
                    ) : null}
                  </span>
                  <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px]">
                    {c.status !== "open" ? (
                      <StatusBadge
                        tone={toneFor(c.status === "resolved" ? "succeeded" : c.status)}
                        dot={false}
                        className="h-4 px-1.5 text-[10px]"
                      >
                        {c.status}
                      </StatusBadge>
                    ) : null}
                    {c.assignee ? (
                      <span className="text-muted-foreground inline-flex items-center gap-1">
                        <UserIcon aria-hidden className="size-3" />
                        {c.assignee.name}
                      </span>
                    ) : null}
                    {c.send_policy.window_open ? null : (
                      <span className="text-warning inline-flex items-center gap-1">
                        <ClockIcon aria-hidden className="size-3" />
                        window closed
                      </span>
                    )}
                    {c.tags.slice(0, 3).map((t) => (
                      <span
                        key={t.id}
                        className="bg-muted text-muted-foreground rounded px-1.5 py-px text-[10px] font-medium"
                      >
                        {t.name}
                      </span>
                    ))}
                    <span className="text-muted-foreground ml-auto truncate">{c.phone_number_display}</span>
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {hasMore ? (
        <div className="p-3">
          <Button variant="outline" size="sm" className="w-full" onClick={onLoadMore}>
            Load more
          </Button>
        </div>
      ) : null}
    </>
  );
}
