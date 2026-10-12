"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import { LoadingButton } from "@/components/ui/spinner";
import { useToast } from "@/components/ui/toast";
import { toApiError } from "@/lib/format";
import { contactService } from "@/services/contacts";
import { whatsappService } from "@/services/whatsapp";
import type { Contact, SetupStatus, TestMessagePreview, TestMessageStatus } from "@/types/api";

import { describeTestStatus } from "./model";

const POLL_MS = 4000;
const MAX_POLLS = 45; // about three minutes, then say honestly that it is still unconfirmed

type Phase =
  | { name: "form" }
  | { name: "preview"; preview: TestMessagePreview }
  | { name: "sent"; messageId: string; note: string };

function costText(cost: TestMessagePreview["cost"]): string {
  if (cost.status === "estimate" && cost.amount) {
    return `Estimated ${cost.currency ?? ""} ${cost.amount}. ${cost.note}`.replace(/\s+/g, " ").trim();
  }
  if (cost.status === "free") return `No charge. ${cost.note}`;
  return `Cost unknown. ${cost.note}`;
}

/** Tracks one test message by reading its own stored status until WhatsApp reports an end state. */
function Tracker({ messageId, note, onFinal }: { messageId: string; note: string; onFinal: () => void }) {
  const [status, setStatus] = useState<TestMessageStatus | undefined>();
  const [polls, setPolls] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const finished = useRef(false);

  const check = useCallback(async () => {
    try {
      const fresh = await whatsappService.testMessageStatus(messageId);
      setStatus(fresh);
      setError(null);
      if (describeTestStatus(fresh.status).final && !finished.current) {
        finished.current = true;
        onFinal();
      }
    } catch (cause) {
      setError(toApiError(cause).message);
    }
    setPolls((n) => n + 1);
  }, [messageId, onFinal]);

  const final = status ? describeTestStatus(status.status).final : false;
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- first read on mount
    void check();
  }, [check]);
  useEffect(() => {
    if (final || polls >= MAX_POLLS) return;
    const id = setTimeout(() => void check(), POLL_MS);
    return () => clearTimeout(id);
  }, [final, polls, check]);

  const view = status ? describeTestStatus(status.status, status.error_message) : null;
  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-2 rounded-md border px-3 py-3">
      <p className="text-sm font-medium">Your test message</p>
      {view ? (
        <p>
          <StatusBadge tone={view.tone}>{view.label}</StatusBadge>{" "}
          <span className="text-supporting">{view.text}</span>
        </p>
      ) : (
        <p className="text-supporting">Checking its status…</p>
      )}
      {error ? <p className="text-destructive text-sm">Couldn&apos;t read its status: {error}</p> : null}
      {!final && polls >= MAX_POLLS ? (
        <p className="text-supporting">
          WhatsApp has not reported it yet. It is not confirmed delivered. Check again in a minute.
        </p>
      ) : null}
      {status?.status === "failed" ? (
        <Link href="/failed-messages" className="text-primary w-fit text-sm font-medium hover:underline">
          See why it failed and what to do
        </Link>
      ) : null}
      <p className="text-caption">{note}</p>
    </div>
  );
}

export function TestMessagePanel({
  setup,
  canSend,
  onChanged,
}: {
  setup: SetupStatus;
  canSend: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const usable = setup.numbers.filter((n) => n.status === "connected");
  const [phase, setPhase] = useState<Phase>({ name: "form" });
  const [numberId, setNumberId] = useState("");
  const [mode, setMode] = useState<"contact" | "number">("number");
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [contactId, setContactId] = useState("");
  const [phoneInput, setPhoneInput] = useState("");
  const [attest, setAttest] = useState(false);
  const [approved, setApproved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sender = numberId || usable[0]?.id || "";

  useEffect(() => {
    let live = true;
    contactService
      .list({})
      .then((page) => live && setContacts(page.results.filter((c) => c.opt_in_status === "opted_in" && c.phone_number)))
      .catch(() => live && setContacts([]));
    return () => {
      live = false;
    };
  }, []);

  if (!canSend) {
    return (
      <p className="text-supporting">
        You don&apos;t have permission to send messages, so you can&apos;t send a test. Ask an administrator.
      </p>
    );
  }
  if (usable.length === 0) {
    return <p className="text-supporting">Connect a number first. A test message needs a connected number.</p>;
  }

  async function preview() {
    setBusy(true);
    setError(null);
    try {
      const result = await whatsappService.previewTestMessage({
        phone_number_id: sender,
        ...(mode === "contact" && contactId ? { contact_id: contactId } : { to: phoneInput }),
        attest_consent: mode === "number" ? attest : false,
      });
      setApproved(false);
      setPhase({ name: "preview", preview: result });
    } catch (cause) {
      setError(toApiError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  async function cancel(preview: TestMessagePreview) {
    if (preview.approval) {
      try {
        await whatsappService.cancelTestMessage(preview.approval.token);
      } catch {
        // The approval simply expires on its own; nothing was sent either way.
      }
    }
    setPhase({ name: "form" });
    setApproved(false);
  }

  async function sendApproved(preview: TestMessagePreview) {
    if (!preview.approval || !approved) return;
    setBusy(true);
    setError(null);
    try {
      const sent = await whatsappService.sendTestMessage(preview.approval.token);
      toast.info("Test message queued", sent.status_note);
      setPhase({ name: "sent", messageId: sent.message_id, note: sent.status_note });
      onChanged();
    } catch (cause) {
      setError(toApiError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  if (phase.name === "sent") {
    return (
      <div className="flex flex-col gap-3">
        <Tracker messageId={phase.messageId} note={phase.note} onFinal={onChanged} />
        <Button variant="outline" size="sm" className="w-fit" onClick={() => setPhase({ name: "form" })}>
          Send another test
        </Button>
      </div>
    );
  }

  if (phase.name === "preview") {
    const p = phase.preview;
    return (
      <section aria-label="Test message preview" className="flex flex-col gap-3">
        <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-2 text-sm">
          <dt className="text-muted-foreground">To</dt>
          <dd>
            {p.recipient.name ? `${p.recipient.name} · ` : ""}
            {p.recipient.phone_number || "—"}
          </dd>
          <dt className="text-muted-foreground">From</dt>
          <dd>
            {p.sender.label ? `${p.sender.label} · ` : ""}
            {p.sender.display_phone_number} ({p.sender.business})
          </dd>
          <dt className="text-muted-foreground">Type</dt>
          <dd>{p.template ? `Template “${p.template.name}” (${p.template.category.toLowerCase()})` : "—"}</dd>
          <dt className="text-muted-foreground">Exact message</dt>
          <dd className="break-words whitespace-pre-wrap">{p.content || "—"}</dd>
          <dt className="text-muted-foreground">Permission</dt>
          <dd>
            {p.authorization === "opted_in_contact"
              ? "This contact has opted in."
              : p.authorization === "attested"
                ? "You confirmed this person agreed to receive a test message."
                : "Not authorized yet."}
          </dd>
          <dt className="text-muted-foreground">Cost</dt>
          <dd>{costText(p.cost)}</dd>
        </dl>

        {p.restrictions.length ? (
          <div>
            <p className="text-sm font-medium">Good to know</p>
            <ul className="text-supporting list-disc pl-5">
              {p.restrictions.map((r) => (
                <li key={r.text}>{r.text}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {p.blockers.length ? (
          <div role="alert" className="border-destructive/25 bg-danger-soft/60 rounded-md border px-3 py-2">
            <p className="text-sm font-medium">This test message can&apos;t be sent yet</p>
            <ul className="text-sm">
              {p.blockers.map((b) => (
                <li key={b.code}>{b.message}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {p.can_send && p.approval ? (
          <fieldset className="flex flex-col gap-3 rounded-md border px-3 py-3">
            <legend className="px-1 text-sm font-medium">Approval needed before anything is sent</legend>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={approved}
                onChange={(event) => setApproved(event.target.checked)}
              />
              <span>
                I approve sending exactly this message to {p.recipient.phone_number} from{" "}
                {p.sender.display_phone_number}.
              </span>
            </label>
            <p className="text-caption">This approval expires in {Math.round(p.approval.expires_in / 60)} minutes and works once.</p>
          </fieldset>
        ) : null}
        {error ? (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {p.can_send && p.approval ? (
            <LoadingButton loading={busy} disabled={!approved} onClick={() => void sendApproved(p)}>
              Send test message
            </LoadingButton>
          ) : null}
          <Button variant="outline" onClick={() => void cancel(p)}>
            {p.can_send ? "Cancel" : "Back"}
          </Button>
        </div>
      </section>
    );
  }

  return (
    <form
      aria-label="Test message"
      className="flex max-w-xl flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        void preview();
      }}
    >
      <p className="text-supporting">
        Nothing is sent from this form. You will see exactly what would be sent and approve it separately.
      </p>
      <label className="flex flex-col gap-1 text-sm">
        Send from
        <select
          className="bg-background h-9 rounded-md border px-2"
          value={sender}
          onChange={(event) => setNumberId(event.target.value)}
        >
          {usable.map((n) => (
            <option key={n.id} value={n.id}>
              {n.label ? `${n.label} · ` : ""}
              {n.display_phone_number}
            </option>
          ))}
        </select>
      </label>

      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm">Send to</legend>
        <label className="flex items-center gap-2 text-sm">
          <input type="radio" name="mode" checked={mode === "number"} onChange={() => setMode("number")} />
          A phone number
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="radio" name="mode" checked={mode === "contact"} onChange={() => setMode("contact")} />
          A contact who has opted in
        </label>
      </fieldset>

      {mode === "number" ? (
        <>
          <label className="flex flex-col gap-1 text-sm">
            Phone number (with country code)
            <input
              className="bg-background h-9 rounded-md border px-2"
              value={phoneInput}
              inputMode="tel"
              placeholder="919876543210"
              onChange={(event) => setPhoneInput(event.target.value)}
            />
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={attest}
              onChange={(event) => setAttest(event.target.checked)}
            />
            <span>This person has agreed to receive a test message from us.</span>
          </label>
        </>
      ) : (
        <label className="flex flex-col gap-1 text-sm">
          Contact
          <select
            className="bg-background h-9 rounded-md border px-2"
            value={contactId}
            onChange={(event) => setContactId(event.target.value)}
          >
            <option value="">Choose a contact…</option>
            {contacts.map((c) => (
              <option key={c.id} value={c.id}>
                {c.display_name || c.profile_name || c.phone_number} · {c.phone_number}
              </option>
            ))}
          </select>
          {contacts.length === 0 ? (
            <span className="text-caption">No contact has opted in yet. Use a phone number instead.</span>
          ) : null}
        </label>
      )}

      {error ? (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      ) : null}
      <LoadingButton
        type="submit"
        loading={busy}
        disabled={mode === "contact" ? !contactId : phoneInput.trim().length < 6}
        className="w-fit"
      >
        Preview test message
      </LoadingButton>
    </form>
  );
}
