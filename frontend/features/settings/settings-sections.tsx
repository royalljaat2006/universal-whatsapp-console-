"use client";

import { CheckIcon, CopyIcon, KeyRoundIcon, ScrollTextIcon, UserPlusIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { DetailList, Panel, SearchInput } from "@/components/ui/page";
import { LoadingButton } from "@/components/ui/spinner";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import { StatusBadge, toneFor } from "@/components/ui/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { useAction } from "@/hooks/use-action";
import { useApiQuery } from "@/hooks/use-api-query";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { api } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { authService } from "@/services/auth";
import { businessService } from "@/services/businesses";
import { teamInvitationService } from "@/services/invitations";
import { organizationService } from "@/services/organizations";
import { useSession } from "@/stores/session-context";
import type { CreatedInvitation, CursorPage, OrgRoleKey } from "@/types/api";

export function Workspace() {
  const { active, businesses, user, hasPermission, refreshBusinesses } = useSession();
  const toast = useToast();
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const canCreate = hasPermission("businesses.manage");

  async function createBusiness(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = name.trim();
    if (!value) return;
    setCreating(true);
    setError(null);
    try {
      const business = await businessService.create(value);
      refreshBusinesses(business.id);
      setName("");
      toast.success("Business created", business.name);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause)));
    } finally {
      setCreating(false);
    }
  }

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
        {businesses.length === 0 ? (
          <p className="text-supporting px-4 pt-3">
            No businesses yet. Create one to start connecting WhatsApp numbers.
          </p>
        ) : null}
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
        {canCreate ? (
          <form
            onSubmit={(e) => void createBusiness(e)}
            className="flex flex-wrap items-end gap-2 border-t p-4"
          >
            <div className="flex flex-1 flex-col gap-1.5">
              <Label htmlFor="new-business-name">New business name</Label>
              <Input
                id="new-business-name"
                required
                maxLength={120}
                placeholder="e.g. Acme Retail"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <LoadingButton type="submit" loading={creating} disabled={!name.trim()}>
              Create business
            </LoadingButton>
          </form>
        ) : null}
        {error ? <ErrorState error={error} inline className="mx-4 mb-4" /> : null}
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

const INVITABLE_ROLES: { value: OrgRoleKey; label: string }[] = [
  { value: "admin", label: "Admin" },
  { value: "manager", label: "Manager" },
  { value: "agent", label: "Agent" },
  { value: "viewer", label: "Viewer" },
  // "owner" is deliberately excluded — granting full billing/ownership
  // control isn't a casual invite-form action.
];

function CopyLink({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API unavailable — the value is still selectable for a manual copy.
    }
  }
  return (
    <div className="flex gap-2">
      <Input readOnly value={value} className="font-mono text-xs" onFocus={(e) => e.target.select()} />
      <Button type="button" variant="outline" size="icon" aria-label="Copy link" onClick={() => void copy()}>
        {copied ? <CheckIcon aria-hidden className="text-success" /> : <CopyIcon aria-hidden />}
      </Button>
    </div>
  );
}

/** Invite teammates to THIS organization and manage pending invites. Distinct
 * from `BusinessAccess` (per-business grants for existing members). */
export function Members({ canManage }: { canManage: boolean }) {
  const members = useApiQuery(() => organizationService.members(), []);
  const invitations = useApiQuery(() => teamInvitationService.list(), []);
  const [email, setEmail] = useState("");
  const [roleKey, setRoleKey] = useState<OrgRoleKey>("agent");
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState<ApiError | null>(null);
  const [justCreated, setJustCreated] = useState<CreatedInvitation | null>(null);
  const { run: runRevoke, error: revokeError, busy: revoking } = useAction(invitations.reload);

  async function invite(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = email.trim();
    if (!value) return;
    setInviting(true);
    setInviteError(null);
    try {
      const created = await teamInvitationService.create(value, roleKey);
      setJustCreated(created);
      setEmail("");
      invitations.reload();
    } catch (cause) {
      setInviteError(cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause)));
    } finally {
      setInviting(false);
    }
  }

  const origin = typeof window !== "undefined" ? window.location.origin : "";
  // The backend is deliberately prefix-unaware — accept_path never includes a
  // subpath deployment's BASE_PATH, so it has to be added here.
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

  return (
    <div className="flex flex-col gap-4">
      <Panel title="Members" description="People with a membership in this organization.">
        {members.error ? (
          <ErrorState error={members.error} onRetry={members.reload} inline className="m-4" />
        ) : null}
        {!members.data && !members.error ? <TableSkeleton rows={3} columns={3} /> : null}
        {members.data ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Role</TableHead>
                <TableHead className="hidden md:table-cell">Joined</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.data.results.map((m) => (
                <TableRow key={m.id}>
                  <TableCell>
                    <span className="font-medium">{m.full_name || m.email}</span>
                    <span className="text-caption block">{m.email}</span>
                  </TableCell>
                  <TableCell className="capitalize">{m.role}</TableCell>
                  <TableCell className="text-caption hidden md:table-cell">
                    {m.joined_at ? new Date(m.joined_at).toLocaleDateString() : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </Panel>

      {canManage ? (
        <Panel title="Invite a teammate" bodyClassName="flex flex-col gap-4 p-4">
          <form onSubmit={(e) => void invite(e)} className="flex flex-wrap items-end gap-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                required
                className="w-64"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="invite-role">Role</Label>
              <NativeSelect
                id="invite-role"
                value={roleKey}
                onChange={(e) => setRoleKey(e.target.value as OrgRoleKey)}
              >
                {INVITABLE_ROLES.map((r) => (
                  <option key={r.value} value={r.value}>
                    {r.label}
                  </option>
                ))}
              </NativeSelect>
            </div>
            <LoadingButton type="submit" loading={inviting} disabled={!email.trim()}>
              <UserPlusIcon aria-hidden /> Send invite
            </LoadingButton>
          </form>
          {inviteError ? <ErrorState error={inviteError} inline /> : null}
          {justCreated ? (
            <div className="bg-muted/40 flex flex-col gap-2 rounded-md border p-3">
              <p className="text-sm">
                Invite created for <span className="font-medium">{justCreated.email}</span>. No email was
                sent automatically — share this link directly. It cannot be retrieved again once you leave
                this page.
              </p>
              <CopyLink value={`${origin}${basePath}${justCreated.accept_path}`} />
            </div>
          ) : null}
        </Panel>
      ) : null}

      <Panel title="Pending invitations">
        {invitations.error ? (
          <ErrorState error={invitations.error} onRetry={invitations.reload} inline className="m-4" />
        ) : null}
        {revokeError ? <ErrorState error={revokeError} inline className="mx-4 mt-2" /> : null}
        {!invitations.data && !invitations.error ? <TableSkeleton rows={2} columns={3} /> : null}
        {invitations.data && invitations.data.length === 0 ? (
          <EmptyState compact icon={UserPlusIcon} title="No invitations" description="Invites you send appear here." />
        ) : null}
        {invitations.data && invitations.data.length ? (
          <ul className="divide-y">
            {invitations.data.map((inv) => (
              <li key={inv.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
                <span>
                  <span className="font-medium">{inv.email}</span>
                  <span className="text-caption block capitalize">
                    {inv.role} · invited by {inv.invited_by_email}
                  </span>
                </span>
                <span className="flex items-center gap-2">
                  <StatusBadge tone={toneFor(inv.status)}>{inv.status}</StatusBadge>
                  {canManage && inv.status === "pending" ? (
                    <Button
                      size="xs"
                      variant="ghost"
                      disabled={revoking}
                      onClick={() => void runRevoke(() => teamInvitationService.revoke(inv.id))}
                    >
                      Revoke
                    </Button>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
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
