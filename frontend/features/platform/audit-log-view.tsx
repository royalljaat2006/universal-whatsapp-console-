"use client";

import { ScrollTextIcon } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { PageHeader, Panel, SearchInput } from "@/components/ui/page";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApiQuery } from "@/hooks/use-api-query";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { apiRequest } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { toApiError } from "@/lib/format";
import { platformService } from "@/services/platform";
import type { CursorPage, PlatformAuditEntry } from "@/types/api";

export function AuditLogView() {
  const searchParams = useSearchParams();
  const organizationId = searchParams.get("organization_id") ?? undefined;
  const [input, setInput] = useState("");
  const action = useDebouncedValue(input.trim(), 400);

  const page = useApiQuery(
    () => platformService.auditLogs({ organization_id: organizationId, action: action || undefined }),
    [organizationId, action],
  );
  const [extra, setExtra] = useState<{ key: string; rows: PlatformAuditEntry[]; next: string | null } | null>(
    null,
  );
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<ApiError | null>(null);

  const key = JSON.stringify([organizationId, action]);
  const more = extra?.key === key ? extra : null;
  const rows = [...(page.data?.results ?? []), ...(more?.rows ?? [])];
  const next = more ? more.next : (page.data?.next ?? null);

  async function loadMore() {
    if (!next) return;
    setMoreError(null);
    setLoadingMore(true);
    try {
      const url = new URL(next, window.location.origin);
      const result = await apiRequest<CursorPage<PlatformAuditEntry>>(url.pathname + url.search, {
        unscoped: true,
      });
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
        title="Audit log"
        description={
          organizationId
            ? "Actions recorded for this organization."
            : "Security-relevant actions across every organization, latest first."
        }
      />
      <Panel
        actions={
          <SearchInput
            aria-label="Filter by exact action"
            placeholder="e.g. organization.suspended"
            className="w-64"
            value={input}
            onChange={(e) => setInput(e.target.value)}
          />
        }
        bodyClassName="overflow-x-auto"
      >
        {page.error ? <ErrorState error={page.error} onRetry={page.reload} inline className="m-4" /> : null}
        {!page.data && !page.error ? <TableSkeleton columns={5} /> : null}
        {page.data && rows.length === 0 ? (
          <EmptyState
            compact
            icon={ScrollTextIcon}
            title="No audit entries"
            description={action ? "No entries for this action." : "Actions appear here as they happen."}
          />
        ) : null}
        {rows.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>When</TableHead>
                <TableHead>Action</TableHead>
                <TableHead className="hidden md:table-cell">Actor</TableHead>
                {organizationId ? null : <TableHead className="hidden lg:table-cell">Organization</TableHead>}
                <TableHead className="hidden lg:table-cell">Resource</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((e) => (
                <TableRow key={e.id}>
                  <TableCell className="text-caption whitespace-nowrap">
                    {new Date(e.created_at).toLocaleString()}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{e.action}</TableCell>
                  <TableCell className="hidden md:table-cell">{e.actor_email ?? e.actor_type}</TableCell>
                  {organizationId ? null : (
                    <TableCell className="text-caption hidden font-mono lg:table-cell">
                      {e.organization ? `${e.organization.slice(0, 8)}…` : "platform"}
                    </TableCell>
                  )}
                  <TableCell className="text-caption hidden font-mono lg:table-cell">
                    {e.resource_type} {e.resource_id ? `· ${e.resource_id.slice(0, 8)}…` : ""}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
        {next ? (
          <div className="flex justify-center border-t p-3">
            {moreError ? <ErrorState error={moreError} onRetry={loadMore} inline /> : null}
            <Button variant="outline" size="sm" disabled={loadingMore} onClick={() => void loadMore()}>
              {loadingMore ? "Loading…" : "Load more"}
            </Button>
          </div>
        ) : null}
      </Panel>
    </div>
  );
}
