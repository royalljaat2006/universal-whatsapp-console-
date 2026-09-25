"use client";

import { BellIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import { StatusBadge } from "@/components/ui/status-badge";
import { useBusinessRealtime } from "@/hooks/use-business-realtime";
import { toApiError } from "@/lib/format";
import type { ApiError } from "@/lib/api/errors";
import { eventService } from "@/services/access";
import { useSession } from "@/stores/session-context";
import type { AccountEvent } from "@/types/api";

const TONE = { critical: "danger", warning: "warning", info: "neutral" } as const;

/**
 * Account notifications = real WhatsApp account events Meta sent for this
 * business (quality, limits, alerts, account updates). The unread dot counts
 * warning/critical alerts that arrived over realtime since the page opened.
 */
export function Notifications() {
  const { active, business, hasPermission } = useSession();
  const [open, setOpen] = useState(false);
  const [events, setEvents] = useState<AccountEvent[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [unread, setUnread] = useState(0);
  const allowed = hasPermission("numbers.view");

  useBusinessRealtime(allowed ? active?.organization.id : undefined, allowed ? business?.id : undefined, {
    onEvent: (event) => {
      if (event.event !== "account.alert") return;
      const data = event.data as { severity?: string } | undefined;
      if (data?.severity !== "info") setUnread((n) => n + 1);
      setEvents(null);
    },
    onResync: () => setEvents(null),
  });

  if (!allowed || !business) return null;

  function load() {
    setError(null);
    eventService
      .list()
      .then((page) => setEvents(page.results.slice(0, 8)))
      .catch((cause) => setError(toApiError(cause)));
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setUnread(0);
          load();
        }
      }}
    >
      <PopoverTrigger
        render={
          <Button
            variant="ghost"
            size="icon"
            aria-label={unread ? `Notifications, ${unread} new` : "Notifications"}
            className="relative"
          />
        }
      >
        <BellIcon aria-hidden />
        {unread ? (
          <span aria-hidden className="bg-destructive absolute top-1.5 right-1.5 size-2 rounded-full" />
        ) : null}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="border-b px-3 py-2">
          <p className="text-sm font-medium">Account notifications</p>
          <p className="text-caption">WhatsApp account events for {business.name}</p>
        </div>
        <div className="max-h-80 overflow-y-auto">
          {error ? <p className="text-destructive p-3 text-xs">{error.message}</p> : null}
          {!events && !error ? (
            <div className="flex justify-center p-4">
              <Spinner />
            </div>
          ) : null}
          {events && events.length === 0 ? (
            <p className="text-supporting p-4 text-center">No account events from Meta yet.</p>
          ) : null}
          <ul className="divide-y">
            {events?.map((e) => (
              <li key={e.id} className="flex flex-col gap-1 px-3 py-2.5">
                <span className="flex items-center justify-between gap-2">
                  <StatusBadge tone={TONE[e.severity]}>{e.severity}</StatusBadge>
                  <span className="text-caption">{new Date(e.occurred_at).toLocaleString()}</span>
                </span>
                <span className="text-sm">{e.summary || e.event}</span>
                <span className="text-caption font-mono">{e.field}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="border-t px-3 py-2">
          <Link
            href="/numbers"
            className="text-primary text-xs font-medium hover:underline"
            onClick={() => setOpen(false)}
          >
            View all account events
          </Link>
        </div>
      </PopoverContent>
    </Popover>
  );
}
