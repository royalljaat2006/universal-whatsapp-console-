"use client";

import { ArrowRightIcon, UsersIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { InitialsAvatar } from "@/components/ui/initials-avatar";
import { NativeSelect } from "@/components/ui/native-select";
import { DetailList, PageHeader, Panel, SearchInput, Toolbar } from "@/components/ui/page";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { LoadingButton } from "@/components/ui/spinner";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import { StatusBadge, toneFor } from "@/components/ui/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ContactImportButton } from "@/features/contacts/contact-import-button";
import { Notes } from "@/features/inbox/panel/contact-panel";
import { ExportButton } from "@/features/reports/export-button";
import { useApiQuery } from "@/hooks/use-api-query";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { apiRequest } from "@/lib/api/client";
import type { ApiError } from "@/lib/api/errors";
import { shortTime, toApiError } from "@/lib/format";
import { useGlobalSearch } from "@/lib/global-search";
import { contactService, tagService } from "@/services/contacts";
import { useSession } from "@/stores/session-context";
import type { Contact, CursorPage } from "@/types/api";

const OPT_IN_LABEL: Record<Contact["opt_in_status"], string> = {
  opted_in: "opted in",
  opted_out: "opted out",
  unknown: "no opt-in",
};

function name(c: Contact): string {
  return c.display_name || c.profile_name || c.phone_number || "Unnamed";
}

/**
 * Contacts of the selected business. Most rows are created by real inbound
 * messages (or sends) — a real WhatsApp user who interacted with one of the
 * business's numbers — but a contact may also be created or enriched through
 * a CSV import (contacts.manage), before any conversation exists.
 */
export function ContactsView() {
  const { business, hasPermission } = useSession();
  const [search, setSearch] = useState("");
  const q = useDebouncedValue(search.trim(), 300);
  const [tag, setTag] = useState("");
  const [extra, setExtra] = useState<{ key: string; rows: Contact[]; next: string | null } | null>(null);
  const [moreError, setMoreError] = useState<ApiError | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  useGlobalSearch(setSearch);

  const key = JSON.stringify([business?.id, q, tag]);
  const page = useApiQuery(
    () => contactService.list({ q: q || undefined, tag: tag || undefined }),
    [business?.id, q, tag],
    Boolean(business),
  );
  const tags = useApiQuery(() => tagService.list(), [business?.id], Boolean(business));

  if (!business) return null;

  const more = extra?.key === key ? extra : null;
  const rows = [...(page.data?.results ?? []), ...(more?.rows ?? [])];
  const next = more ? more.next : (page.data?.next ?? null);

  async function loadMore() {
    if (!next) return;
    setMoreError(null);
    setLoadingMore(true);
    try {
      const url = new URL(next, window.location.origin);
      const result = await apiRequest<CursorPage<Contact>>(url.pathname + url.search);
      setExtra({ key, rows: [...(more?.rows ?? []), ...result.results], next: result.next });
    } catch (cause) {
      setMoreError(toApiError(cause));
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Contacts"
        description={`Customers who have interacted with ${business.name}'s WhatsApp numbers.`}
        actions={
          <>
            <ExportButton kind="contacts" params={{}} />
            <ContactImportButton onImported={page.reload} />
          </>
        }
      />
      <Panel bodyClassName="overflow-x-auto">
        <Toolbar>
          <SearchInput
            aria-label="Search contacts"
            className="w-full sm:w-80"
            placeholder="Search name or phone…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <NativeSelect aria-label="Filter by tag" value={tag} onChange={(e) => setTag(e.target.value)}>
            <option value="">All tags</option>
            {tags.data?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </NativeSelect>
          {page.data ? <span className="text-caption ml-auto">{rows.length} shown</span> : null}
        </Toolbar>
        {page.error ? <ErrorState error={page.error} onRetry={page.reload} inline className="m-4" /> : null}
        {moreError ? <ErrorState error={moreError} inline className="m-4" /> : null}
        {!page.data && !page.error ? (
          <TableSkeleton columns={6} />
        ) : rows.length === 0 && page.data ? (
          <EmptyState
            icon={UsersIcon}
            title={q || tag ? "No contacts match" : "No contacts yet"}
            description={
              q || tag
                ? "Try a different search or tag."
                : "Contacts are created when customers message this business's WhatsApp numbers, or by importing a CSV above."
            }
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Phone</TableHead>
                <TableHead className="hidden md:table-cell">Tags</TableHead>
                <TableHead className="hidden sm:table-cell">Last activity</TableHead>
                <TableHead className="hidden lg:table-cell">Conversations</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((c) => (
                <TableRow key={c.id} className="cursor-pointer" onClick={() => setSelectedId(c.id)}>
                  <TableCell>
                    <button
                      type="button"
                      className="flex items-center gap-2.5 text-left"
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelectedId(c.id);
                      }}
                    >
                      <InitialsAvatar name={name(c)} seed={c.id} size="sm" />
                      <span className="min-w-0">
                        <span className="block max-w-56 truncate font-medium">{name(c)}</span>
                        {c.profile_name && c.display_name && c.profile_name !== c.display_name ? (
                          <span className="text-caption block truncate">WhatsApp: {c.profile_name}</span>
                        ) : null}
                      </span>
                    </button>
                  </TableCell>
                  <TableCell className="tabular-nums">{c.phone_number || "BSUID"}</TableCell>
                  <TableCell className="hidden md:table-cell">
                    <span className="flex max-w-64 flex-wrap gap-1">
                      {c.tags.length === 0 ? <span className="text-caption">—</span> : null}
                      {c.tags.map((t) => (
                        <span key={t.id} className="bg-muted rounded px-1.5 py-0.5 text-[11px] font-medium">
                          {t.name}
                        </span>
                      ))}
                    </span>
                  </TableCell>
                  <TableCell className="text-caption hidden sm:table-cell">
                    {c.last_message_at ? shortTime(c.last_message_at) : "—"}
                  </TableCell>
                  <TableCell className="hidden tabular-nums lg:table-cell">
                    {c.conversation_count ?? "—"}
                  </TableCell>
                  <TableCell>
                    <StatusBadge tone={c.opt_in_status === "unknown" ? "neutral" : toneFor(c.opt_in_status)}>
                      {OPT_IN_LABEL[c.opt_in_status]}
                    </StatusBadge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {next ? (
          <div className="border-t p-3">
            <LoadingButton
              variant="outline"
              size="sm"
              className="w-full"
              loading={loadingMore}
              onClick={() => void loadMore()}
            >
              Load more
            </LoadingButton>
          </div>
        ) : null}
      </Panel>

      <Sheet open={selectedId !== null} onOpenChange={(open) => !open && setSelectedId(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-md">
          {selectedId ? (
            <ContactDrawer id={selectedId} canEditNotes={hasPermission("contacts.manage")} />
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function ContactDrawer({ id, canEditNotes }: { id: string; canEditNotes: boolean }) {
  const contact = useApiQuery(() => contactService.get(id), [id]);
  const history = useApiQuery(() => contactService.history(id), [id]);
  const c = contact.data?.id === id ? contact.data : undefined;
  return (
    <>
      <SheetHeader className="border-b">
        <div className="flex items-center gap-3">
          {c ? <InitialsAvatar name={name(c)} seed={c.id} size="lg" /> : null}
          <div className="min-w-0">
            <SheetTitle className="truncate">{c ? name(c) : "Contact"}</SheetTitle>
            <SheetDescription className="tabular-nums">{c?.phone_number || " "}</SheetDescription>
          </div>
        </div>
      </SheetHeader>
      <div className="flex flex-col gap-6 px-4 pb-8">
        {contact.error ? <ErrorState error={contact.error} onRetry={contact.reload} inline /> : null}
        {!c && !contact.error ? <TableSkeleton rows={4} columns={2} /> : null}
        {c ? (
          <>
            <DetailList
              items={[
                { label: "WhatsApp name", value: c.profile_name || "—" },
                { label: "Opt-in", value: OPT_IN_LABEL[c.opt_in_status] },
                { label: "First seen", value: new Date(c.first_seen_at).toLocaleString() },
                {
                  label: "Last message",
                  value: c.last_message_at ? new Date(c.last_message_at).toLocaleString() : "—",
                },
                { label: "Conversations", value: c.conversation_count ?? "—" },
                {
                  label: "Tags",
                  value: c.tags.length ? c.tags.map((t) => t.name).join(", ") : "—",
                },
              ]}
            />
            <section>
              <h3 className="text-section-title mb-2">Attributes</h3>
              {Object.keys(c.attributes).length === 0 ? (
                <p className="text-supporting">No attributes.</p>
              ) : (
                <DetailList
                  items={Object.entries(c.attributes).map(([k, v]) => ({ label: k, value: String(v) }))}
                />
              )}
            </section>
          </>
        ) : null}
        <section>
          <h3 className="text-section-title mb-2">Conversations</h3>
          {history.error ? <ErrorState error={history.error} inline /> : null}
          {history.data?.length === 0 ? <p className="text-supporting">No conversations.</p> : null}
          <ul className="flex flex-col gap-2">
            {history.data?.map((h) => (
              <li key={h.id} className="rounded-md border px-3 py-2">
                <p className="flex items-center justify-between gap-2 text-sm">
                  <span className="truncate">via {h.phone_number_display}</span>
                  <StatusBadge tone={toneFor(h.status === "resolved" ? "succeeded" : h.status)}>
                    {h.status}
                  </StatusBadge>
                </p>
                <p className="text-caption truncate">{h.last_message_preview || "—"}</p>
                <p className="text-caption">{shortTime(h.last_message_at)}</p>
              </li>
            ))}
          </ul>
          <Button
            variant="link"
            size="sm"
            className="mt-1 px-0"
            nativeButton={false}
            render={<Link href="/inbox" />}
          >
            Open inbox <ArrowRightIcon aria-hidden />
          </Button>
        </section>
        {c ? (
          <section>
            <h3 className="text-section-title mb-2">Notes</h3>
            <Notes contactId={c.id} canEdit={canEditNotes} />
          </section>
        ) : null}
      </div>
    </>
  );
}
