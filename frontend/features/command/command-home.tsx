"use client";

import {
  AlertTriangleIcon,
  ArrowRightIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  CommandIcon,
  CornerDownLeftIcon,
  LockIcon,
  SearchXIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { LoadingButton, Spinner } from "@/components/ui/spinner";
import { ErrorState } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { useApiQuery } from "@/hooks/use-api-query";
import { presetRange } from "@/lib/analytics";
import { ApiError } from "@/lib/api/errors";
import { cn } from "@/lib/utils";
import { analyticsService } from "@/services/analytics";
import { campaignService } from "@/services/campaigns";
import { inboxService } from "@/services/inbox";
import { templateService } from "@/services/templates";
import { whatsappService } from "@/services/whatsapp";
import { useSession } from "@/stores/session-context";

import { OnboardingChecklist } from "../overview/onboarding-checklist";
import { OverviewView } from "../overview/overview-view";
import { parseCommand } from "./parse";
import { runCommand } from "./run";
import type { CommandContext, CommandResult, ParsedCommand } from "./types";

type RunState =
  | { status: "idle" }
  | { status: "running"; text: string }
  | { status: "result"; text: string; result: CommandResult; command: ParsedCommand }
  | { status: "confirming"; text: string; result: CommandResult; command: ParsedCommand }
  | { status: "error"; text: string; error: ApiError; command: ParsedCommand };

interface Suggestion {
  id: string;
  label: string;
  command: string;
  badge?: string;
}

// "Show the business overview" is a per-browser convenience. It lives outside
// React so it can be read during render without a setState-in-effect, and keeps
// working in memory when storage is blocked (private windows, cleared data).
const OVERVIEW_KEY = "console:overview-open";
const OVERVIEW_EVENT = "console:overview-changed";
let overviewInMemory: boolean | null = null;

function subscribeOverview(notify: () => void): () => void {
  window.addEventListener(OVERVIEW_EVENT, notify);
  window.addEventListener("storage", notify);
  return () => {
    window.removeEventListener(OVERVIEW_EVENT, notify);
    window.removeEventListener("storage", notify);
  };
}

function readOverviewOpen(): boolean {
  if (overviewInMemory !== null) return overviewInMemory;
  try {
    return window.localStorage.getItem(OVERVIEW_KEY) === "1";
  } catch {
    return false;
  }
}

function writeOverviewOpen(open: boolean): void {
  overviewInMemory = open;
  try {
    window.localStorage.setItem(OVERVIEW_KEY, open ? "1" : "0");
  } catch {
    // Not persisted; the in-memory value still applies for this page load.
  }
  window.dispatchEvent(new Event(OVERVIEW_EVENT));
}

function asApiError(cause: unknown): ApiError {
  return cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause));
}

function ResultView({
  result,
  onConfirm,
  onCancel,
  confirming,
  confirmError,
}: {
  result: CommandResult;
  onConfirm: () => void;
  onCancel: () => void;
  confirming: boolean;
  confirmError: ApiError | null;
}) {
  const Icon =
    result.kind === "denied"
      ? LockIcon
      : result.kind === "done"
        ? CheckCircle2Icon
        : result.kind === "confirm"
          ? AlertTriangleIcon
          : result.kind === "empty" || result.kind === "unsupported"
            ? SearchXIcon
            : null;
  const iconTone =
    result.kind === "denied" || result.kind === "confirm"
      ? "text-warning"
      : result.kind === "done"
        ? "text-success"
        : "text-muted-foreground";
  const confirmation = result.confirmation;

  return (
    <section
      aria-labelledby="command-result-title"
      className="bg-surface flex flex-col gap-4 rounded-lg border p-4 shadow-xs sm:p-5"
    >
      <header className="flex items-start gap-3">
        {Icon ? <Icon aria-hidden className={cn("mt-0.5 size-5 shrink-0", iconTone)} /> : null}
        <div className="min-w-0">
          <h2 id="command-result-title" tabIndex={-1} className="text-section-title outline-none">
            {result.title}
          </h2>
          {result.summary ? <p className="text-supporting mt-0.5">{result.summary}</p> : null}
        </div>
      </header>

      {result.stats?.length ? (
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {result.stats.map((stat) => (
            <div key={stat.label} className="bg-surface-muted rounded-md border px-3 py-2">
              <dt className="text-caption">{stat.label}</dt>
              <dd
                className={cn(
                  "text-lg font-semibold tracking-tight tabular-nums",
                  stat.tone === "danger" && "text-destructive",
                )}
              >
                {stat.value}
              </dd>
              {stat.hint ? <dd className="text-caption">{stat.hint}</dd> : null}
            </div>
          ))}
        </dl>
      ) : null}

      {result.rows?.length ? (
        <ul className="divide-y rounded-md border">
          {result.rows.map((row) => {
            const body = (
              <>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{row.title}</p>
                  {row.subtitle ? <p className="text-caption truncate">{row.subtitle}</p> : null}
                </div>
                {row.meta ? <span className="text-caption shrink-0 tabular-nums">{row.meta}</span> : null}
                {row.badge ? <StatusBadge tone={row.badge.tone}>{row.badge.text}</StatusBadge> : null}
              </>
            );
            return (
              <li key={row.id}>
                {row.href ? (
                  <Link
                    href={row.href}
                    className="hover:bg-surface-muted flex items-center gap-3 px-3 py-2.5"
                  >
                    {body}
                  </Link>
                ) : (
                  <div className="flex items-center gap-3 px-3 py-2.5">{body}</div>
                )}
              </li>
            );
          })}
        </ul>
      ) : null}

      {confirmation ? (
        <div className="flex flex-col gap-3 rounded-md border border-dashed p-3">
          <ul className="text-supporting flex list-disc flex-col gap-1 pl-5">
            {confirmation.details.map((detail) => (
              <li key={detail}>{detail}</li>
            ))}
          </ul>
          {confirmation.blockedReason ? (
            <p role="alert" className="text-destructive text-sm font-medium">
              Can&apos;t go ahead: {confirmation.blockedReason}
            </p>
          ) : null}
          {confirmError ? <ErrorState error={confirmError} inline /> : null}
          <div className="flex gap-2">
            <LoadingButton
              onClick={onConfirm}
              loading={confirming}
              disabled={Boolean(confirmation.blockedReason)}
              variant={confirmation.destructive ? "destructive" : "default"}
            >
              {confirmation.label}
            </LoadingButton>
            <Button variant="ghost" onClick={onCancel} disabled={confirming}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      {result.notes?.length ? (
        <ul className="text-caption flex flex-col gap-1">
          {result.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}

      {result.links?.length ? (
        <div className="flex flex-wrap gap-3">
          {result.links.map((link) => (
            <Link
              key={link.href + link.label}
              href={link.href}
              className="text-primary inline-flex items-center gap-1 text-sm font-medium hover:underline"
            >
              {link.label} <ArrowRightIcon aria-hidden className="size-3.5" />
            </Link>
          ))}
        </div>
      ) : null}
    </section>
  );
}

/** Intent-first home. Understands a fixed set of commands deterministically
 * (features/command/parse.ts), runs them through the console's own typed
 * services, and keeps the full console one click away. */
export function CommandHome() {
  const router = useRouter();
  const { business, businesses, hasPermission, user } = useSession();
  const [text, setText] = useState("");
  const [state, setState] = useState<RunState>({ status: "idle" });
  const [confirmError, setConfirmError] = useState<ApiError | null>(null);
  const [confirming, setConfirming] = useState(false);
  const generation = useRef(0);
  const resultRef = useRef<HTMLDivElement>(null);

  const overviewOpen = useSyncExternalStore(subscribeOverview, readOverviewOpen, () => false);
  const toggleOverview = () => writeOverviewOpen(!overviewOpen);

  const ctx: CommandContext = useMemo(
    () => ({ business, businesses, hasPermission }),
    [business, businesses, hasPermission],
  );

  // ---- context for suggestions: the same real reads the overview uses ------
  const enabled = Boolean(business);
  const today = presetRange("today", business?.timezone || undefined);
  const numbers = useApiQuery(
    () => whatsappService.numbers(),
    [business?.id],
    enabled && hasPermission("numbers.view"),
  );
  const overview = useApiQuery(
    () => analyticsService.overview(today),
    [business?.id, today],
    enabled && hasPermission("analytics.view"),
  );
  const campaigns = useApiQuery(
    () => campaignService.list(),
    [business?.id],
    enabled && hasPermission("campaigns.view"),
  );
  const templates = useApiQuery(
    () => templateService.list(),
    [business?.id],
    enabled && hasPermission("templates.view"),
  );
  const unread = useApiQuery(
    () => inboxService.conversations({ unread: "1" }),
    [business?.id],
    enabled && hasPermission("conversations.view"),
  );

  const suggestions: Suggestion[] = useMemo(() => {
    const out: Suggestion[] = [];
    if (hasPermission("analytics.view")) {
      const failed = overview.data?.totals.failed ?? 0;
      out.push({
        id: "perf",
        label: "Today's delivery performance",
        command: "Show today's message delivery performance",
      });
      if (failed > 0) {
        out.push({
          id: "failed",
          label: "Messages that failed",
          command: "Find messages that failed today",
          badge: String(failed),
        });
      }
    }
    if (hasPermission("conversations.view") && (unread.data?.results.length ?? 0) > 0) {
      out.push({
        id: "unread",
        label: "Unread conversations",
        command: "Show unread conversations",
        badge: `${unread.data!.results.length}${unread.data!.next ? "+" : ""}`,
      });
    }
    if (hasPermission("campaigns.view")) {
      const running = (campaigns.data?.results ?? []).filter(
        (c) => c.status === "running" || c.status === "queued",
      ).length;
      if (running > 0) {
        out.push({
          id: "running",
          label: "Campaigns running now",
          command: "Show campaigns currently running",
          badge: String(running),
        });
      }
    }
    if (hasPermission("templates.view")) {
      const attention = (templates.data?.results ?? []).filter(
        (t) => t.missing_from_provider || ["rejected", "paused", "disabled"].includes(t.status.toLowerCase()),
      ).length;
      if (attention > 0) {
        out.push({
          id: "templates",
          label: "Templates needing attention",
          command: "Find templates that need attention",
          badge: String(attention),
        });
      }
    }
    if (hasPermission("analytics.view") && businesses.filter((b) => b.status === "active").length > 1) {
      out.push({
        id: "costs",
        label: "Compare costs across businesses",
        command: "Compare messaging costs across my businesses",
      });
    }
    return out;
  }, [hasPermission, overview.data, unread.data, campaigns.data, templates.data, businesses]);

  const execute = useCallback(
    async (input: string) => {
      const value = input.trim();
      if (!value) return;
      const run = ++generation.current;
      const command = parseCommand(value);
      setConfirmError(null);
      setState({ status: "running", text: value });
      try {
        const result = await runCommand(command, ctx);
        if (run !== generation.current) return;
        if (result.navigateTo) {
          router.push(result.navigateTo);
          return;
        }
        setState({
          status: result.kind === "confirm" ? "confirming" : "result",
          text: value,
          result,
          command,
        });
      } catch (cause) {
        if (run !== generation.current) return;
        setState({ status: "error", text: value, error: asApiError(cause), command });
      }
    },
    [ctx, router],
  );

  // Move focus to the result so keyboard and screen-reader users land on it.
  useEffect(() => {
    if (state.status === "result" || state.status === "confirming" || state.status === "error") {
      resultRef.current?.querySelector<HTMLElement>("#command-result-title, [role=alert]")?.focus?.();
    }
  }, [state.status]);

  async function confirm() {
    if (state.status !== "confirming" || !state.result.confirmation) return;
    const { text: original, command } = state;
    const run = ++generation.current;
    setConfirmError(null);
    setState({ ...state, status: "confirming" });
    setConfirming(true);
    try {
      const done = await state.result.confirmation.run();
      if (run !== generation.current) return;
      setState({ status: "result", text: original, result: done, command });
    } catch (cause) {
      if (run !== generation.current) return;
      setConfirmError(asApiError(cause));
    } finally {
      setConfirming(false);
    }
  }
  function reset() {
    generation.current++;
    setState({ status: "idle" });
    setConfirmError(null);
  }

  const greeting = user?.full_name ? user.full_name.split(" ")[0] : null;

  return (
    <div className="flex flex-col gap-6">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 pt-2 sm:pt-6">
        <div>
          <p className="text-caption">{business ? business.name : "No business selected"}</p>
          <h1 className="text-page-title mt-0.5">
            {greeting ? `${greeting}, what would you like to do?` : "What would you like to do?"}
          </h1>
        </div>

        <form
          role="search"
          aria-label="Ask the console"
          onSubmit={(e) => {
            e.preventDefault();
            void execute(text);
          }}
          className="flex flex-col gap-2 sm:flex-row"
        >
          <div className="relative flex-1">
            <CommandIcon
              aria-hidden
              className="text-muted-foreground pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2"
            />
            <label htmlFor="command-input" className="sr-only">
              Type a command or a question
            </label>
            <Input
              id="command-input"
              autoFocus
              autoComplete="off"
              maxLength={200}
              placeholder="Try “Show today's delivery performance”"
              className="h-12 pl-10 text-base"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <LoadingButton
            type="submit"
            size="lg"
            className="h-12 px-5"
            loading={state.status === "running"}
            disabled={!text.trim()}
          >
            Run <CornerDownLeftIcon aria-hidden />
          </LoadingButton>
        </form>

        {suggestions.length > 0 && state.status !== "running" ? (
          <div className="flex flex-col gap-2">
            <p className="text-caption">Suggested for {business?.name ?? "you"}</p>
            <ul className="flex flex-wrap gap-2">
              {suggestions.map((s) => (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setText(s.command);
                      void execute(s.command);
                    }}
                    className="bg-surface hover:bg-surface-muted focus-visible:ring-ring/50 inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm outline-none focus-visible:ring-2"
                  >
                    {s.label}
                    {s.badge ? (
                      <span className="bg-brand-soft text-primary rounded-full px-1.5 text-xs font-medium tabular-nums">
                        {s.badge}
                      </span>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <div ref={resultRef} aria-live="polite" aria-busy={state.status === "running"}>
          {state.status === "running" ? (
            <div className="bg-surface text-supporting flex items-center gap-3 rounded-lg border p-4">
              <Spinner /> Working on “{state.text}”…
            </div>
          ) : null}
          {state.status === "error" ? (
            <div className="flex flex-col gap-2">
              <ErrorState error={state.error} onRetry={() => void execute(state.text)} inline />
              <button
                type="button"
                className="text-caption text-muted-foreground w-fit hover:underline"
                onClick={reset}
              >
                Dismiss
              </button>
            </div>
          ) : null}
          {state.status === "result" || state.status === "confirming" ? (
            <div className="flex flex-col gap-2">
              <ResultView
                result={state.result}
                onConfirm={() => void confirm()}
                onCancel={reset}
                confirming={confirming}
                confirmError={confirmError}
              />
              {state.status === "result" ? (
                <button
                  type="button"
                  className="text-caption text-muted-foreground w-fit hover:underline"
                  onClick={reset}
                >
                  Clear
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
        <OnboardingChecklist numbers={numbers.data?.results} />
        <button
          type="button"
          aria-expanded={overviewOpen}
          aria-controls="business-overview"
          onClick={toggleOverview}
          className="text-supporting hover:text-foreground inline-flex w-fit items-center gap-1.5 text-sm font-medium"
        >
          <ChevronDownIcon
            aria-hidden
            className={cn("size-4 transition-transform", !overviewOpen && "-rotate-90")}
          />
          Business overview
        </button>
      </div>
      {overviewOpen ? (
        <div id="business-overview">
          <OverviewView hideOnboarding />
        </div>
      ) : null}
    </div>
  );
}
