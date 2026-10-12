"use client";

import { ExternalLinkIcon, RotateCcwIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PageHeader, Panel } from "@/components/ui/page";
import { Spinner, LoadingButton } from "@/components/ui/spinner";
import { StatusBadge } from "@/components/ui/status-badge";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError } from "@/lib/api/errors";
import { recoveryService } from "@/services/recovery";
import { useSession } from "@/stores/session-context";
import { useUiMode } from "@/stores/ui-mode";
import type { FailedMessage, FailedMessagesResponse, FailureDetail } from "@/types/api";

import { KIND_LABEL, KIND_TONE, costLine, describeAttempt, hasPending, recipientLabel } from "./model";

const POLL_MS = 5000;

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "";
}

function toApiError(cause: unknown): ApiError {
  return cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause));
}

/** Review + confirm dialog: shows the real preview from the server, sends nothing until confirmed. */
function RetryDialog({
  row,
  onClose,
  onDone,
}: {
  row: FailedMessage;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [detail, setDetail] = useState<FailureDetail | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let live = true;
    recoveryService
      .detail(row.id)
      .then((d) => live && setDetail(d))
      .catch((cause) => live && setError(toApiError(cause)));
    return () => {
      live = false;
    };
  }, [row.id]);

  const preview = detail?.retry_preview ?? null;

  async function confirm() {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const response = await recoveryService.retry(row.id);
      toast.info("Retry queued", response.status_note);
      onDone();
      onClose();
    } catch (cause) {
      const err = toApiError(cause);
      setSubmitError(err.message);
      if (err.status === 409) onDone(); // the situation changed: show the current state behind the dialog
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Retry this message?</DialogTitle>
          <DialogDescription>
            Check the details below. Nothing is sent until you confirm.
          </DialogDescription>
        </DialogHeader>

        {!detail && !error ? (
          <p className="text-supporting flex items-center gap-2" role="status">
            <Spinner /> Loading the details…
          </p>
        ) : null}
        {error ? <ErrorState error={error} inline /> : null}
        {detail && !preview ? (
          <p role="alert" className="text-sm">
            {detail.recovery.blockers[0]?.message ?? "This message can't be retried."}
          </p>
        ) : null}
        {preview ? (
          <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-2 text-sm">
            <dt className="text-muted-foreground">To</dt>
            <dd>
              {preview.destination.name ? `${preview.destination.name} · ` : ""}
              {preview.destination.phone_number}
            </dd>
            <dt className="text-muted-foreground">From</dt>
            <dd>
              {preview.destination.from_label ? `${preview.destination.from_label} · ` : ""}
              {preview.destination.from_number}
            </dd>
            <dt className="text-muted-foreground">Message</dt>
            <dd className="break-words whitespace-pre-wrap">{preview.message_text}</dd>
            <dt className="text-muted-foreground">Duplicates</dt>
            <dd>{preview.duplicate_risk.text}</dd>
            <dt className="text-muted-foreground">Cost</dt>
            <dd>{costLine(preview.cost)}</dd>
          </dl>
        ) : null}
        {submitError ? (
          <p role="alert" className="text-destructive text-sm">
            {submitError}
          </p>
        ) : null}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          {preview ? (
            <LoadingButton loading={submitting} onClick={() => void confirm()}>
              Confirm and retry
            </LoadingButton>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function FailureCard({
  row,
  canSend,
  advanced,
  onRetry,
}: {
  row: FailedMessage;
  canSend: boolean;
  advanced: boolean;
  onRetry: (row: FailedMessage) => void;
}) {
  const r = row.recovery;
  const attempt = r.retried_as ? describeAttempt(r.retried_as) : null;
  const name = recipientLabel(row);
  return (
    <li className="px-4 py-3">
      <article aria-label={`Failed message to ${name}`} className="flex flex-col gap-2">
        <header className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium">{name}</h3>
          {row.recipient.name ? <span className="text-caption">{row.recipient.phone_number}</span> : null}
          <StatusBadge tone={KIND_TONE[r.kind]}>{KIND_LABEL[r.kind]}</StatusBadge>
          <span className="text-caption ml-auto">Failed {when(row.failed_at ?? row.occurred_at)}</span>
        </header>

        {row.preview ? (
          <p className="text-supporting line-clamp-2 break-words">&ldquo;{row.preview}&rdquo;</p>
        ) : null}

        <p className="text-sm">{r.explanation}</p>
        {r.safe_error ? (
          <p className="text-caption">
            What WhatsApp reported: {r.safe_error}
            {advanced ? ` (${r.label}${r.provider_code ? ` · code ${r.provider_code}` : ""})` : ""}
          </p>
        ) : null}
        <p className="text-sm">
          <span className="font-medium">What to do: </span>
          {r.next_step}
        </p>

        {r.links.length && (r.kind === "external" || advanced) ? (
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {r.links.map((link) => (
              <li key={link.url}>
                <a
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary inline-flex items-center gap-1 text-sm font-medium hover:underline"
                >
                  {link.label} <ExternalLinkIcon aria-hidden className="size-3.5" />
                  {" "}<span className="sr-only">(opens in a new tab)</span>
                </a>
              </li>
            ))}
          </ul>
        ) : null}

        {attempt && r.retried_as ? (
          <div role="status" className="bg-muted/50 rounded-md border px-3 py-2">
            <p className="text-sm">
              <StatusBadge tone={attempt.tone}>{attempt.label}</StatusBadge>{" "}
              <span className="text-caption">Retry requested {when(r.retried_as.created_at)}</span>
            </p>
            <p className="text-supporting mt-1">{attempt.text}</p>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-3">
          {r.can_retry && canSend ? (
            <Button size="sm" variant="outline" onClick={() => onRetry(row)}>
              <RotateCcwIcon aria-hidden /> Review retry
              {" "}<span className="sr-only">for {name}</span>
            </Button>
          ) : null}
          {r.can_retry && !canSend ? (
            <span className="text-caption">You don&apos;t have permission to send, so you can&apos;t retry.</span>
          ) : null}
          {!r.can_retry && !r.retried_as && r.blockers.length ? (
            <span className="text-caption">Why no retry: {r.blockers[0].message}</span>
          ) : null}
          <Link href="/inbox" className="text-primary text-sm hover:underline">
            Open inbox
            {" "}<span className="sr-only">to check the conversation with {name}</span>
          </Link>
        </div>
      </article>
    </li>
  );
}

export function FailedMessagesView() {
  const { business, hasPermission } = useSession();
  const { mode } = useUiMode();
  const [days, setDays] = useState(7);
  const [data, setData] = useState<FailedMessagesResponse | undefined>();
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [reviewing, setReviewing] = useState<FailedMessage | null>(null);
  const alive = useRef(true);
  const canSend = hasPermission("messages.send");

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    try {
      const fresh = await recoveryService.failures({ days, limit: 50 });
      if (!alive.current) return;
      setData(fresh);
      setError(null);
    } catch (cause) {
      if (alive.current) setError(toApiError(cause));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount / filter / business change
    void load();
  }, [load, business?.id]);

  // While a retry is still in flight, ask again: the status shown is only ever what the server stored.
  const pending = data ? hasPending(data.results) : false;
  useEffect(() => {
    if (!pending) return;
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [pending, load]);

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Failed messages"
        description="Why messages did not go through, and the safe next step for each."
        actions={
          <label className="text-supporting flex items-center gap-2 text-sm">
            Show
            <select
              className="bg-background h-8 rounded-md border px-2 text-sm"
              value={days}
              onChange={(event) => {
                setLoading(true);
                setDays(Number(event.target.value));
              }}
            >
              <option value={1}>last 24 hours</option>
              <option value={7}>last 7 days</option>
              <option value={30}>last 30 days</option>
            </select>
          </label>
        }
      />

      {loading ? (
        <p className="text-supporting" role="status">
          Loading failed messages…
        </p>
      ) : error ? (
        <ErrorState error={error} onRetry={() => void load()} />
      ) : data && data.results.length === 0 ? (
        <Panel>
          <EmptyState
            title="No failed messages"
            description="Nothing you sent failed in this period. If a message fails, it will appear here with what to do next."
          />
        </Panel>
      ) : data ? (
        <Panel
          title={`${data.total} failed message${data.total === 1 ? "" : "s"}`}
          description={
            data.total > data.shown
              ? `Showing the latest ${data.shown}.`
              : "Retry is only offered when it is safe: the message was certainly not sent."
          }
        >
          <ul className="divide-y">
            {data.results.map((row) => (
              <FailureCard
                key={row.id}
                row={row}
                canSend={canSend}
                advanced={mode === "advanced"}
                onRetry={setReviewing}
              />
            ))}
          </ul>
        </Panel>
      ) : null}

      {reviewing ? (
        <RetryDialog row={reviewing} onClose={() => setReviewing(null)} onDone={() => void load()} />
      ) : null}
    </div>
  );
}
