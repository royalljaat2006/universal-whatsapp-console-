"use client";

import { useState } from "react";

import { ApiErrorNotice } from "@/components/status/api-error-notice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { useApiQuery } from "@/hooks/use-api-query";
import type { ApiError } from "@/lib/api/errors";
import { toApiError } from "@/lib/format";
import { renderPreview } from "@/lib/template-preview";
import { type CampaignInput, campaignService } from "@/services/campaigns";
import { mediaService } from "@/services/media";
import { templateService } from "@/services/templates";
import { whatsappService } from "@/services/whatsapp";
import { useSession } from "@/stores/session-context";
import type {
  AudienceDefinition,
  Campaign,
  LaunchPreview,
  TemplateParameterSpec,
  VariableMapping,
  VariableMappingEntry,
} from "@/types/api";

import { AudienceBuilder } from "./audience-builder";

const SOURCES = [
  { value: "contact.name", label: "Contact name" },
  { value: "contact.display_name", label: "Display name" },
  { value: "contact.profile_name", label: "WhatsApp profile name" },
  { value: "contact.phone_number", label: "Phone number" },
  { value: "contact.attribute", label: "Contact attribute" },
  { value: "static", label: "Fixed text" },
];

function entryFor(mapping: VariableMapping, spec: TemplateParameterSpec): VariableMappingEntry | undefined {
  if (spec.component === "header") return mapping.header;
  const section = spec.component === "body" ? mapping.body : mapping.buttons;
  return section?.[spec.key];
}

function withEntry(
  mapping: VariableMapping,
  spec: TemplateParameterSpec,
  entry: VariableMappingEntry,
): VariableMapping {
  if (spec.component === "header") return { ...mapping, header: entry };
  const key = spec.component === "body" ? "body" : "buttons";
  return { ...mapping, [key]: { ...(mapping[key] ?? {}), [spec.key]: entry } };
}

/** Local datetime-local value → ISO string without offset (backend applies the campaign timezone). */
function toLocalInput(iso: string | null, timeZone: string): string {
  if (!iso) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timeZone || undefined,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}

/**
 * Create or edit a DRAFT campaign. Everything is validated again by the
 * backend on save and on launch; launch requires an explicit confirmation
 * after the backend's launch checks have been shown.
 */
export function CampaignEditor({
  campaign,
  onSaved,
  onLaunched,
  onCancel,
}: {
  campaign: Campaign | null;
  onSaved: (c: Campaign) => void;
  onLaunched: (c: Campaign) => void;
  onCancel: () => void;
}) {
  const { business, active, hasPermission } = useSession();
  const defaultTz = business?.timezone || active?.organization.timezone || "UTC";
  const [name, setName] = useState(campaign?.name ?? "");
  const [numberId, setNumberId] = useState(campaign?.phone_number_id ?? "");
  const [templateId, setTemplateId] = useState(campaign?.template_id ?? "");
  const [mapping, setMapping] = useState<VariableMapping>(campaign?.variable_mapping ?? {});
  const [audience, setAudience] = useState<AudienceDefinition>(
    campaign?.audience_definition ?? { match: "all", conditions: [] },
  );
  const [rate, setRate] = useState(campaign?.max_messages_per_minute ?? 60);
  const [timezone, setTimezone] = useState(campaign?.timezone || defaultTz);
  const [when, setWhen] = useState<"now" | "later">(campaign?.scheduled_at ? "later" : "now");
  const [scheduled, setScheduled] = useState(
    toLocalInput(campaign?.scheduled_at ?? null, campaign?.timezone || defaultTz),
  );
  const [saved, setSaved] = useState<Campaign | null>(campaign);
  const [preview, setPreview] = useState<LaunchPreview | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const numbers = useApiQuery(() => whatsappService.numbers(), [business?.id], Boolean(business));
  const number = numbers.data?.results.find((n) => n.id === numberId);
  const templates = useApiQuery(
    () => templateService.list({ sendable: "1", business_account: number?.business_account_id }),
    [number?.business_account_id],
    Boolean(number),
  );
  const detail = useApiQuery(() => templateService.get(templateId), [templateId], Boolean(templateId));
  const structure = detail.data?.id === templateId ? detail.data.structure : undefined;
  const specs = structure?.supported ? structure.parameters : [];

  function input(): CampaignInput {
    return {
      name: name.trim(),
      phone_number_id: numberId,
      template_id: templateId,
      audience_definition: audience,
      variable_mapping: mapping,
      max_messages_per_minute: rate,
      timezone,
      scheduled_at: when === "later" && scheduled ? scheduled : null,
    };
  }

  async function run<T>(action: () => Promise<T>): Promise<T | null> {
    setBusy(true);
    setError(null);
    try {
      return await action();
    } catch (cause) {
      setError(toApiError(cause));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function save(): Promise<Campaign | null> {
    const result = await run(() =>
      saved ? campaignService.update(saved.id, input()) : campaignService.create(input()),
    );
    if (result) {
      setSaved(result);
      onSaved(result);
    }
    return result;
  }

  async function review() {
    const current = await save();
    if (!current) return;
    const report = await run(() => campaignService.preview(current.id));
    if (report) {
      setPreview(report);
      setConfirm(false);
    }
  }

  async function launch() {
    if (!saved) return;
    const result = await run(() => campaignService.launch(saved.id));
    if (result) onLaunched(result.campaign);
  }

  const templateText = structure?.supported ? structure.body_text : "";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">{saved ? `Edit draft · ${saved.name}` : "New campaign"}</h2>
        <Button variant="ghost" onClick={onCancel}>
          Back
        </Button>
      </div>
      {error ? <ApiErrorNotice error={error} /> : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Details</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          <label className="flex flex-col gap-1 text-xs">
            Name
            <Input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            WhatsApp number
            <NativeSelect
              value={numberId}
              onChange={(e) => {
                setNumberId(e.target.value);
                setTemplateId("");
                setMapping({});
              }}
            >
              <option value="">Choose a number…</option>
              {numbers.data?.results.map((n) => (
                <option key={n.id} value={n.id} disabled={n.status !== "connected"}>
                  {n.display_phone_number} {n.status !== "connected" ? `(${n.status})` : ""}
                </option>
              ))}
            </NativeSelect>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            Approved template
            <NativeSelect
              value={templateId}
              disabled={!number}
              onChange={(e) => {
                setTemplateId(e.target.value);
                setMapping({});
              }}
            >
              <option value="">{number ? "Choose a template…" : "Choose a number first"}</option>
              {templates.data?.results.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} · {t.language} · {t.category}
                </option>
              ))}
            </NativeSelect>
          </label>
          {numbers.data && numbers.data.results.length === 0 ? (
            <p className="text-muted-foreground text-xs md:col-span-3">
              This business has no WhatsApp numbers. Connect a WABA first.
            </p>
          ) : null}
          {number && templates.data && templates.data.results.length === 0 ? (
            <p className="text-muted-foreground text-xs md:col-span-3">
              No approved templates are synced for this number. Sync templates on the Templates page.
            </p>
          ) : null}
        </CardContent>
      </Card>

      {structure ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Template variables</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {!structure.supported ? (
              <p className="text-destructive text-sm">{structure.reason}</p>
            ) : specs.length === 0 ? (
              <p className="text-muted-foreground text-sm">This template has no variables.</p>
            ) : (
              specs.map((spec) => (
                <VariableRow
                  key={`${spec.component}-${spec.key}`}
                  spec={spec}
                  entry={entryFor(mapping, spec)}
                  onChange={(entry) => setMapping((m) => withEntry(m, spec, entry))}
                  onError={setError}
                />
              ))
            )}
            {templateText ? (
              <p className="bg-muted rounded-md p-2 text-xs whitespace-pre-wrap">
                {renderPreview(templateText, {})}
              </p>
            ) : null}
            <p className="text-muted-foreground text-[11px]">
              A recipient whose mapped value is empty and has no fallback is skipped — templates are never
              sent with empty variables.
            </p>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Audience</CardTitle>
        </CardHeader>
        <CardContent>
          <AudienceBuilder
            value={audience}
            onChange={setAudience}
            canSave={hasPermission("campaigns.manage")}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Schedule and rate</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          <label className="flex flex-col gap-1 text-xs">
            When
            <NativeSelect value={when} onChange={(e) => setWhen(e.target.value as "now" | "later")}>
              <option value="now">Immediately after launch</option>
              <option value="later">At a scheduled time</option>
            </NativeSelect>
          </label>
          {when === "later" ? (
            <label className="flex flex-col gap-1 text-xs">
              Date and time ({timezone})
              <Input type="datetime-local" value={scheduled} onChange={(e) => setScheduled(e.target.value)} />
            </label>
          ) : null}
          <label className="flex flex-col gap-1 text-xs">
            Time zone
            <Input
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              placeholder="Asia/Kolkata"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            Send rate (messages / minute)
            <Input
              type="number"
              min={1}
              max={1200}
              value={rate}
              onChange={(e) => setRate(Number(e.target.value))}
            />
          </label>
        </CardContent>
      </Card>

      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          disabled={busy || !name.trim() || !numberId || !templateId}
          onClick={() => void save()}
        >
          Save draft
        </Button>
        {hasPermission("campaigns.launch") ? (
          <Button disabled={busy || !name.trim() || !numberId || !templateId} onClick={() => void review()}>
            Review launch
          </Button>
        ) : null}
      </div>

      {preview ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Launch summary</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 text-sm">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
              {Object.entries(preview.summary)
                .filter(([, v]) => v !== null && typeof v !== "object")
                .map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-muted-foreground">{k.replaceAll("_", " ")}</dt>
                    <dd>{String(v)}</dd>
                  </div>
                ))}
            </dl>
            <ul className="flex flex-col gap-1">
              {preview.checks.map((c) => (
                <li key={c.code} className="flex items-start gap-2 text-xs">
                  <Badge variant={c.ok ? "outline" : c.severity === "error" ? "destructive" : "secondary"}>
                    {c.ok ? "ok" : c.severity}
                  </Badge>
                  <span>{c.message}</span>
                </li>
              ))}
            </ul>
            {preview.launchable ? (
              <div className="flex flex-col gap-2">
                <label className="flex items-center gap-2 text-xs">
                  <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />I
                  confirm sending this template to {String(preview.summary.recipients ?? 0)} recipient(s)
                  {when === "later" ? " at the scheduled time" : " now"}.
                </label>
                <Button className="self-start" disabled={!confirm || busy} onClick={() => void launch()}>
                  {when === "later" ? "Schedule campaign" : "Launch campaign"}
                </Button>
              </div>
            ) : (
              <p className="text-destructive text-xs">Fix the failed checks before launching.</p>
            )}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

function VariableRow({
  spec,
  entry,
  onChange,
  onError,
}: {
  spec: TemplateParameterSpec;
  entry: VariableMappingEntry | undefined;
  onChange: (entry: VariableMappingEntry) => void;
  onError: (error: ApiError) => void;
}) {
  const label =
    spec.component === "header"
      ? `Header ${spec.kind === "text" ? "text" : spec.kind}`
      : spec.component === "body"
        ? `Body {{${spec.key}}}`
        : `Button ${Number(spec.button_index) + 1} URL suffix`;
  if (spec.kind !== "text") {
    return (
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="w-40 font-medium">{label}</span>
        <input
          type="file"
          aria-label={label}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            mediaService
              .upload(file)
              .then((asset) => onChange({ source: "static", media_asset_id: asset.id }))
              .catch((cause) => onError(toApiError(cause)));
          }}
        />
        {entry?.media_asset_id ? <Badge variant="outline">file uploaded</Badge> : null}
      </div>
    );
  }
  const current = entry ?? { source: "" };
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="w-40 font-medium">{label}</span>
      <NativeSelect
        aria-label={`${label} source`}
        value={current.source}
        onChange={(e) => onChange({ source: e.target.value, fallback: current.fallback })}
      >
        <option value="">Map to…</option>
        {SOURCES.map((s) => (
          <option key={s.value} value={s.value}>
            {s.label}
          </option>
        ))}
      </NativeSelect>
      {current.source === "contact.attribute" ? (
        <Input
          aria-label={`${label} attribute`}
          className="h-8 w-32"
          placeholder="attribute"
          value={current.key ?? ""}
          onChange={(e) => onChange({ ...current, key: e.target.value })}
        />
      ) : null}
      {current.source === "static" ? (
        <Input
          aria-label={`${label} value`}
          className="h-8 w-48"
          placeholder={spec.example || "value"}
          value={current.value ?? ""}
          onChange={(e) => onChange({ ...current, value: e.target.value })}
        />
      ) : current.source ? (
        <Input
          aria-label={`${label} fallback`}
          className="h-8 w-40"
          placeholder="fallback (optional)"
          value={current.fallback ?? ""}
          onChange={(e) => onChange({ ...current, fallback: e.target.value })}
        />
      ) : null}
    </div>
  );
}
