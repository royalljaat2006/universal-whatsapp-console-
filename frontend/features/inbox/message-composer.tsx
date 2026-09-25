"use client";

import { FileTextIcon, LockIcon, MessageSquareTextIcon, PaperclipIcon } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";
import type { InboxConversation, InboxMessage } from "@/types/api";

import { MediaComposer } from "./composer/media-composer";
import { PolicyBanner } from "./composer/policy-banner";
import { TemplateComposer } from "./composer/template-composer";
import { TextComposer } from "./composer/text-composer";

type Tab = "text" | "template" | "media";

/**
 * Composer driven by the backend send policy (``conversation.send_policy``).
 * Free-form tabs are offered only while the server says the customer service
 * window is open; the backend rejects anything else regardless of the UI.
 * Messages are sent through the backend only — never to Meta directly.
 */
export function MessageComposer({
  conversation,
  businessAccountId,
  canSend,
  onQueued,
}: {
  conversation: InboxConversation;
  businessAccountId: string | undefined;
  canSend: boolean;
  onQueued: (message: InboxMessage) => void;
}) {
  const policy = conversation.send_policy;
  const [chosen, setChosen] = useState<Tab>("text");
  const tab: Tab = policy.free_form_allowed ? chosen : "template";

  if (!canSend) {
    return (
      <p className="text-muted-foreground bg-surface flex items-center gap-2 border-t px-4 py-3 text-xs">
        <LockIcon aria-hidden className="size-3.5" />
        You do not have permission to send messages.
      </p>
    );
  }

  const tabs: { id: Tab; label: string; icon: typeof FileTextIcon; enabled: boolean }[] = [
    { id: "text", label: "Text", icon: MessageSquareTextIcon, enabled: policy.free_form_allowed },
    { id: "template", label: "Template", icon: FileTextIcon, enabled: policy.template_allowed },
    { id: "media", label: "Attach", icon: PaperclipIcon, enabled: policy.free_form_allowed },
  ];

  return (
    <div className="bg-surface border-t">
      <PolicyBanner policy={policy} />
      <div role="tablist" aria-label="Message type" className="flex gap-1 px-3 pt-2">
        {tabs.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              disabled={!t.enabled}
              onClick={() => setChosen(t.id)}
              className={cn(
                "focus-visible:ring-ring/50 inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium transition-colors outline-none focus-visible:ring-2 disabled:cursor-not-allowed disabled:opacity-40",
                tab === t.id
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Icon aria-hidden className="size-3.5" />
              {t.label}
            </button>
          );
        })}
      </div>
      <div className="px-3 pt-2 pb-3">
        {tab === "text" ? <TextComposer conversation={conversation} onQueued={onQueued} /> : null}
        {tab === "media" ? <MediaComposer conversation={conversation} onQueued={onQueued} /> : null}
        {tab === "template" ? (
          <TemplateComposer
            key={conversation.id}
            conversation={conversation}
            businessAccountId={businessAccountId}
            onQueued={onQueued}
          />
        ) : null}
      </div>
    </div>
  );
}
