"use client";

import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  CircleHelpIcon,
  OctagonAlertIcon,
  RefreshCwIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Panel } from "@/components/ui/page";
import { StatusBadge } from "@/components/ui/status-badge";
import { LoadingButton } from "@/components/ui/spinner";
import { ErrorState } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError } from "@/lib/api/errors";
import { cn } from "@/lib/utils";
import { whatsappService } from "@/services/whatsapp";
import { useSession } from "@/stores/session-context";
import type { ConnectionHealth, ConnectionHealthCheck, ConnectionHealthState } from "@/types/api";

import {
  OVERALL_LABEL,
  OVERALL_TONE,
  STATE_LABEL,
  checkedAtSnapshot,
  fixWhere,
  refreshTookEffect,
  sortChecks,
} from "./model";

const STATE_ICON = {
  ok: CheckCircle2Icon,
  warning: AlertTriangleIcon,
  problem: OctagonAlertIcon,
  unknown: CircleHelpIcon,
} satisfies Record<ConnectionHealthState, unknown>;

const STATE_COLOUR: Record<ConnectionHealthState, string> = {
  ok: "text-success",
  warning: "text-warning",
  problem: "text-destructive",
  unknown: "text-muted-foreground",
};

export function CheckRow({ check }: { check: ConnectionHealthCheck }) {
  const Icon = STATE_ICON[check.state];
  const where = fixWhere(check);
  return (
    <li className="flex gap-3 py-2.5">
      <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", STATE_COLOUR[check.state])} />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">
          {check.title}
          <span className="sr-only"> — {STATE_LABEL[check.state]}</span>
        </p>
        <p className="text-supporting">{check.detail}</p>
        {check.next_step ? (
          <p className="mt-1 text-sm">
            <span className="font-medium">What to do: </span>
            {check.next_step}
          </p>
        ) : null}
        <p className="text-caption mt-1 flex flex-wrap gap-x-3">
          {where ? <span>{where}</span> : null}
          {check.basis === "console" ? (
            <span title="This is a rule of this console, not a status from Meta.">Console rule</span>
          ) : null}
        </p>
      </div>
    </li>
  );
}

function CheckList({ checks }: { checks: ConnectionHealthCheck[] }) {
  const sorted = sortChecks(checks);
  const attention = sorted.filter((c) => c.state !== "ok");
  const fine = sorted.filter((c) => c.state === "ok");
  return (
    <div>
      {attention.length ? (
        <ul className="divide-y">
          {attention.map((c) => (
            <CheckRow key={c.code} check={c} />
          ))}
        </ul>
      ) : null}
      {fine.length ? (
        <details className={cn(attention.length && "mt-1")}>
          <summary className="text-supporting hover:text-foreground cursor-pointer py-1.5 text-sm">
            {fine.length} thing{fine.length === 1 ? "" : "s"} {attention.length ? "that are" : "that look"}{" "}
            fine
          </summary>
          <ul className="divide-y">
            {fine.map((c) => (
              <CheckRow key={c.code} check={c} />
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "never";
}

/**
 * Refresh from Meta, then POLL the health endpoint until every account and
 * number reports a later "last checked" time. The UI says "updated" only when
 * that really happened; otherwise it says it is still waiting.
 */
export function useRefreshFromMeta(
  health: ConnectionHealth | undefined,
  onData: (fresh: ConnectionHealth) => void,
) {
  const toast = useToast();
  const [refreshing, setRefreshing] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    if (!health || health.accounts.length === 0) return;
    setRefreshing(true);
    const before = checkedAtSnapshot(health);
    try {
      await Promise.all(
        health.accounts.flatMap((a) => [
          whatsappService.requestVerify(a.id),
          whatsappService.requestSync(a.id),
        ]),
      );
      for (let attempt = 0; attempt < 10; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 2500));
        if (!alive.current) return;
        const fresh = await whatsappService.health();
        onData(fresh);
        if (refreshTookEffect(before, fresh)) {
          toast.success("Updated from Meta", "The status below is what Meta reported just now.");
          return;
        }
      }
      toast.info(
        "Still waiting for Meta",
        "The refresh was requested but Meta has not answered yet. Check again in a minute.",
      );
    } catch (cause) {
      const error = cause instanceof ApiError ? cause.message : "Something went wrong.";
      toast.error("Couldn't refresh", error);
    } finally {
      if (alive.current) setRefreshing(false);
    }
  }, [health, onData, toast]);

  return { refresh, refreshing };
}

/** Full health view for the Numbers page. */
export function HealthPanel() {
  const { business, hasPermission } = useSession();
  const [data, setData] = useState<ConnectionHealth | undefined>();
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const canRefresh = hasPermission("numbers.manage");

  const load = useCallback(async () => {
    try {
      setData(await whatsappService.health());
      setError(null);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause)));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount / business change
    void load();
  }, [load, business?.id]);

  const { refresh, refreshing } = useRefreshFromMeta(data, setData);

  if (loading) {
    return (
      <Panel title="Health">
        <p className="text-supporting p-4">Checking…</p>
      </Panel>
    );
  }
  if (error) {
    return (
      <Panel title="Health">
        <div className="p-4">
          <ErrorState error={error} onRetry={() => void load()} inline />
        </div>
      </Panel>
    );
  }
  if (!data) return null;

  return (
    <Panel
      title="Health"
      description={data.summary}
      actions={
        <span className="flex items-center gap-2">
          <StatusBadge tone={OVERALL_TONE[data.overall]}>{OVERALL_LABEL[data.overall]}</StatusBadge>
          {canRefresh && data.accounts.length ? (
            <LoadingButton size="sm" variant="outline" loading={refreshing} onClick={() => void refresh()}>
              <RefreshCwIcon aria-hidden /> Refresh from Meta
            </LoadingButton>
          ) : null}
        </span>
      }
      bodyClassName="p-4"
    >
      <div className="flex flex-col gap-5">
        {data.recent_alerts.length ? (
          <div role="region" aria-label="Recent alerts from Meta" className="rounded-md border px-3 py-2">
            <p className="text-sm font-medium">Recent alerts from Meta</p>
            <ul className="mt-1 flex flex-col gap-1">
              {data.recent_alerts.map((alert) => (
                <li key={alert.id} className="text-supporting">
                  <StatusBadge tone={alert.severity === "critical" ? "danger" : "warning"}>
                    {alert.severity}
                  </StatusBadge>{" "}
                  {alert.summary || "Meta sent an alert."}{" "}
                  <span className="text-caption">{when(alert.occurred_at)}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {data.numbers.map((n) => (
          <section key={n.id} aria-label={`Health of ${n.display_phone_number}`}>
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <h3 className="text-section-title">{n.label || n.verified_name || n.display_phone_number}</h3>
              <span className="text-caption">{n.display_phone_number}</span>
              <StatusBadge tone={OVERALL_TONE[n.overall]}>{OVERALL_LABEL[n.overall]}</StatusBadge>
              <span className="text-caption">Checked with Meta: {when(n.last_checked_at)}</span>
            </div>
            <CheckList checks={n.checks} />
          </section>
        ))}

        {data.accounts.map((a) => (
          <section key={a.id} aria-label={`Health of ${a.name || "WhatsApp account"}`}>
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <h3 className="text-section-title">{a.name || "WhatsApp Business Account"}</h3>
              <span className="text-caption">Account</span>
              <StatusBadge tone={OVERALL_TONE[a.overall]}>{OVERALL_LABEL[a.overall]}</StatusBadge>
              <span className="text-caption">Checked with Meta: {when(a.last_checked_at)}</span>
            </div>
            <CheckList checks={a.checks} />
          </section>
        ))}

        <ul className="text-caption flex flex-col gap-1">
          {data.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </div>
    </Panel>
  );
}
