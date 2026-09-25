"use client";

import { useEffect, useRef, useState } from "react";

import { acquireRealtime, type ConnectionState } from "@/lib/realtime/client";
import type { RealtimeEvent } from "@/types/api";

/**
 * Subscribe to the selected business's realtime channel. All subscribers on
 * a page share one WebSocket. ``onEvent`` receives server events;
 * ``onResync`` fires after every *re*connection so the caller refetches state
 * it may have missed while disconnected. No timers, no simulated events.
 */
export function useBusinessRealtime(
  organizationId: string | undefined,
  businessId: string | undefined,
  handlers: { onEvent: (event: RealtimeEvent) => void; onResync: () => void },
): ConnectionState {
  const [state, setState] = useState<ConnectionState>("idle");
  const handlersRef = useRef(handlers);

  useEffect(() => {
    handlersRef.current = handlers;
  });

  useEffect(() => {
    if (!organizationId || !businessId) return;
    const { client, release } = acquireRealtime(organizationId, businessId);
    let opened = client.state === "open";
    const initial = client.state;
    const offState = client.onState((next) => {
      setState(next);
      if (next === "open") {
        if (opened) handlersRef.current.onResync();
        opened = true;
      }
    });
    const offEvent = client.onEvent((event) => handlersRef.current.onEvent(event));
    queueMicrotask(() => setState(initial));
    return () => {
      offState();
      offEvent();
      release();
    };
  }, [organizationId, businessId]);

  return state;
}
