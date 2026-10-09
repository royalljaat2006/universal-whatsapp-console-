"use client";

import { FileTextIcon, PlusIcon, RefreshCwIcon, XIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { DetailList, PageHeader, Panel, SearchInput, Toolbar } from "@/components/ui/page";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { LoadingButton } from "@/components/ui/spinner";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import { StatusBadge, toneFor } from "@/components/ui/status-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { useAction } from "@/hooks/use-action";
import { useApiQuery } from "@/hooks/use-api-query";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { presetRange } from "@/lib/analytics";
import { cn } from "@/lib/utils";
import { toApiError } from "@/lib/format";
import { analyticsService } from "@/services/analytics";
import { type CreateTemplateInput, templateService } from "@/services/templates";
import { whatsappService } from "@/services/whatsapp";
import { useSession } from "@/stores/session-context";
import type { BusinessAccount } from "@/types/api";

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "Never";
}

/**
 * Templates synced from Meta for the selected business's WABAs. Templates are
 * created and approved in WhatsApp Manager; this page shows exactly what the
 * last sync returned, including sync failures.
 */
export function TemplatesView() {
  const { business, hasPermission } = useSession();
  const toast = useToast();
  const [search, setSearch] = useState("");
  const q = useDebouncedValue(search.trim(), 300);
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const enabled = Boolean(business);
  const range = presetRange("30d", business?.timezone || undefined);

  const accounts = useApiQuery(
    () => whatsappService.accounts(),
    [business?.id],
    enabled && hasPermission("numbers.view"),
  );
  const templates = useApiQuery(
    () =>
      templateService.list({
        q: q || undefined,
        status: status || undefined,
        category: category || undefined,
      }),
    [business?.id, q, status, category],
    enabled,
  );
  const usage = useApiQuery(
    () => analyticsService.templates(range),
    [business?.id, range],
    enabled && hasPermission("analytics.view"),
  );
  const usageById = useMemo(
    () => new Map((usage.data?.templates ?? []).map((t) => [t.template_id, t.sends])),
    [usage.data],
  );

  if (!business) return null;

  async function sync() {
    setSyncing(true);
    try {
      const result = await templateService.sync();
      toast.success("Template sync requested", `${result.accounts} WhatsApp Business Account(s) queued.`);
      setTimeout(() => {
        templates.reload();
        accounts.reload();
      }, 3000);
    } catch (cause) {
      toast.error("Sync failed", toApiError(cause).message);
    } finally {
      setSyncing(false);
    }
  }

  const rows = templates.data?.results ?? [];
  const noAccounts = accounts.data && accounts.data.results.length === 0;
  const filtered = Boolean(q || status || category);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Templates"
        description="Message templates approved in WhatsApp Manager and synced from Meta."
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => templates.reload()}>
              <RefreshCwIcon aria-hidden /> Refresh
            </Button>
            {hasPermission("templates.manage") ? (
              <LoadingButton
                size="sm"
                loading={syncing}
                disabled={Boolean(noAccounts)}
                onClick={() => void sync()}
              >
                Sync from Meta
              </LoadingButton>
            ) : null}
            {hasPermission("templates.manage") ? (
              <Button size="sm" disabled={Boolean(noAccounts)} onClick={() => setCreating(true)}>
                <PlusIcon aria-hidden /> New template
              </Button>
            ) : null}
          </>
        }
      />

      {accounts.data?.results.length ? (
        <div className="flex flex-wrap gap-2">
          {accounts.data.results.map((a) => (
            <span
              key={a.id}
              className="bg-surface text-caption inline-flex items-center gap-2 rounded-md border px-2.5 py-1.5"
            >
              <span className="text-foreground font-medium">{a.name || a.external_id}</span>
              <StatusBadge tone={toneFor(a.template_sync_state)}>sync {a.template_sync_state}</StatusBadge>
              <span>{when(a.templates_last_synced_at)}</span>
              {a.template_sync_error ? (
                <span className="text-destructive">{a.template_sync_error}</span>
              ) : null}
            </span>
          ))}
        </div>
      ) : null}

      {noAccounts ? (
        <Panel>
          <EmptyState
            icon={FileTextIcon}
            title="No WhatsApp Business Account connected"
            description="Templates appear here after this business's WhatsApp Business Account is connected and synced."
          />
        </Panel>
      ) : (
        <Panel bodyClassName="overflow-x-auto">
          <Toolbar>
            <SearchInput
              aria-label="Search templates"
              className="w-full sm:w-72"
              placeholder="Search by name…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <NativeSelect
              aria-label="Status filter"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="">All statuses</option>
              {["APPROVED", "PENDING", "REJECTED", "PAUSED", "DISABLED"].map((s) => (
                <option key={s} value={s}>
                  {s.toLowerCase()}
                </option>
              ))}
            </NativeSelect>
            <NativeSelect
              aria-label="Category filter"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            >
              <option value="">All categories</option>
              {["MARKETING", "UTILITY", "AUTHENTICATION"].map((c) => (
                <option key={c} value={c}>
                  {c.toLowerCase()}
                </option>
              ))}
            </NativeSelect>
          </Toolbar>
          {templates.error ? (
            <ErrorState error={templates.error} onRetry={templates.reload} inline className="m-4" />
          ) : null}
          {!templates.data && !templates.error ? (
            <TableSkeleton columns={6} />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={FileTextIcon}
              title={filtered ? "No templates match" : "No templates synced yet"}
              description={
                filtered
                  ? "Try another search or filter."
                  : "Create templates in WhatsApp Manager, then sync from Meta."
              }
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Language</TableHead>
                  <TableHead className="hidden sm:table-cell">Category</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="hidden lg:table-cell">Last synced</TableHead>
                  <TableHead className="hidden text-right md:table-cell">Usage (30 days)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((t) => (
                  <TableRow key={t.id} className="cursor-pointer" onClick={() => setSelectedId(t.id)}>
                    <TableCell>
                      <button
                        type="button"
                        className="text-left font-medium"
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedId(t.id);
                        }}
                      >
                        {t.name}
                      </button>
                      {t.missing_from_provider ? (
                        <StatusBadge tone="danger" className="ml-2">
                          removed in Meta
                        </StatusBadge>
                      ) : null}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{t.language}</TableCell>
                    <TableCell className="hidden capitalize sm:table-cell">
                      {t.category.toLowerCase()}
                    </TableCell>
                    <TableCell>
                      <StatusBadge tone={toneFor(t.status)}>{t.status.toLowerCase()}</StatusBadge>
                    </TableCell>
                    <TableCell className="text-caption hidden lg:table-cell">
                      {when(t.last_synced_at)}
                    </TableCell>
                    <TableCell className="hidden text-right tabular-nums md:table-cell">
                      {hasPermission("analytics.view")
                        ? usage.data
                          ? (usageById.get(t.id) ?? 0).toLocaleString()
                          : "…"
                        : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Panel>
      )}

      <Sheet open={selectedId !== null} onOpenChange={(open) => !open && setSelectedId(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
          {selectedId ? <TemplateDrawer id={selectedId} /> : null}
        </SheetContent>
      </Sheet>

      <Sheet open={creating} onOpenChange={setCreating}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
          <SheetHeader className="border-b">
            <SheetTitle>New template</SheetTitle>
            <SheetDescription>
              Submitted to Meta for review — status starts PENDING until Meta approves or rejects it.
            </SheetDescription>
          </SheetHeader>
          <div className="px-4 pb-8">
            <TemplateCreateForm
              accounts={accounts.data?.results ?? []}
              onCancel={() => setCreating(false)}
              onCreated={() => {
                setCreating(false);
                templates.reload();
              }}
            />
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}

function TemplateCreateForm({
  accounts,
  onCancel,
  onCreated,
}: {
  accounts: BusinessAccount[];
  onCancel: () => void;
  onCreated: () => void;
}) {
  const toast = useToast();
  const { run, error, busy } = useAction(onCreated);
  const [businessAccountId, setBusinessAccountId] = useState(accounts[0]?.id ?? "");
  const [name, setName] = useState("");
  const [language, setLanguage] = useState("en_US");
  const [category, setCategory] = useState<CreateTemplateInput["category"]>("UTILITY");
  const [hasHeader, setHasHeader] = useState(false);
  const [headerText, setHeaderText] = useState("");
  const [bodyText, setBodyText] = useState("");
  const [footerText, setFooterText] = useState("");
  const [buttons, setButtons] = useState<string[]>([]);

  function buildComponents(): Record<string, unknown>[] {
    const components: Record<string, unknown>[] = [];
    if (hasHeader && headerText.trim()) {
      components.push({ type: "HEADER", format: "TEXT", text: headerText.trim() });
    }
    components.push({ type: "BODY", text: bodyText.trim() });
    if (footerText.trim()) {
      components.push({ type: "FOOTER", text: footerText.trim() });
    }
    const nonEmptyButtons = buttons.map((b) => b.trim()).filter(Boolean);
    if (nonEmptyButtons.length) {
      components.push({
        type: "BUTTONS",
        buttons: nonEmptyButtons.map((text) => ({ type: "QUICK_REPLY", text })),
      });
    }
    return components;
  }

  async function submit() {
    const ok = await run(() =>
      templateService.create({
        business_account_id: businessAccountId,
        name: name.trim(),
        language,
        category,
        components: buildComponents(),
      }),
    );
    if (ok) toast.success("Template submitted", "Meta will review it — check back for the status.");
  }

  const valid = businessAccountId && /^[a-z0-9_]+$/.test(name) && bodyText.trim().length > 0;

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      {error ? (
        <p className="border-destructive/25 bg-danger-soft rounded-md border px-3 py-2 text-sm">
          {error.message}
        </p>
      ) : null}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="tpl-account">WhatsApp Business Account</Label>
        <NativeSelect
          id="tpl-account"
          value={businessAccountId}
          onChange={(e) => setBusinessAccountId(e.target.value)}
        >
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name || a.external_id}
            </option>
          ))}
        </NativeSelect>
      </div>
      <div className="flex flex-wrap gap-2">
        <div className="flex min-w-48 flex-1 flex-col gap-1.5">
          <Label htmlFor="tpl-name">Name</Label>
          <Input
            id="tpl-name"
            required
            placeholder="order_confirmation"
            value={name}
            onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))}
          />
        </div>
        <div className="flex w-28 flex-col gap-1.5">
          <Label htmlFor="tpl-language">Language</Label>
          <Input
            id="tpl-language"
            required
            placeholder="en_US"
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
          />
        </div>
        <div className="flex w-36 flex-col gap-1.5">
          <Label htmlFor="tpl-category">Category</Label>
          <NativeSelect
            id="tpl-category"
            value={category}
            onChange={(e) => setCategory(e.target.value as CreateTemplateInput["category"])}
          >
            {["MARKETING", "UTILITY", "AUTHENTICATION"].map((c) => (
              <option key={c} value={c}>
                {c.toLowerCase()}
              </option>
            ))}
          </NativeSelect>
        </div>
      </div>

      <div className="flex flex-col gap-2 rounded-md border p-3">
        <label className="flex items-center gap-2 text-sm font-medium">
          <input type="checkbox" checked={hasHeader} onChange={(e) => setHasHeader(e.target.checked)} />
          Header (optional)
        </label>
        {hasHeader ? (
          <Input
            placeholder="Header text"
            value={headerText}
            onChange={(e) => setHeaderText(e.target.value)}
          />
        ) : null}
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="tpl-body">
          Body — use {"{{1}}"}, {"{{2}}"}, … for variables
        </Label>
        <textarea
          id="tpl-body"
          required
          rows={5}
          placeholder={"Hi {{1}}, your order #{{2}} has shipped."}
          value={bodyText}
          onChange={(e) => setBodyText(e.target.value)}
          className={cn(
            "min-h-24 w-full rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30",
          )}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="tpl-footer">Footer (optional)</Label>
        <Input
          id="tpl-footer"
          placeholder="Reply STOP to unsubscribe"
          value={footerText}
          onChange={(e) => setFooterText(e.target.value)}
        />
      </div>

      <div className="flex flex-col gap-2">
        <Label>Quick-reply buttons (optional)</Label>
        {buttons.map((b, i) => (
          <div key={i} className="flex gap-2">
            <Input
              value={b}
              placeholder="Button text"
              onChange={(e) =>
                setButtons((prev) => prev.map((v, idx) => (idx === i ? e.target.value : v)))
              }
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Remove button"
              onClick={() => setButtons((prev) => prev.filter((_, idx) => idx !== i))}
            >
              <XIcon aria-hidden />
            </Button>
          </div>
        ))}
        {buttons.length < 3 ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="self-start"
            onClick={() => setButtons((prev) => [...prev, ""])}
          >
            <PlusIcon aria-hidden /> Add button
          </Button>
        ) : null}
      </div>

      <div className="flex gap-2">
        <LoadingButton type="submit" loading={busy} disabled={!valid}>
          Submit for review
        </LoadingButton>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function TemplateDrawer({ id }: { id: string }) {
  const detail = useApiQuery(() => templateService.get(id), [id]);
  const t = detail.data?.id === id ? detail.data : undefined;
  const s = t?.structure;
  return (
    <>
      <SheetHeader className="border-b">
        <SheetTitle>{t?.name ?? "Template"}</SheetTitle>
        <SheetDescription>{t ? `${t.language} · ${t.category.toLowerCase()}` : " "}</SheetDescription>
      </SheetHeader>
      <div className="flex flex-col gap-6 px-4 pb-8">
        {detail.error ? <ErrorState error={detail.error} onRetry={detail.reload} inline /> : null}
        {!t && !detail.error ? <TableSkeleton rows={5} columns={2} /> : null}
        {t ? (
          <>
            <div className="flex flex-wrap gap-2">
              <StatusBadge tone={toneFor(t.status)}>{t.status.toLowerCase()}</StatusBadge>
              <StatusBadge tone={t.sendable ? "success" : "neutral"}>
                {t.sendable ? "sendable" : "not sendable"}
              </StatusBadge>
              {t.quality_score ? (
                <StatusBadge tone={toneFor(t.quality_score)}>
                  quality {t.quality_score.toLowerCase()}
                </StatusBadge>
              ) : null}
            </div>
            {s?.supported ? (
              <section aria-label="Preview" className="rounded-lg bg-[oklch(0.95_0.02_150)] p-4">
                <div className="bg-surface max-w-sm rounded-xl rounded-tl-sm p-3 text-sm shadow-xs">
                  {s.header_format === "TEXT" && s.header_text ? (
                    <p className="mb-1 font-semibold">{s.header_text}</p>
                  ) : null}
                  {s.header_format && s.header_format !== "TEXT" ? (
                    <p className="bg-muted text-muted-foreground mb-2 rounded-md px-2 py-6 text-center text-xs">
                      {s.header_format.toLowerCase()} header
                    </p>
                  ) : null}
                  <p className="leading-5 whitespace-pre-wrap">{s.body_text}</p>
                  {s.footer_text ? <p className="text-caption mt-2">{s.footer_text}</p> : null}
                </div>
                {s.buttons.length ? (
                  <div className="mt-1.5 flex max-w-sm flex-col gap-1">
                    {s.buttons.map((b, i) => (
                      <span
                        key={i}
                        className="bg-surface text-info rounded-lg px-3 py-1.5 text-center text-sm font-medium shadow-xs"
                      >
                        {b.text}
                      </span>
                    ))}
                  </div>
                ) : null}
              </section>
            ) : s ? (
              <p className="border-destructive/25 bg-danger-soft rounded-md border px-3 py-2 text-sm">
                {s.reason}
              </p>
            ) : null}
            {s?.supported ? (
              <section>
                <h3 className="text-section-title mb-2">Variables</h3>
                {s.parameters.length === 0 ? (
                  <p className="text-supporting">This template has no variables.</p>
                ) : (
                  <ul className="divide-y rounded-md border">
                    {s.parameters.map((p) => (
                      <li
                        key={`${p.component}-${p.key}`}
                        className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
                      >
                        <span className="font-mono text-xs">
                          {p.component}.{p.key}
                        </span>
                        <span className="text-caption">
                          {p.kind}
                          {p.example ? ` · e.g. ${p.example}` : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            ) : null}
            <section>
              <h3 className="text-section-title mb-2">Meta status</h3>
              <DetailList
                items={[
                  { label: "Status", value: t.status },
                  { label: "Parameter format", value: t.parameter_format || "POSITIONAL" },
                  { label: "Quality", value: t.quality_score || "Not reported" },
                  {
                    label: "WABA",
                    value: <span className="font-mono text-xs">{t.business_account_external_id}</span>,
                  },
                  { label: "Last synced", value: when(t.last_synced_at) },
                  ...(t.rejected_reason && t.rejected_reason !== "NONE"
                    ? [
                        {
                          label: "Rejected reason",
                          value: <span className="text-destructive">{t.rejected_reason}</span>,
                        },
                      ]
                    : []),
                ]}
              />
            </section>
          </>
        ) : null}
      </div>
    </>
  );
}
