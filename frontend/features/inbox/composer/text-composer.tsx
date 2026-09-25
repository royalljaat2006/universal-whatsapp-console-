"use client";

import { SendHorizontalIcon, SmileIcon } from "lucide-react";
import { useRef, useState } from "react";

import { ApiErrorNotice } from "@/components/status/api-error-notice";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/native-select";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { LoadingButton } from "@/components/ui/spinner";
import { inboxService } from "@/services/inbox";
import type { InboxConversation, InboxMessage } from "@/types/api";

import { useSend } from "./use-send";

const EMOJI = [
  "😀",
  "😊",
  "🙂",
  "😉",
  "😍",
  "🤝",
  "🙏",
  "👍",
  "👌",
  "👏",
  "🎉",
  "✅",
  "❤️",
  "🔥",
  "⭐",
  "💡",
  "📦",
  "🚚",
  "📄",
  "💳",
  "💰",
  "📞",
  "⏰",
  "📍",
  "😅",
  "😢",
  "😮",
  "🤔",
  "🙌",
  "👋",
  "✔️",
  "❗",
];

/** Free-form text reply; appears as QUEUED and advances only via realtime events. */
export function TextComposer({
  conversation,
  onQueued,
}: {
  conversation: InboxConversation;
  onQueued: (message: InboxMessage) => void;
}) {
  const [text, setText] = useState("");
  const [emojiOpen, setEmojiOpen] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const { send, sending, error } = useSend(onQueued);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const body = text.trim();
    if (!body) return;
    if (await send((key) => inboxService.sendText(conversation.id, body, key))) setText("");
  }

  function insert(emoji: string) {
    const el = area.current;
    const start = el?.selectionStart ?? text.length;
    const end = el?.selectionEnd ?? text.length;
    const next = text.slice(0, start) + emoji + text.slice(end);
    setText(next);
    setEmojiOpen(false);
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(start + emoji.length, start + emoji.length);
    });
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      {error ? <ApiErrorNotice error={error} /> : null}
      <div className="bg-surface focus-within:border-ring focus-within:ring-ring/30 flex items-end gap-1 rounded-lg border p-1.5 focus-within:ring-3">
        <Popover open={emojiOpen} onOpenChange={setEmojiOpen}>
          <PopoverTrigger
            render={<Button type="button" variant="ghost" size="icon" aria-label="Insert emoji" />}
          >
            <SmileIcon aria-hidden />
          </PopoverTrigger>
          <PopoverContent align="start" side="top" className="w-72 p-2">
            <div role="listbox" aria-label="Emoji" className="grid grid-cols-8 gap-0.5">
              {EMOJI.map((e) => (
                <button
                  key={e}
                  type="button"
                  role="option"
                  aria-selected={false}
                  aria-label={`Insert ${e}`}
                  onClick={() => insert(e)}
                  className="hover:bg-muted focus-visible:ring-ring/50 flex size-8 items-center justify-center rounded-md text-lg outline-none focus-visible:ring-2"
                >
                  {e}
                </button>
              ))}
            </div>
          </PopoverContent>
        </Popover>
        <Textarea
          ref={area}
          aria-label="Message"
          rows={1}
          className="max-h-40 min-h-9 flex-1 resize-none border-0 bg-transparent px-1.5 py-2 shadow-none focus-visible:ring-0"
          maxLength={4096}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              e.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder="Type a reply… (Enter to send, Shift+Enter for a new line)"
        />
        <LoadingButton type="submit" size="lg" loading={sending} disabled={!text.trim()} aria-label="Send">
          {sending ? null : <SendHorizontalIcon aria-hidden />}
          <span className="hidden sm:inline">{sending ? "Sending…" : "Send"}</span>
        </LoadingButton>
      </div>
    </form>
  );
}
