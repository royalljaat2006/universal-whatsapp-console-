"use client";

import { Building2Icon } from "lucide-react";
import { useMemo, useState } from "react";

import { PageHeader, Panel, SearchInput, Toolbar } from "@/components/ui/page";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApiQuery } from "@/hooks/use-api-query";
import { platformService } from "@/services/platform";

import { OrganizationDetail } from "./organization-detail-view";

type Mode = { kind: "list" } | { kind: "detail"; id: string };

export function OrganizationsView() {
  const [mode, setMode] = useState<Mode>({ kind: "list" });
  const [q, setQ] = useState("");
  const orgs = useApiQuery(() => platformService.organizations.list(), []);
  const filtered = useMemo(() => {
    const rows = orgs.data ?? [];
    const needle = q.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter(
      (o) => o.name.toLowerCase().includes(needle) || o.slug.toLowerCase().includes(needle),
    );
  }, [orgs.data, q]);

  if (mode.kind === "detail") {
    return <OrganizationDetail organizationId={mode.id} onBack={() => setMode({ kind: "list" })} />;
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Organizations"
        description="Every tenant on the platform. Suspending an organization locks out all of its members immediately."
      />
      <Panel bodyClassName="overflow-x-auto">
        <Toolbar>
          <SearchInput
            aria-label="Filter organizations"
            placeholder="Filter by name or slug…"
            className="w-64"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </Toolbar>
        {orgs.error ? <ErrorState error={orgs.error} onRetry={orgs.reload} inline className="m-4" /> : null}
        {!orgs.data && !orgs.error ? <TableSkeleton columns={6} /> : null}
        {orgs.data && filtered.length === 0 ? (
          <EmptyState
            compact
            icon={Building2Icon}
            title={q ? "No matching organizations" : "No organizations yet"}
          />
        ) : null}
        {filtered.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Organization</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Members</TableHead>
                <TableHead className="text-right">Businesses</TableHead>
                <TableHead className="hidden md:table-cell">Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((org) => (
                <TableRow key={org.id}>
                  <TableCell>
                    <button
                      type="button"
                      onClick={() => setMode({ kind: "detail", id: org.id })}
                      className="text-primary block font-medium hover:underline"
                    >
                      {org.name}
                    </button>
                    <span className="text-caption block font-mono">{org.slug}</span>
                  </TableCell>
                  <TableCell>
                    <StatusBadge tone={org.status === "active" ? "success" : "danger"}>
                      {org.status}
                    </StatusBadge>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{org.member_count}</TableCell>
                  <TableCell className="text-right tabular-nums">{org.business_count}</TableCell>
                  <TableCell className="text-caption hidden md:table-cell">
                    {new Date(org.created_at).toLocaleDateString()}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </Panel>
    </div>
  );
}
