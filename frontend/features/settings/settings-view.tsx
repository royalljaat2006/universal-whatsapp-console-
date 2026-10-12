"use client";

import {
  Building2Icon,
  CodeIcon,
  KeyRoundIcon,
  type LucideIcon,
  ScrollTextIcon,
  ShieldIcon,
  SmartphoneIcon,
  TagIcon,
  UserIcon,
  UserPlusIcon,
  UsersIcon,
} from "lucide-react";
import { useState } from "react";

import { ApiErrorNotice } from "@/components/status/api-error-notice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader, Panel } from "@/components/ui/page";
import { Input } from "@/components/ui/input";
import { useAction } from "@/hooks/use-action";
import { useApiQuery } from "@/hooks/use-api-query";
import { accessService, memberScopeService, teamService } from "@/services/access";
import { tagService } from "@/services/contacts";
import { cn } from "@/lib/utils";
import { useSession } from "@/stores/session-context";
import { useUiMode } from "@/stores/ui-mode";

import {
  AccountSettings,
  ApiKeys,
  AuditLog,
  Members,
  Roles,
  WhatsAppSettings,
  Workspace,
} from "./settings-sections";

type SectionId =
  | "workspace"
  | "account"
  | "members"
  | "roles"
  | "access"
  | "apikeys"
  | "whatsapp"
  | "teams"
  | "tags"
  | "audit";

export function SettingsView() {
  const { business, hasPermission } = useSession();
  const { isSimple, setMode } = useUiMode();
  const sections: {
    id: SectionId;
    label: string;
    icon: LucideIcon;
    allowed: boolean;
    /** Technical: only listed in Advanced mode (permissions still apply in both). */
    advanced?: boolean;
  }[] = [
    { id: "workspace", label: "Workspace", icon: Building2Icon, allowed: hasPermission("organization.view") },
    // Always available — this is about the signed-in person, not an
    // organization permission.
    { id: "account", label: "Your account", icon: UserIcon, allowed: true },
    { id: "members", label: "Members", icon: UserPlusIcon, allowed: hasPermission("members.view") },
    { id: "roles", label: "Roles", icon: ShieldIcon, allowed: hasPermission("roles.manage"), advanced: true },
    {
      id: "access",
      label: "Business access",
      icon: KeyRoundIcon,
      allowed: hasPermission("members.manage"),
      advanced: true,
    },
    {
      id: "apikeys",
      label: "API keys",
      icon: CodeIcon,
      allowed: hasPermission("api_keys.manage"),
      advanced: true,
    },
    {
      id: "whatsapp",
      label: "WhatsApp",
      icon: SmartphoneIcon,
      allowed: hasPermission("integrations.manage"),
      advanced: true,
    },
    { id: "teams", label: "Teams", icon: UsersIcon, allowed: hasPermission("teams.view") },
    { id: "tags", label: "Tags", icon: TagIcon, allowed: hasPermission("contacts.view") },
    {
      id: "audit",
      label: "Audit log",
      icon: ScrollTextIcon,
      allowed: hasPermission("audit.view"),
      advanced: true,
    },
  ];
  const permitted = sections.filter((section) => section.allowed);
  const available = permitted.filter((section) => !isSimple || !section.advanced);
  const hiddenInSimple = permitted.length - available.length;
  const [current, setCurrent] = useState<SectionId>(available[0]?.id ?? "workspace");
  const active = available.find((section) => section.id === current) ?? available[0];

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Settings" description="Workspace configuration, access and audit history." />
      <div className="grid gap-5 md:grid-cols-[13rem_1fr]">
        <nav aria-label="Settings sections" className="flex gap-1 overflow-x-auto md:flex-col">
          {available.map((section) => {
            const Icon = section.icon;
            const selected = section.id === active?.id;
            return (
              <button
                key={section.id}
                type="button"
                aria-current={selected ? "page" : undefined}
                onClick={() => setCurrent(section.id)}
                className={cn(
                  "focus-visible:ring-ring/50 flex h-9 shrink-0 items-center gap-2.5 rounded-md px-3 text-left text-[13px] font-medium outline-none focus-visible:ring-2",
                  selected
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <Icon aria-hidden className={cn("size-4", selected && "text-primary")} />
                {section.label}
              </button>
            );
          })}
          {isSimple && hiddenInSimple > 0 ? (
            <div className="text-caption mt-2 hidden rounded-md border border-dashed px-3 py-2 md:block">
              {hiddenInSimple} more technical setting{hiddenInSimple === 1 ? "" : "s"} (roles, API keys,
              audit log…) are in Advanced mode.
              <button
                type="button"
                className="text-primary mt-1 block font-medium hover:underline"
                onClick={() => setMode("advanced")}
              >
                Switch to Advanced
              </button>
            </div>
          ) : null}
        </nav>
        <div className="min-w-0">
          {active?.id === "workspace" ? <Workspace /> : null}
          {active?.id === "account" ? <AccountSettings /> : null}
          {active?.id === "members" ? <Members canManage={hasPermission("members.manage")} /> : null}
          {active?.id === "roles" ? <Roles canManage={hasPermission("roles.manage")} /> : null}
          {active?.id === "access" ? (
            business ? (
              <BusinessAccess businessId={business.id} />
            ) : (
              <p className="text-supporting">Create a business to manage its access.</p>
            )
          ) : null}
          {active?.id === "apikeys" ? <ApiKeys canManage={hasPermission("api_keys.manage")} /> : null}
          {active?.id === "whatsapp" ? <WhatsAppSettings /> : null}
          {active?.id === "teams" ? <Teams canManage={hasPermission("teams.manage")} /> : null}
          {active?.id === "tags" ? (
            business ? (
              <TagManager businessId={business.id} canManage={hasPermission("contacts.manage")} />
            ) : (
              <p className="text-supporting">Create a business to manage its tags.</p>
            )
          ) : null}
          {active?.id === "audit" ? <AuditLog /> : null}
        </div>
      </div>
    </div>
  );
}

/**
 * Who may open this business. The backend refuses self-changes and changes to
 * owners (ACCESS_CHANGE_FORBIDDEN); ``manageable`` only hides those buttons.
 */
function BusinessAccess({ businessId }: { businessId: string }) {
  const access = useApiQuery(() => accessService.list(businessId), [businessId]);
  const { run, error, busy } = useAction(access.reload);

  return (
    <Panel
      title="Business access"
      description={
        <>
          Members with &quot;all businesses&quot; scope see every business. Others see only businesses granted
          here. Revoking from an all-businesses member converts them to explicit grants for the remaining
          businesses.
        </>
      }
      bodyClassName="flex flex-col gap-3 p-4"
    >
      {access.error ? <ApiErrorNotice error={access.error} /> : null}
      {error ? <ApiErrorNotice error={error} /> : null}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-muted-foreground text-left text-xs">
            <tr>
              <th className="py-1 pr-3">Member</th>
              <th className="py-1 pr-3">Role</th>
              <th className="py-1 pr-3">Scope</th>
              <th className="py-1 pr-3">Access</th>
              <th className="py-1" />
            </tr>
          </thead>
          <tbody className="divide-y">
            {access.data?.members.map((m) => (
              <tr key={m.membership_id}>
                <td className="py-2 pr-3">
                  <span className="font-medium">{m.name || m.email}</span>
                  <span className="text-muted-foreground block text-xs">{m.email}</span>
                </td>
                <td className="py-2 pr-3 text-xs">{m.role}</td>
                <td className="py-2 pr-3 text-xs">
                  {m.business_scope === "all" ? "All businesses" : "Selected"}
                </td>
                <td className="py-2 pr-3">
                  <Badge variant={m.has_access ? "default" : "outline"}>
                    {m.has_access
                      ? m.access_via === "all_businesses"
                        ? "via all-businesses"
                        : "granted"
                      : "no access"}
                  </Badge>
                  {m.is_self ? <span className="text-muted-foreground ml-1 text-xs">(you)</span> : null}
                </td>
                <td className="py-2 text-right">
                  {m.manageable ? (
                    <span className="flex justify-end gap-1">
                      {m.has_access ? (
                        <Button
                          size="xs"
                          variant="outline"
                          disabled={busy}
                          onClick={() => void run(() => accessService.revoke(businessId, m.membership_id))}
                        >
                          Revoke
                        </Button>
                      ) : (
                        <Button
                          size="xs"
                          disabled={busy}
                          onClick={() => void run(() => accessService.grant(businessId, m.membership_id))}
                        >
                          Grant
                        </Button>
                      )}
                      <Button
                        size="xs"
                        variant="ghost"
                        disabled={busy}
                        onClick={() =>
                          void run(() =>
                            memberScopeService.setScope(
                              m.membership_id,
                              m.business_scope === "all" ? "selected" : "all",
                            ),
                          )
                        }
                      >
                        {m.business_scope === "all" ? "Limit scope" : "Allow all"}
                      </Button>
                    </span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function TagManager({ businessId, canManage }: { businessId: string; canManage: boolean }) {
  const tags = useApiQuery(() => tagService.list(), [businessId]);
  const { run, error, busy } = useAction(tags.reload);
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<{ id: string; name: string } | null>(null);

  return (
    <Panel
      title="Tags"
      description={<>Tags belong to this business only; names are unique ignoring case.</>}
      bodyClassName="flex flex-col gap-3 p-4"
    >
      {tags.error ? <ApiErrorNotice error={tags.error} /> : null}
      {error ? <ApiErrorNotice error={error} /> : null}
      {canManage ? (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const value = name.trim();
            if (value) void run(() => tagService.create(value)).then((ok) => ok && setName(""));
          }}
        >
          <Input
            aria-label="New tag name"
            className="max-w-xs"
            maxLength={50}
            placeholder="New tag"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button type="submit" disabled={busy || !name.trim()}>
            Add tag
          </Button>
        </form>
      ) : null}
      {tags.data?.length === 0 ? <p className="text-muted-foreground text-sm">No tags yet.</p> : null}
      <ul className="flex flex-col divide-y text-sm">
        {tags.data?.map((t) => (
          <li key={t.id} className="flex items-center justify-between gap-2 py-1.5">
            {editing?.id === t.id ? (
              <form
                className="flex gap-1"
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(() => tagService.rename(t.id, editing.name.trim())).then(
                    (ok) => ok && setEditing(null),
                  );
                }}
              >
                <Input
                  aria-label="Tag name"
                  className="h-7"
                  maxLength={50}
                  value={editing.name}
                  onChange={(e) => setEditing({ id: t.id, name: e.target.value })}
                />
                <Button size="xs" type="submit" disabled={busy}>
                  Save
                </Button>
                <Button size="xs" variant="ghost" type="button" onClick={() => setEditing(null)}>
                  Cancel
                </Button>
              </form>
            ) : (
              <Badge variant="outline">{t.name}</Badge>
            )}
            {canManage && editing?.id !== t.id ? (
              <span className="flex gap-1">
                <Button size="xs" variant="ghost" onClick={() => setEditing({ id: t.id, name: t.name })}>
                  Rename
                </Button>
                <Button
                  size="xs"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => {
                    if (
                      window.confirm(
                        `Delete tag "${t.name}"? It is removed from all contacts and conversations.`,
                      )
                    ) {
                      void run(() => tagService.remove(t.id));
                    }
                  }}
                >
                  Delete
                </Button>
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function Teams({ canManage }: { canManage: boolean }) {
  const teams = useApiQuery(() => teamService.list(), []);
  const { run, error, busy } = useAction(teams.reload);
  const [name, setName] = useState("");

  return (
    <Panel
      title="Teams"
      description={<>Organization-wide teams used when assigning conversations.</>}
      bodyClassName="flex flex-col gap-3 p-4"
    >
      {teams.error ? <ApiErrorNotice error={teams.error} /> : null}
      {error ? <ApiErrorNotice error={error} /> : null}
      {canManage ? (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const value = name.trim();
            if (value) void run(() => teamService.create(value)).then((ok) => ok && setName(""));
          }}
        >
          <Input
            aria-label="New team name"
            className="max-w-xs"
            maxLength={120}
            placeholder="New team"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Button type="submit" disabled={busy || !name.trim()}>
            Add team
          </Button>
        </form>
      ) : null}
      {teams.data?.length === 0 ? <p className="text-muted-foreground text-sm">No teams yet.</p> : null}
      <ul className="flex flex-col divide-y text-sm">
        {teams.data?.map((t) => (
          <li key={t.id} className="flex justify-between py-1.5">
            <span>{t.name}</span>
            <span className="text-muted-foreground text-xs">{t.member_ids.length} member(s)</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
