"use client";

import { ArchiveIcon, CheckCircle2Icon, ClockIcon, RotateCcwIcon, SmartphoneIcon } from "lucide-react";
import { useState } from "react";

import { ApiErrorNotice } from "@/components/status/api-error-notice";
import { Badge } from "@/components/ui/badge";
import { InitialsAvatar } from "@/components/ui/initials-avatar";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge, toneFor } from "@/components/ui/status-badge";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect, Textarea } from "@/components/ui/native-select";
import { useApiQuery } from "@/hooks/use-api-query";
import type { ApiError } from "@/lib/api/errors";
import { toApiError } from "@/lib/format";
import { contactService } from "@/services/contacts";
import { inboxService } from "@/services/inbox";
import { useSession } from "@/stores/session-context";
import type { AssigneeOptions, Contact, ConversationStatus, InboxConversation, Tag } from "@/types/api";

import { AttributesEditor } from "./attributes-editor";
import { TagPicker } from "./tag-picker";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2.5 border-b px-4 py-4">
      <h3 className="text-muted-foreground text-[11px] font-semibold tracking-wider uppercase">{title}</h3>
      {children}
    </section>
  );
}

const STATUS_ACTIONS: {
  status: ConversationStatus;
  label: string;
  icon: typeof CheckCircle2Icon;
  when: ConversationStatus[];
}[] = [
  { status: "resolved", label: "Resolve", icon: CheckCircle2Icon, when: ["open", "pending"] },
  { status: "open", label: "Reopen", icon: RotateCcwIcon, when: ["resolved", "archived"] },
  { status: "pending", label: "Mark pending", icon: ClockIcon, when: ["open"] },
  { status: "archived", label: "Archive", icon: ArchiveIcon, when: ["open", "pending", "resolved"] },
];

/**
 * Right-hand panel: contact profile (name, attributes, tags, notes) and the
 * conversation's workflow (status, assignment + history, tags). Every change
 * goes to the backend, which checks permission and business ownership; the
 * returned resource replaces local state (no optimistic writes).
 */
export function ContactPanel({
  conversation,
  onConversationChange,
  tags,
  assignees,
  contactVersion,
}: {
  conversation: InboxConversation;
  onConversationChange: (c: InboxConversation) => void;
  tags: Tag[];
  assignees: AssigneeOptions | undefined;
  contactVersion: number;
}) {
  const { hasPermission } = useSession();
  const canManageContacts = hasPermission("contacts.manage");
  const canAssign = hasPermission("conversations.assign");
  const canTagConversation = hasPermission("messages.send");
  const [error, setError] = useState<ApiError | null>(null);
  const toast = useToast();

  const contactQuery = useApiQuery(
    () => contactService.get(conversation.contact_id),
    [conversation.contact_id, contactVersion],
    hasPermission("contacts.view"),
  );
  const contact = contactQuery.data?.id === conversation.contact_id ? contactQuery.data : null;

  async function run<T>(action: () => Promise<T>, apply: (value: T) => void): Promise<boolean> {
    setError(null);
    try {
      apply(await action());
      return true;
    } catch (cause) {
      setError(toApiError(cause));
      return false;
    }
  }
  // The mutation response is authoritative, but re-reading keeps the
  // annotated fields (conversation_count) consistent.
  const applyContact = () => contactQuery.reload();

  return (
    <div className="flex flex-col">
      {error ? (
        <div className="p-3">
          <ApiErrorNotice error={error} />
        </div>
      ) : null}

      <Section title="Contact">
        {contactQuery.error ? <ApiErrorNotice error={contactQuery.error} /> : null}
        {!hasPermission("contacts.view") ? (
          <p className="text-muted-foreground text-xs">You cannot view contact details.</p>
        ) : !contact ? (
          <div role="status" aria-label="Loading contact" className="flex items-center gap-3">
            <Skeleton className="size-12 rounded-full" />
            <div className="flex flex-1 flex-col gap-1.5">
              <Skeleton className="h-3.5 w-3/5" />
              <Skeleton className="h-3 w-2/5" />
            </div>
          </div>
        ) : (
          <ContactDetails
            contact={contact}
            canEdit={canManageContacts}
            tags={tags}
            run={run}
            applyContact={applyContact}
          />
        )}
      </Section>

      <Section title="Conversation">
        <div className="flex items-center justify-between gap-2 text-xs">
          <span className="text-muted-foreground">Status</span>
          <StatusBadge tone={toneFor(conversation.status === "resolved" ? "succeeded" : conversation.status)}>
            {conversation.status}
          </StatusBadge>
        </div>
        <div className="flex items-center justify-between gap-2 text-xs">
          <span className="text-muted-foreground">WhatsApp number</span>
          <span className="inline-flex items-center gap-1 tabular-nums">
            <SmartphoneIcon aria-hidden className="text-muted-foreground size-3.5" />
            {conversation.phone_number_display}
          </span>
        </div>
        {canAssign ? (
          <div className="flex flex-wrap gap-1.5">
            {STATUS_ACTIONS.filter((a) => a.when.includes(conversation.status)).map((a) => {
              const Icon = a.icon;
              return (
                <Button
                  key={a.status}
                  variant={a.status === "resolved" ? "default" : "outline"}
                  size="xs"
                  onClick={() =>
                    void run(
                      () => inboxService.setStatus(conversation.id, a.status),
                      (c) => {
                        onConversationChange(c);
                        toast.success(`Conversation ${c.status}`);
                      },
                    )
                  }
                >
                  <Icon aria-hidden /> {a.label}
                </Button>
              );
            })}
          </div>
        ) : null}
        <TagPicker
          label="Conversation tags"
          current={conversation.tags}
          available={tags}
          canEdit={canTagConversation}
          onAdd={(id) => void run(() => inboxService.addTag(conversation.id, id), onConversationChange)}
          onRemove={(id) => void run(() => inboxService.removeTag(conversation.id, id), onConversationChange)}
        />
      </Section>

      <Section title="Assignment">
        <AssignmentControls
          conversation={conversation}
          assignees={assignees}
          canAssign={canAssign}
          onAssign={(membershipId, teamId) =>
            run(() => inboxService.assign(conversation.id, membershipId, teamId), onConversationChange)
          }
        />
      </Section>

      {contact && hasPermission("contacts.view") ? (
        <Section title="Notes">
          <Notes contactId={contact.id} canEdit={canManageContacts} />
        </Section>
      ) : null}
    </div>
  );
}

function ContactDetails({
  contact,
  canEdit,
  tags,
  run,
  applyContact,
}: {
  contact: Contact;
  canEdit: boolean;
  tags: Tag[];
  run: <T>(action: () => Promise<T>, apply: (value: T) => void) => Promise<boolean>;
  applyContact: () => void;
}) {
  const [name, setName] = useState<string | null>(null);
  return (
    <div className="flex flex-col gap-3 text-xs">
      {name === null ? (
        <div className="flex items-center gap-3">
          <InitialsAvatar
            name={contact.display_name || contact.profile_name || contact.phone_number || "?"}
            seed={contact.id}
            size="lg"
          />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold">
              {contact.display_name || contact.profile_name || "Unnamed"}
            </span>
            <span className="text-caption block tabular-nums">{contact.phone_number || "BSUID contact"}</span>
          </span>
          {canEdit ? (
            <Button variant="ghost" size="xs" onClick={() => setName(contact.display_name)}>
              Rename
            </Button>
          ) : null}
        </div>
      ) : (
        <form
          className="flex gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            void run(
              () => contactService.update(contact.id, { display_name: name.trim() }),
              () => {
                applyContact();
                setName(null);
              },
            );
          }}
        >
          <Input
            aria-label="Display name"
            className="h-7 text-xs"
            maxLength={200}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button size="xs" type="submit">
            Save
          </Button>
          <Button size="xs" variant="ghost" type="button" onClick={() => setName(null)}>
            Cancel
          </Button>
        </form>
      )}
      <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
        <dt className="text-muted-foreground">Phone</dt>
        <dd>{contact.phone_number || "hidden (username contact)"}</dd>
        {contact.profile_name ? (
          <>
            <dt className="text-muted-foreground">WhatsApp name</dt>
            <dd>{contact.profile_name}</dd>
          </>
        ) : null}
        {contact.identities
          .filter((i) => i.kind !== "phone")
          .map((i) => (
            <div key={`${i.kind}-${i.value}`} className="contents">
              <dt className="text-muted-foreground">{i.kind}</dt>
              <dd className="font-mono break-all">{i.value}</dd>
            </div>
          ))}
        <dt className="text-muted-foreground">First seen</dt>
        <dd>{new Date(contact.first_seen_at).toLocaleString()}</dd>
        <dt className="text-muted-foreground">Conversations</dt>
        <dd>{contact.conversation_count ?? "—"}</dd>
      </dl>
      <label className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground">Opt-in</span>
        <NativeSelect
          aria-label="Opt-in status"
          className="h-7 text-xs"
          value={contact.opt_in_status}
          disabled={!canEdit}
          onChange={(e) =>
            void run(
              () =>
                contactService.update(contact.id, {
                  opt_in_status: e.target.value as Contact["opt_in_status"],
                }),
              applyContact,
            )
          }
        >
          <option value="unknown">Unknown</option>
          <option value="opted_in">Opted in</option>
          <option value="opted_out">Opted out</option>
        </NativeSelect>
      </label>
      <AttributesEditor
        key={contact.updated_at}
        attributes={contact.attributes}
        canEdit={canEdit}
        onSave={(attributes) => run(() => contactService.update(contact.id, { attributes }), applyContact)}
      />
      <TagPicker
        label="Contact tags"
        current={contact.tags}
        available={tags}
        canEdit={canEdit}
        onAdd={(id) => void run(() => contactService.addTag(contact.id, id), applyContact)}
        onRemove={(id) => void run(() => contactService.removeTag(contact.id, id), applyContact)}
      />
    </div>
  );
}

function AssignmentControls({
  conversation,
  assignees,
  canAssign,
  onAssign,
}: {
  conversation: InboxConversation;
  assignees: AssigneeOptions | undefined;
  canAssign: boolean;
  onAssign: (membershipId: string | null, teamId: string | null) => Promise<boolean>;
}) {
  const [showHistory, setShowHistory] = useState(false);
  const history = useApiQuery(
    () => inboxService.assignments(conversation.id),
    [conversation.id, conversation.assignee?.membership_id ?? null, conversation.team?.id ?? null],
    showHistory,
  );
  const memberId = conversation.assignee?.membership_id ?? "";
  const teamId = conversation.team?.id ?? "";

  return (
    <div className="flex flex-col gap-2 text-xs">
      <label className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground">Member</span>
        <NativeSelect
          aria-label="Assign member"
          className="h-7 max-w-44 text-xs"
          value={memberId}
          disabled={!canAssign}
          onChange={(e) => void onAssign(e.target.value || null, teamId || null)}
        >
          <option value="">Unassigned</option>
          {conversation.assignee && !assignees?.members.some((m) => m.membership_id === memberId) ? (
            <option value={memberId}>{conversation.assignee.name}</option>
          ) : null}
          {assignees?.members.map((m) => (
            <option key={m.membership_id} value={m.membership_id}>
              {m.name}
            </option>
          ))}
        </NativeSelect>
      </label>
      <label className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground">Team</span>
        <NativeSelect
          aria-label="Assign team"
          className="h-7 max-w-44 text-xs"
          value={teamId}
          disabled={!canAssign}
          onChange={(e) => void onAssign(memberId || null, e.target.value || null)}
        >
          <option value="">No team</option>
          {assignees?.teams.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </NativeSelect>
      </label>
      <Button variant="ghost" size="xs" className="self-start" onClick={() => setShowHistory((v) => !v)}>
        {showHistory ? "Hide history" : "Show assignment history"}
      </Button>
      {showHistory ? (
        history.error ? (
          <ApiErrorNotice error={history.error} />
        ) : !history.data ? (
          <span className="text-muted-foreground">Loading…</span>
        ) : history.data.length === 0 ? (
          <span className="text-muted-foreground">Never assigned.</span>
        ) : (
          <ol className="flex flex-col gap-1">
            {history.data.map((h) => (
              <li key={h.id} className="border-l-2 pl-2">
                <span>
                  {h.new_assignee_name ?? "Unassigned"}
                  {h.new_team_name ? ` · ${h.new_team_name}` : ""}
                </span>
                <span className="text-muted-foreground block">
                  {new Date(h.created_at).toLocaleString()}
                  {h.assigned_by_email ? ` by ${h.assigned_by_email}` : ""}
                </span>
              </li>
            ))}
          </ol>
        )
      ) : null}
    </div>
  );
}

export function Notes({ contactId, canEdit }: { contactId: string; canEdit: boolean }) {
  const notes = useApiQuery(() => contactService.notes(contactId), [contactId]);
  const [body, setBody] = useState("");
  const [error, setError] = useState<ApiError | null>(null);

  async function act(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      notes.reload();
      return true;
    } catch (cause) {
      setError(toApiError(cause));
      return false;
    }
  }

  return (
    <div className="flex flex-col gap-2 text-xs">
      {error ? <ApiErrorNotice error={error} /> : null}
      {notes.error ? <ApiErrorNotice error={notes.error} /> : null}
      {canEdit ? (
        <form
          className="flex flex-col gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            const text = body.trim();
            if (text) void act(() => contactService.addNote(contactId, text)).then((ok) => ok && setBody(""));
          }}
        >
          <Textarea
            aria-label="New note"
            className="text-xs"
            placeholder="Internal note (not sent to the customer)"
            maxLength={5000}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
          <Button size="xs" type="submit" className="self-end" disabled={!body.trim()}>
            Add note
          </Button>
        </form>
      ) : null}
      {notes.data?.length === 0 ? <span className="text-muted-foreground">No notes.</span> : null}
      <ul className="flex flex-col gap-2">
        {notes.data?.map((n) => (
          <li key={n.id} className="bg-muted/50 rounded-md p-2">
            <p className="whitespace-pre-wrap">{n.body}</p>
            <p className="text-muted-foreground mt-1 flex items-center justify-between">
              <span>
                {n.author_email ?? "unknown"} · {new Date(n.created_at).toLocaleString()}
              </span>
              {canEdit ? (
                <button
                  type="button"
                  className="hover:text-destructive"
                  onClick={() => void act(() => contactService.deleteNote(contactId, n.id))}
                >
                  Delete
                </button>
              ) : null}
            </p>
          </li>
        ))}
      </ul>
      <Badge variant="outline" className="self-start">
        internal only
      </Badge>
    </div>
  );
}
