"use client";

import { KeyRoundIcon, ScrollTextIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { DetailList, Panel, SearchInput } from "@/components/ui/page";
import { LoadingButton } from "@/components/ui/spinner";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import { StatusBadge, toneFor } from "@/components/ui/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { useApiQuery } from "@/hooks/use-api-query";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { api } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { authService } from "@/services/auth";
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

export function AccountSettings() {
  const { user } = useSession();
  const router = useRouter();
  const toast = useToast();
  const [resending, setResending] = useState(false);
  const [verified, setVerified] = useState(user?.is_email_verified ?? false);
  const [password, setPassword] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<ApiError | null>(null);

  async function resend() {
    setResending(true);
    try {
      await authService.resendEmailVerification();
      toast.success("Verification email sent");
    } catch (cause) {
      toast.error("Couldn't send it", cause instanceof ApiError ? cause.message : String(cause));
    } finally {
      setResending(false);
    }
  }

  async function deleteAccount(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !window.confirm(
        "Delete your account? This cannot be undone. You'll be signed out immediately.",
      )
    ) {
      return;
    }
    setDeleting(true);
    setDeleteError(null);
    try {
      await authService.deleteAccount(password);
      router.replace("/login");
    } catch (cause) {
      setDeleteError(cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause)));
    } finally {
      setDeleting(false);
    }
  }

  if (!user) return null;

  return (
    <div className="flex flex-col gap-4">
      <Panel title="Your account" bodyClassName="p-4">
        <DetailList
          items={[
            { label: "Name", value: user.full_name || "—" },
            { label: "Email", value: user.email },
            {
              label: "Email status",
              value: verified ? (
                <StatusBadge tone="success">verified</StatusBadge>
              ) : (
                <span className="flex items-center gap-2">
                  <StatusBadge tone="warning">unverified</StatusBadge>
                  <Button
                    size="xs"
                    variant="outline"
                    disabled={resending}
                    onClick={() =>
                      void resend().then(() => {
                        // The link itself flips this — this just confirms the send.
                        setVerified(user.is_email_verified);
                      })
                    }
                  >
                    {resending ? "Sending…" : "Resend verification"}
                  </Button>
                </span>
              ),
            },
          ]}
        />
      </Panel>
      <Panel
        title="Delete account"
        description="Permanent. You must transfer or delete ownership of any organization you're the sole owner of first."
        bodyClassName="p-4"
      >
        <form onSubmit={(e) => void deleteAccount(e)} className="flex max-w-sm flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="delete-password">Confirm your password</Label>
            <Input
              id="delete-password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {deleteError ? <ErrorState error={deleteError} inline /> : null}
          <LoadingButton type="submit" variant="destructive" loading={deleting} className="w-fit">
            Delete my account
          </LoadingButton>
        </form>
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
