"use client";

import { BellIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { PageHeader, Panel } from "@/components/ui/page";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useApiQuery } from "@/hooks/use-api-query";
import { apiRequest } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { toApiError } from "@/lib/format";
import { notificationService } from "@/services/access";
import { useSession } from "@/stores/session-context";
import type { CursorPage, Notification } from "@/types/api";

function NotificationList() {
  const page = useApiQuery(() => notificationService.list(), []);
  const [extra, setExtra] = useState<{ rows: Notification[]; next: string | null } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<ApiError | null>(null);
  const [readOverrides, setReadOverrides] = useState<Record<string, string>>({});

  const rows = [...(page.data?.results ?? []), ...(extra?.rows ?? [])];
  const next = extra ? extra.next : (page.data?.next ?? null);
  const unreadCount = rows.filter((n) => !n.read_at && !readOverrides[n.id]).length;

  async function loadMore() {
    if (!next) return;
    setMoreError(null);
    setLoadingMore(true);
    try {
      const url = new URL(next, window.location.origin);
      const result = await apiRequest<CursorPage<Notification>>(url.pathname + url.search, {
        noBusiness: true,
      });
      setExtra({ rows: [...(extra?.rows ?? []), ...result.results], next: result.next });
    } catch (cause) {
      setMoreError(toApiError(cause));
    } finally {
      setLoadingMore(false);
    }
  }

  function markRead(notification: Notification) {
    if (notification.read_at || readOverrides[notification.id]) return;
    setReadOverrides((prev) => ({ ...prev, [notification.id]: new Date().toISOString() }));
    notificationService.markRead(notification.id).catch(() => undefined);
  }

  function markAllRead() {
    const now = new Date().toISOString();
    setReadOverrides((prev) => {
      const next = { ...prev };
      for (const n of rows) if (!n.read_at) next[n.id] = now;
      return next;
    });
    notificationService.markAllRead().catch(() => undefined);
  }

  return (
    <Panel
      title="Inbox"
      actions={
        unreadCount ? (
          <Button variant="outline" size="sm" onClick={markAllRead}>
            Mark all read ({unreadCount})
          </Button>
        ) : null
      }
    >
      {page.error ? <ErrorState error={page.error} onRetry={page.reload} inline className="m-4" /> : null}
      {page.loading && !page.data ? (
        <div className="flex justify-center p-8 text-sm text-muted-foreground">Loading…</div>
      ) : null}
      {page.data && rows.length === 0 ? (
        <EmptyState
          icon={BellIcon}
          title="No notifications yet"
          description="Conversation assignments, campaign results and account alerts will appear here."
        />
      ) : null}
      {rows.length ? (
        <ul className="divide-y">
          {rows.map((n) => {
            const read = Boolean(n.read_at || readOverrides[n.id]);
            return (
              <li key={n.id}>
                <button
                  type="button"
                  onClick={() => markRead(n)}
                  className="hover:bg-muted flex w-full items-start gap-3 px-4 py-3 text-left"
                >
                  <span
                    aria-hidden
                    className={read ? "mt-1.5 size-1.5 shrink-0 rounded-full" : "bg-primary mt-1.5 size-1.5 shrink-0 rounded-full"}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                      <span className={read ? "text-sm text-muted-foreground" : "text-sm font-medium"}>
                        {n.title}
                      </span>
                      <span className="text-caption shrink-0">{new Date(n.created_at).toLocaleString()}</span>
                    </span>
                    {n.body ? <span className="text-supporting block">{n.body}</span> : null}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
      {next ? (
        <div className="flex justify-center border-t p-3">
          {moreError ? <ErrorState error={moreError} onRetry={loadMore} inline /> : null}
          <Button variant="outline" size="sm" disabled={loadingMore} onClick={() => void loadMore()}>
            {loadingMore ? "Loading…" : "Load more"}
          </Button>
        </div>
      ) : null}
    </Panel>
  );
}

function PreferencesPanel() {
  const page = useApiQuery(() => notificationService.preferences(), []);
  const [muted, setMuted] = useState<Set<string> | null>(null);
  const [saving, setSaving] = useState(false);
  const current = muted ?? new Set(page.data?.muted_kinds ?? []);

  async function toggle(kind: string) {
    const next = new Set(current);
    if (next.has(kind)) next.delete(kind);
    else next.add(kind);
    setMuted(next);
    setSaving(true);
    try {
      await notificationService.updatePreferences([...next]);
    } catch {
      setMuted(current); // revert on failure
    } finally {
      setSaving(false);
    }
  }

  if (!page.data) return null;

  return (
    <Panel title="Preferences" description="Choose which notifications you want to receive.">
      <div className="flex flex-col gap-2 p-4">
        {Object.entries(page.data.available_kinds).map(([code, label]) => (
          <label key={code} className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={!current.has(code)}
              disabled={saving}
              onChange={() => void toggle(code)}
            />
            <span>{label}</span>
          </label>
        ))}
      </div>
    </Panel>
  );
}

export function NotificationsView() {
  const { hasPermission } = useSession();
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Notifications" description="Everything that's happened that needs your attention." />
      <div className="grid gap-5 lg:grid-cols-[2fr_1fr]">
        <NotificationList />
        {hasPermission("notifications.manage") ? <PreferencesPanel /> : null}
      </div>
    </div>
  );
}
