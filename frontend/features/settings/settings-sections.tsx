"use client";

import { KeyRoundIcon, ScrollTextIcon } from "lucide-react";
import { useState } from "react";

import { DetailList, Panel, SearchInput } from "@/components/ui/page";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import { StatusBadge, toneFor } from "@/components/ui/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApiQuery } from "@/hooks/use-api-query";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { api } from "@/lib/api/client";
import { useSession } from "@/stores/session-context";
import type { CursorPage } from "@/types/api";

export function Workspace() {
  const { active, businesses, user } = useSession();
  return (
    <div className="flex flex-col gap-4">
      <Panel title="Organization" bodyClassName="p-4">
        <DetailList
          items={[
            { label: "Name", value: active?.organization.name ?? "—" },
            { label: "Slug", value: <span className="font-mono text-xs">{active?.organization.slug}</span> },
            { label: "Time zone", value: active?.organization.timezone ?? "—" },
            { label: "Your role", value: <span className="capitalize">{active?.role}</span> },
            { label: "Signed in as", value: user?.email ?? "—" },
          ]}
        />
      </Panel>
      <Panel title="Businesses" description="Workspaces you can access in this organization.">
        <ul className="divide-y">
          {businesses.map((b) => (
            <li key={b.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
              <span className="font-medium">{b.name}</span>
              <span className="text-caption">
                {b.phone_number_count} number(s) · {b.business_account_count} WhatsApp account(s) ·{" "}
                {b.timezone || "organization time zone"}
              </span>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}

interface Credential {
  id: string;
  label: string;
  provider: string;
  kind: string;
  backend: string;
  fingerprint: string;
  status: string;
  created_at: string;
}

/** Provider credentials: metadata only — the API never returns secrets. */
export function WhatsAppSettings() {
  const credentials = useApiQuery(
    () => api.get<CursorPage<Credential>>("/api/v1/whatsapp/credentials/", { noBusiness: true }),
    [],
  );
  return (
    <Panel
      title="Provider credentials"
      description="Access tokens are stored encrypted or referenced from the server environment; they are never shown here."
    >
      {credentials.error ? (
        <ErrorState error={credentials.error} onRetry={credentials.reload} inline className="m-4" />
      ) : null}
      {!credentials.data && !credentials.error ? <TableSkeleton rows={2} columns={4} /> : null}
      {credentials.data && credentials.data.results.length === 0 ? (
        <EmptyState
          compact
          icon={KeyRoundIcon}
          title="No credentials configured"
          description="A credential is added when a WhatsApp Business Account is connected."
        />
      ) : null}
      <ul className="divide-y">
        {credentials.data?.results.map((c) => (
          <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
            <span>
              <span className="font-medium">{c.label}</span>
              <span className="text-caption block">
                {c.provider} · {c.kind.replaceAll("_", " ")} ·{" "}
                {c.backend === "env" ? "server environment" : "encrypted"}
              </span>
            </span>
            <span className="flex items-center gap-2">
              {c.fingerprint ? <span className="text-caption font-mono">…{c.fingerprint}</span> : null}
              <StatusBadge tone={toneFor(c.status)}>{c.status}</StatusBadge>
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

interface AuditEntry {
  id: string;
  actor_type: string;
  actor_email: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  ip_address: string | null;
  created_at: string;
}

export function AuditLog() {
  const [input, setInput] = useState("");
  const action = useDebouncedValue(input.trim(), 400);
  const log = useApiQuery(
    () =>
      api.get<CursorPage<AuditEntry>>("/api/v1/audit-logs/", {
        noBusiness: true,
        query: { action: action || undefined, page_size: 50 },
      }),
    [action],
  );
  return (
    <Panel
      title="Audit log"
      description="Security-relevant actions in this organization, latest first."
      actions={
        <SearchInput
          aria-label="Filter by exact action"
          placeholder="e.g. campaign.launched"
          className="w-56"
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
      }
      bodyClassName="overflow-x-auto"
    >
      {log.error ? <ErrorState error={log.error} onRetry={log.reload} inline className="m-4" /> : null}
      {!log.data && !log.error ? <TableSkeleton columns={4} /> : null}
      {log.data && log.data.results.length === 0 ? (
        <EmptyState
          compact
          icon={ScrollTextIcon}
          title="No audit entries"
          description={action ? "No entries for this action." : "Actions appear here as they happen."}
        />
      ) : null}
      {log.data && log.data.results.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>When</TableHead>
              <TableHead>Action</TableHead>
              <TableHead className="hidden md:table-cell">Actor</TableHead>
              <TableHead className="hidden lg:table-cell">Resource</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {log.data.results.map((e) => (
              <TableRow key={e.id}>
                <TableCell className="text-caption whitespace-nowrap">
                  {new Date(e.created_at).toLocaleString()}
                </TableCell>
                <TableCell className="font-mono text-xs">{e.action}</TableCell>
                <TableCell className="hidden md:table-cell">{e.actor_email ?? e.actor_type}</TableCell>
                <TableCell className="text-caption hidden font-mono lg:table-cell">
                  {e.resource_type} {e.resource_id ? `· ${e.resource_id.slice(0, 8)}…` : ""}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
    </Panel>
  );
}
