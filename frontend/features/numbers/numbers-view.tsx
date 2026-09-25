"use client";

import { RefreshCwIcon, ShieldCheckIcon, SmartphoneIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { DetailList, PageHeader, Panel, SearchInput, Toolbar } from "@/components/ui/page";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { LoadingButton } from "@/components/ui/spinner";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import { StatusBadge, toneFor } from "@/components/ui/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { useApiQuery } from "@/hooks/use-api-query";
import { toApiError } from "@/lib/format";
import { eventService } from "@/services/access";
import { whatsappService } from "@/services/whatsapp";
import { useSession } from "@/stores/session-context";
import type { BusinessAccount, PhoneNumber } from "@/types/api";

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "Never";
}

function mask(display: string): string {
  const digits = display.replace(/\D/g, "");
  return digits.length > 4 ? `•••• ${digits.slice(-4)}` : display;
}

/** WhatsApp Business Accounts and numbers of the selected business, as synced from Meta. */
export function NumbersView() {
  const { business, hasPermission } = useSession();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<PhoneNumber | null>(null);
  const [syncing, setSyncing] = useState(false);
  const enabled = Boolean(business);
  const accounts = useApiQuery(() => whatsappService.accounts(), [business?.id], enabled);
  const numbers = useApiQuery(
    () => whatsappService.numbers({ status: status || undefined }),
    [business?.id, status],
    enabled,
  );
  const events = useApiQuery(() => eventService.list(), [business?.id], enabled);
  const canManage = hasPermission("numbers.manage");

  const rows = useMemo(() => {
    const term = q.trim().toLowerCase();
    const all = numbers.data?.results ?? [];
    if (!term) return all;
    return all.filter((n) =>
      [
        n.display_phone_number,
        n.verified_name,
        n.label,
        n.business_account_external_id,
        n.business_account_name,
      ]
        .join(" ")
        .toLowerCase()
        .includes(term),
    );
  }, [numbers.data, q]);

  const accountsById = useMemo(
    () => new Map((accounts.data?.results ?? []).map((a) => [a.id, a])),
    [accounts.data],
  );

  if (!business) return null;

  async function syncAll(list: BusinessAccount[]) {
    setSyncing(true);
    try {
      await Promise.all(list.map((a) => whatsappService.requestSync(a.id)));
      toast.success(
        "Sync requested",
        `${list.length} account(s) queued. Numbers refresh when Meta responds.`,
      );
    } catch (cause) {
      toast.error("Sync failed", toApiError(cause).message);
    } finally {
      setSyncing(false);
    }
  }

  const refresh = () => {
    accounts.reload();
    numbers.reload();
    events.reload();
  };
  const accountList = accounts.data?.results ?? [];
  const error = accounts.error ?? numbers.error;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="WhatsApp numbers"
        description={`Numbers and WhatsApp Business Accounts connected to ${business.name}. Data comes from Meta's sync.`}
        actions={
          <>
            <Button variant="outline" size="sm" onClick={refresh}>
              <RefreshCwIcon aria-hidden /> Refresh
            </Button>
            {canManage && accountList.length ? (
              <LoadingButton size="sm" loading={syncing} onClick={() => void syncAll(accountList)}>
                Sync from Meta
              </LoadingButton>
            ) : null}
          </>
        }
      />

      {error ? <ErrorState error={error} onRetry={refresh} inline /> : null}

      {accounts.data && accountList.length === 0 ? (
        <Panel>
          <EmptyState
            icon={SmartphoneIcon}
            title={`No WhatsApp Business Account connected to ${business.name}`}
            description="Once an administrator connects this business's WhatsApp Business Account, its numbers are imported from Meta and appear here."
          />
        </Panel>
      ) : null}

      {accountList.length ? (
        <Panel title="WhatsApp Business Accounts" bodyClassName="divide-y">
          {accountList.map((a) => (
            <div key={a.id} className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
              <div className="min-w-48 flex-1">
                <p className="text-sm font-medium">{a.name || "Unnamed account"}</p>
                <p className="text-caption font-mono">WABA {a.external_id}</p>
              </div>
              <StatusBadge tone={toneFor(a.connection_state)}>{a.connection_state}</StatusBadge>
              <StatusBadge tone={a.webhooks_subscribed ? "success" : "danger"}>
                {a.webhooks_subscribed ? "webhooks on" : "webhooks off"}
              </StatusBadge>
              <StatusBadge tone={toneFor(a.sync_state)}>sync {a.sync_state}</StatusBadge>
              <span className="text-caption">Last sync {when(a.last_synced_at)}</span>
              {a.connection_error || a.sync_error ? (
                <p className="text-destructive w-full text-xs">{a.connection_error || a.sync_error}</p>
              ) : null}
            </div>
          ))}
        </Panel>
      ) : null}

      {accountList.length ? (
        <Panel bodyClassName="overflow-x-auto">
          <Toolbar>
            <SearchInput
              aria-label="Search numbers"
              placeholder="Search number, name or WABA…"
              className="w-full sm:w-72"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <NativeSelect
              aria-label="Filter by status"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="">All statuses</option>
              {["connected", "pending", "disconnected", "flagged", "restricted"].map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </NativeSelect>
            <span className="text-caption ml-auto">{rows.length} number(s)</span>
          </Toolbar>
          {!numbers.data ? (
            <TableSkeleton columns={7} />
          ) : rows.length === 0 ? (
            <EmptyState
              compact
              title="No numbers match"
              description="Try a different search or status filter."
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Number</TableHead>
                  <TableHead className="hidden lg:table-cell">Business</TableHead>
                  <TableHead>WABA</TableHead>
                  <TableHead>Connection</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Quality</TableHead>
                  <TableHead className="hidden md:table-cell">Messaging limit</TableHead>
                  <TableHead className="hidden xl:table-cell">Last sync</TableHead>
                  <TableHead className="text-right">
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((n) => (
                  <TableRow key={n.id} className="cursor-pointer" onClick={() => setSelected(n)}>
                    <TableCell>
                      <span className="font-medium tabular-nums">{n.display_phone_number}</span>
                      <span className="text-caption block">{n.label || n.verified_name || "—"}</span>
                    </TableCell>
                    <TableCell className="hidden lg:table-cell">{business.name}</TableCell>
                    <TableCell>
                      <span className="block max-w-44 truncate">{n.business_account_name || "—"}</span>
                      <span className="text-caption font-mono">{n.business_account_external_id}</span>
                    </TableCell>
                    <TableCell>
                      <StatusBadge tone={toneFor(n.business_account_connection_state)}>
                        {n.business_account_connection_state}
                      </StatusBadge>
                    </TableCell>
                    <TableCell>
                      <StatusBadge tone={toneFor(n.status)}>{n.status}</StatusBadge>
                    </TableCell>
                    <TableCell>
                      {n.quality_rating ? (
                        <StatusBadge tone={toneFor(n.quality_rating)}>{n.quality_rating}</StatusBadge>
                      ) : (
                        <span className="text-caption">Not reported</span>
                      )}
                    </TableCell>
                    <TableCell className="hidden md:table-cell">
                      {n.messaging_limit_tier || "Not reported"}
                    </TableCell>
                    <TableCell className="text-caption hidden xl:table-cell">
                      {when(n.last_synced_at)}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelected(n);
                        }}
                      >
                        Details
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Panel>
      ) : null}

      <Panel
        title="Account events"
        description="Quality, messaging-limit, alert and account-update webhooks received from Meta."
        actions={
          <Button variant="ghost" size="xs" onClick={() => events.reload()}>
            <RefreshCwIcon aria-hidden /> Refresh
          </Button>
        }
      >
        {events.error ? (
          <ErrorState error={events.error} onRetry={events.reload} inline className="m-4" />
        ) : null}
        {!events.data && !events.error ? <TableSkeleton rows={3} columns={4} /> : null}
        {events.data && events.data.results.length === 0 ? (
          <EmptyState
            compact
            icon={ShieldCheckIcon}
            title="No account events yet"
            description="Meta has not sent any account-level notifications for this business."
          />
        ) : null}
        {events.data && events.data.results.length ? (
          <ul className="divide-y">
            {events.data.results.map((e) => (
              <li key={e.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-sm">
                <StatusBadge
                  tone={
                    e.severity === "critical" ? "danger" : e.severity === "warning" ? "warning" : "neutral"
                  }
                >
                  {e.severity}
                </StatusBadge>
                <span className="min-w-0 flex-1 truncate">{e.summary || e.event}</span>
                <span className="text-caption font-mono">{e.field}</span>
                <span className="text-caption">{when(e.occurred_at)}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </Panel>

      <Sheet open={selected !== null} onOpenChange={(open) => !open && setSelected(null)}>
        <SheetContent className="w-full sm:max-w-md">
          {selected ? (
            <>
              <SheetHeader className="border-b">
                <SheetTitle>{selected.display_phone_number}</SheetTitle>
                <SheetDescription>
                  {selected.verified_name || selected.label || mask(selected.display_phone_number)}
                </SheetDescription>
              </SheetHeader>
              <div className="flex flex-col gap-5 overflow-y-auto px-4 pb-6">
                <div className="flex flex-wrap gap-2">
                  <StatusBadge tone={toneFor(selected.status)}>{selected.status}</StatusBadge>
                  <StatusBadge tone={toneFor(selected.business_account_connection_state)}>
                    WABA {selected.business_account_connection_state}
                  </StatusBadge>
                </div>
                <DetailList
                  items={[
                    { label: "Business", value: business.name },
                    { label: "Display name", value: selected.verified_name || "—" },
                    { label: "Label", value: selected.label || "—" },
                    {
                      label: "Phone number ID",
                      value: <span className="font-mono text-xs">{selected.external_id}</span>,
                    },
                    {
                      label: "WABA",
                      value: (
                        <>
                          {accountsById.get(selected.business_account_id)?.name ||
                            selected.business_account_name ||
                            "—"}
                          <span className="text-caption block font-mono">
                            {selected.business_account_external_id}
                          </span>
                        </>
                      ),
                    },
                    { label: "Provider status", value: selected.provider_status || "—" },
                    { label: "Name review", value: selected.name_status || "—" },
                    { label: "Quality", value: selected.quality_rating || "Not reported" },
                    { label: "Messaging limit", value: selected.messaging_limit_tier || "Not reported" },
                    { label: "Last sync", value: when(selected.last_synced_at) },
                  ]}
                />
                {selected.last_error ? (
                  <p className="border-destructive/25 bg-danger-soft rounded-md border px-3 py-2 text-xs">
                    {selected.last_error}
                  </p>
                ) : null}
                <div>
                  <h3 className="text-section-title mb-2">Events for this number</h3>
                  {(events.data?.results ?? []).filter((e) => e.phone_number_id === selected.id).length ===
                  0 ? (
                    <p className="text-supporting">No events recorded for this number.</p>
                  ) : (
                    <ul className="flex flex-col gap-2">
                      {(events.data?.results ?? [])
                        .filter((e) => e.phone_number_id === selected.id)
                        .map((e) => (
                          <li key={e.id} className="rounded-md border px-3 py-2 text-xs">
                            <span className="font-medium">{e.summary || e.event}</span>
                            <span className="text-caption block">{when(e.occurred_at)}</span>
                          </li>
                        ))}
                    </ul>
                  )}
                </div>
              </div>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}
