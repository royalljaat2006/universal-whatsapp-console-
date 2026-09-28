"use client";

import { BellIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import { useBusinessRealtime } from "@/hooks/use-business-realtime";
import { toApiError } from "@/lib/format";
import type { ApiError } from "@/lib/api/errors";
import { notificationService } from "@/services/access";
import { useSession } from "@/stores/session-context";
import type { Notification } from "@/types/api";

/**
 * In-app notifications: a personal inbox (conversation assignments, campaign
 * results, account alerts, …) backed by apps.notifications on the backend.
 * Delivered over the same realtime socket as business events — the consumer
 * joins this membership's own group in addition to the business group.
 */
export function Notifications() {
  const { active, business, hasPermission } = useSession();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notification[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [unread, setUnread] = useState(0);
  const allowed = hasPermission("notifications.view");

  useEffect(() => {
    if (!allowed) return;
    notificationService
      .unreadCount()
      .then((r) => setUnread(r.unread_count))
      .catch(() => undefined);
  }, [allowed, active?.organization.id]);

  useBusinessRealtime(allowed ? active?.organization.id : undefined, allowed ? business?.id : undefined, {
    onEvent: (event) => {
      if (event.event !== "notification.created") return;
      setUnread((n) => n + 1);
      setItems(null);
    },
    onResync: () => {
      notificationService
        .unreadCount()
        .then((r) => setUnread(r.unread_count))
        .catch(() => undefined);
    },
  });

  if (!allowed) return null;

  function load() {
    setError(null);
    notificationService
      .list()
      .then((page) => setItems(page.results.slice(0, 8)))
      .catch((cause) => setError(toApiError(cause)));
  }

  function markRead(notification: Notification) {
    if (notification.read_at) return;
    setItems((prev) =>
      prev?.map((n) => (n.id === notification.id ? { ...n, read_at: new Date().toISOString() } : n)) ?? prev,
    );
    setUnread((n) => Math.max(0, n - 1));
    notificationService.markRead(notification.id).catch(() => undefined);
  }

  function markAllRead() {
    setItems((prev) => prev?.map((n) => ({ ...n, read_at: n.read_at ?? new Date().toISOString() })) ?? prev);
    setUnread(0);
    notificationService.markAllRead().catch(() => undefined);
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) load();
      }}
    >
      <PopoverTrigger
        render={
          <Button
            variant="ghost"
            size="icon"
            aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
            className="relative"
          />
        }
      >
        <BellIcon aria-hidden />
        {unread ? (
          <span
            aria-hidden
            className="bg-destructive text-destructive-foreground absolute top-0.5 right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-medium"
          >
            {unread > 9 ? "9+" : unread}
          </span>
        ) : null}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
          <p className="text-sm font-medium">Notifications</p>
          {items?.some((n) => !n.read_at) ? (
            <button
              type="button"
              onClick={markAllRead}
              className="text-primary text-xs font-medium hover:underline"
            >
              Mark all read
            </button>
          ) : null}
        </div>
        <div className="max-h-80 overflow-y-auto">
          {error ? <p className="text-destructive p-3 text-xs">{error.message}</p> : null}
          {!items && !error ? (
            <div className="flex justify-center p-4">
              <Spinner />
            </div>
          ) : null}
          {items && items.length === 0 ? (
            <p className="text-supporting p-4 text-center">You&rsquo;re all caught up.</p>
          ) : null}
          <ul className="divide-y">
            {items?.map((n) => (
              <li key={n.id}>
                <button
                  type="button"
                  onClick={() => markRead(n)}
                  className="hover:bg-muted flex w-full flex-col gap-0.5 px-3 py-2.5 text-left"
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5">
                      {n.read_at ? null : (
                        <span aria-hidden className="bg-primary size-1.5 shrink-0 rounded-full" />
                      )}
                      <span className="text-sm font-medium">{n.title}</span>
                    </span>
                    <span className="text-caption shrink-0">{new Date(n.created_at).toLocaleString()}</span>
                  </span>
                  {n.body ? <span className="text-caption line-clamp-2">{n.body}</span> : null}
                </button>
              </li>
            ))}
          </ul>
        </div>
        <div className="border-t px-3 py-2">
          <Link
            href="/notifications"
            className="text-primary text-xs font-medium hover:underline"
            onClick={() => setOpen(false)}
          >
            View all notifications
          </Link>
        </div>
      </PopoverContent>
    </Popover>
  );
}
