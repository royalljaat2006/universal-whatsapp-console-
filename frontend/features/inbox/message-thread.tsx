"use client";

import { FileTextIcon, MessageSquareDashedIcon, TriangleAlertIcon } from "lucide-react";
import { Fragment, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/states";
import { dayKey, dayLabel } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { InboxMessage } from "@/types/api";

import { MessageAttachment } from "./message-attachment";
import { MessageStatus } from "./message-status";

function fallbackBody(message: InboxMessage): string | null {
  if (message.text) return message.text;
  if (message.attachments.length > 0 || message.template) return null;
  const providerType = (message.content as { provider_type?: string } | undefined)?.provider_type;
  return `[${providerType ?? message.message_type} message — this type is not rendered by the console yet]`;
}

export function MessageThread({
  messages,
  hasOlder,
  onLoadOlder,
}: {
  messages: InboxMessage[];
  hasOlder: boolean;
  onLoadOlder: () => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  // Only messages created after this thread was opened (real realtime events
  // or sends) get the arrival animation; history renders still.
  const [openedAt] = useState(() => Date.now());
  const lastId = messages.at(-1)?.id;
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [lastId]);

  if (messages.length === 0) {
    return (
      <EmptyState
        className="m-auto"
        icon={MessageSquareDashedIcon}
        title="No messages in this conversation yet."
        description="Messages appear here as soon as they are sent or received."
      />
    );
  }

  return (
    <div className="flex flex-col gap-1.5 px-4 py-5 md:px-8">
      {hasOlder ? (
        <Button variant="outline" size="xs" className="mb-2 self-center" onClick={onLoadOlder}>
          Load older messages
        </Button>
      ) : null}
      {messages.map((m, i) => {
        const showDay = i === 0 || dayKey(messages[i - 1].occurred_at) !== dayKey(m.occurred_at);
        const body = fallbackBody(m);
        const outbound = m.direction === "outbound";
        const grouped = !showDay && i > 0 && messages[i - 1].direction === m.direction;
        const fresh = new Date(m.created_at).getTime() > openedAt;
        return (
          <Fragment key={m.id}>
            {showDay ? (
              <div className="bg-surface text-muted-foreground my-3 self-center rounded-full border px-3 py-0.5 text-[11px] font-medium shadow-xs">
                {dayLabel(m.occurred_at)}
              </div>
            ) : null}
            <div
              className={cn(
                "flex max-w-[min(38rem,78%)] flex-col gap-1 rounded-xl px-3 py-2 text-sm shadow-xs",
                outbound
                  ? "bg-brand-soft self-end rounded-br-sm border border-[oklch(0.9_0.05_160)]"
                  : "bg-surface self-start rounded-bl-sm border",
                grouped ? "mt-0" : "mt-1.5",
                m.status === "failed" && "border-destructive/50",
                fresh && "animate-in fade-in slide-in-from-bottom-1 duration-200",
              )}
            >
              {m.template ? (
                <p className="text-primary flex items-center gap-1 text-[11px] font-medium">
                  <FileTextIcon aria-hidden className="size-3" />
                  Template · {m.template.name} ({m.template.language})
                  {m.template.category ? ` · ${m.template.category}` : ""}
                </p>
              ) : null}
              {m.attachments.map((a) => (
                <MessageAttachment key={a.id} attachment={a} />
              ))}
              {body ? <p className="leading-5 break-words whitespace-pre-wrap">{body}</p> : null}
              {m.attachments
                .filter((a) => a.caption && a.caption !== m.text)
                .map((a) => (
                  <p key={`${a.id}-caption`} className="break-words whitespace-pre-wrap">
                    {a.caption}
                  </p>
                ))}
              {m.status === "failed" && m.error_message ? (
                <p className="bg-danger-soft text-destructive flex items-start gap-1.5 rounded-md px-2 py-1 text-xs">
                  <TriangleAlertIcon aria-hidden className="mt-0.5 size-3 shrink-0" />
                  {m.error_message}
                </p>
              ) : null}
              <p className="text-muted-foreground flex items-center justify-end gap-2 text-[11px]">
                <time dateTime={m.occurred_at}>
                  {new Date(m.occurred_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </time>
                <MessageStatus message={m} />
              </p>
            </div>
          </Fragment>
        );
      })}
      <div ref={endRef} />
    </div>
  );
}
