"use client";

import { useState } from "react";

import type { ApiError } from "@/lib/api/errors";
import { toApiError } from "@/lib/format";
import { newIdempotencyKey } from "@/lib/ids";
import type { InboxMessage } from "@/types/api";

/**
 * One idempotency key per composed message: a retry after a network failure
 * reuses it (the backend returns the original message), a successful send
 * rotates it for the next message.
 */
export function useSend(onQueued: (message: InboxMessage) => void) {
  const [key, setKey] = useState(() => newIdempotencyKey());
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function send(run: (key: string) => Promise<InboxMessage>): Promise<boolean> {
    setSending(true);
    setError(null);
    try {
      onQueued(await run(key));
      setKey(newIdempotencyKey());
      return true;
    } catch (cause) {
      setError(toApiError(cause));
      return false;
    } finally {
      setSending(false);
    }
  }

  return { send, sending, error, setError };
}
