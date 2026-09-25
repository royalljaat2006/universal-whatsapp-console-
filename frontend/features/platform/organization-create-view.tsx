"use client";

import { ArrowLeftIcon, CheckIcon, CopyIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { DetailList, PageHeader, Panel } from "@/components/ui/page";
import { LoadingButton } from "@/components/ui/spinner";
import { ErrorState } from "@/components/ui/states";
import { platformService } from "@/services/platform";
import { ApiError } from "@/lib/api/errors";
import type { OrgRoleKey, ProvisionedOrganization } from "@/types/api";

const ROLES: { value: OrgRoleKey; label: string; description: string }[] = [
  { value: "owner", label: "Owner", description: "Full control of their own organization, including billing and roles." },
  { value: "admin", label: "Admin", description: "Full operational control except billing management." },
  { value: "manager", label: "Manager", description: "Runs messaging operations: templates, campaigns, assignment." },
  { value: "agent", label: "Agent", description: "Handles conversations and replies to customers." },
  { value: "viewer", label: "Viewer", description: "Read-only access to operational data." },
];

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API unavailable (e.g. insecure context) — the value is
      // still selectable/visible in the input for a manual copy.
    }
  }
  return (
    <div className="flex flex-col gap-1.5">
      <Label>{label}</Label>
      <div className="flex gap-2">
        <Input readOnly value={value} className="font-mono text-xs" onFocus={(e) => e.target.select()} />
        <Button type="button" variant="outline" size="icon" aria-label={`Copy ${label}`} onClick={() => void copy()}>
          {copied ? <CheckIcon aria-hidden className="text-success" /> : <CopyIcon aria-hidden />}
        </Button>
      </div>
    </div>
  );
}

function ProvisionedResult({ result, onDone }: { result: ProvisionedOrganization; onDone: () => void }) {
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  // The backend is deliberately prefix-unaware (see docs/productization/
  // tenant-isolation.md) — accept_path never includes a subpath deployment's
  // BASE_PATH, so it has to be added here, the same way lib/api/client.ts does.
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
  const acceptUrl = `${origin}${basePath}${result.invitation.accept_path}`;
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title={`${result.name} created`}
        description="Share this link with the invited user — it works exactly once and expires in 7 days."
      />
      <Panel title="Signup link" bodyClassName="flex flex-col gap-4 p-4">
        <DetailList
          items={[
            { label: "Organization", value: <span className="font-mono">{result.slug}</span> },
            { label: "Invited email", value: result.invitation.email },
            {
              label: "Starting role",
              value: ROLES.find((r) => r.value === result.invitation.role)?.label ?? result.invitation.role,
            },
          ]}
        />
        <CopyField label="Signup link (send this to the invitee)" value={acceptUrl} />
        <p className="text-caption">
          No email was sent automatically — email delivery isn&rsquo;t configured yet. Share this link
          directly (Slack, WhatsApp, etc.). It cannot be retrieved again once you leave this page.
        </p>
      </Panel>
      <Button variant="outline" className="w-fit" onClick={onDone}>
        <ArrowLeftIcon aria-hidden /> Back to organizations
      </Button>
    </div>
  );
}

export function OrganizationCreate({ onBack, onCreated }: { onBack: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [roleKey, setRoleKey] = useState<OrgRoleKey>("owner");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [result, setResult] = useState<ProvisionedOrganization | null>(null);

  if (result) {
    return (
      <ProvisionedResult
        result={result}
        onDone={() => {
          onCreated();
        }}
      />
    );
  }

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const created = await platformService.organizations.provision({
        name: name.trim(),
        owner_email: email.trim(),
        role_key: roleKey,
      });
      setResult(created);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause)));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <Button variant="ghost" size="sm" onClick={onBack} className="w-fit">
        <ArrowLeftIcon aria-hidden /> Organizations
      </Button>
      <PageHeader
        title="New organization"
        description="Creates the organization with no members yet, then sends a one-time signup link for the first user — they set their own password on acceptance."
      />
      <Panel bodyClassName="p-4">
        <form onSubmit={(e) => void onSubmit(e)} className="flex max-w-md flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="org-name">Organization name</Label>
            <Input id="org-name" required value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="owner-email">First user&rsquo;s email</Label>
            <Input
              id="owner-email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="role-key">Starting role</Label>
            <NativeSelect
              id="role-key"
              value={roleKey}
              onChange={(e) => setRoleKey(e.target.value as OrgRoleKey)}
            >
              {ROLES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </NativeSelect>
            <p className="text-caption">{ROLES.find((r) => r.value === roleKey)?.description}</p>
            {roleKey !== "owner" ? (
              <p className="text-caption text-warning">
                A non-owner starting role means this person cannot invite teammates or manage roles
                themselves (missing members.manage / roles.manage) — only give this if that&rsquo;s
                intentional.
              </p>
            ) : null}
          </div>
          {error ? <ErrorState error={error} inline /> : null}
          <LoadingButton type="submit" loading={submitting} className="w-fit">
            Create organization
          </LoadingButton>
        </form>
      </Panel>
    </div>
  );
}
