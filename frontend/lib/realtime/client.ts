import type { RealtimeEvent } from "@/types/api";

/**
 * WebSocket client for /ws/v1/realtime/. Every event originates from a
 * committed backend state change; the client never synthesises events.
 * On reconnect, consumers should refetch over REST (events may have been
 * missed while disconnected).
 */

export type ConnectionState = "idle" | "connecting" | "open" | "closed" | "unauthorized";

type Listener = (event: RealtimeEvent) => void;
type StateListener = (state: ConnectionState) => void;

const MAX_BACKOFF_MS = 30_000;
const HEARTBEAT_MS = 25_000;
const AUTH_CLOSE_CODES = new Set([4401, 4403]);

export class RealtimeClient {
  private socket: WebSocket | null = null;
  private attempts = 0;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private stopped = true;
  private listeners = new Set<Listener>();
  private stateListeners = new Set<StateListener>();

  constructor(
    private readonly organizationId: string,
    private readonly businessId: string,
  ) {}

  connect(): void {
    this.stopped = false;
    this.open();
  }

  close(): void {
    this.stopped = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.stopHeartbeat();
    this.socket?.close(1000);
    this.socket = null;
    this.emitState("closed");
  }

  onEvent(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onState(listener: StateListener): () => void {
    this.stateListeners.add(listener);
    return () => this.stateListeners.delete(listener);
  }

  private open(): void {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const query = new URLSearchParams({ organization: this.organizationId, business: this.businessId });
    const url = `${protocol}://${window.location.host}/ws/v1/realtime/?${query.toString()}`;
    this.emitState("connecting");
    const socket = new WebSocket(url);
    this.socket = socket;

    socket.onopen = () => {
      this.attempts = 0;
      this.emitState("open");
      this.heartbeat = setInterval(() => socket.send(JSON.stringify({ type: "ping" })), HEARTBEAT_MS);
    };
    socket.onmessage = (message) => {
      try {
        const event = JSON.parse(String(message.data)) as RealtimeEvent;
        this.listeners.forEach((listener) => listener(event));
      } catch {
        // Ignore malformed frames; the server only sends JSON.
      }
    };
    socket.onclose = (closeEvent) => {
      this.stopHeartbeat();
      if (AUTH_CLOSE_CODES.has(closeEvent.code)) {
        this.emitState("unauthorized");
        return;
      }
      this.emitState("closed");
      if (!this.stopped) this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** this.attempts) * (0.5 + Math.random() / 2);
    this.attempts += 1;
    this.reconnectTimer = setTimeout(() => this.open(), delay);
  }

  private stopHeartbeat(): void {
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = null;
  }

  private current: ConnectionState = "idle";

  get state(): ConnectionState {
    return this.current;
  }

  private emitState(state: ConnectionState): void {
    this.current = state;
    this.stateListeners.forEach((listener) => listener(state));
  }
}

/**
 * One socket per (organization, business) shared by every subscriber on the
 * page (shell notifications, inbox, campaigns …) — reference counted, closed
 * when the last subscriber leaves.
 */
const shared = new Map<string, { client: RealtimeClient; refs: number }>();

export function acquireRealtime(
  organizationId: string,
  businessId: string,
): {
  client: RealtimeClient;
  release: () => void;
} {
  const key = `${organizationId}:${businessId}`;
  let entry = shared.get(key);
  if (!entry) {
    entry = { client: new RealtimeClient(organizationId, businessId), refs: 0 };
    shared.set(key, entry);
    entry.client.connect();
  }
  entry.refs += 1;
  const current = entry;
  let released = false;
  return {
    client: current.client,
    release: () => {
      if (released) return;
      released = true;
      current.refs -= 1;
      if (current.refs <= 0) {
        current.client.close();
        shared.delete(key);
      }
    },
  };
}
