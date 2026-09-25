"use client";

import { useEffect, useState } from "react";

import { ApiErrorNotice } from "@/components/status/api-error-notice";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { useApiQuery } from "@/hooks/use-api-query";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import type { ApiError } from "@/lib/api/errors";
import { toApiError } from "@/lib/format";
import { audienceService, campaignService } from "@/services/campaigns";
import { tagService } from "@/services/contacts";
import { templateService } from "@/services/templates";
import type { AudienceCondition, AudienceDefinition, AudiencePreview } from "@/types/api";

export const FIELD_OPTIONS: { field: string; label: string; ops: { op: string; label: string }[] }[] = [
  {
    field: "tag",
    label: "Tag",
    ops: [
      { op: "has", label: "has" },
      { op: "not_has", label: "does not have" },
    ],
  },
  {
    field: "attribute",
    label: "Attribute",
    ops: [
      { op: "eq", label: "equals" },
      { op: "neq", label: "does not equal" },
      { op: "contains", label: "contains" },
      { op: "exists", label: "is set" },
      { op: "not_exists", label: "is not set" },
    ],
  },
  {
    field: "phone",
    label: "Phone number",
    ops: [
      { op: "starts_with", label: "starts with" },
      { op: "contains", label: "contains" },
    ],
  },
  { field: "name", label: "Name", ops: [{ op: "contains", label: "contains" }] },
  {
    field: "last_activity",
    label: "Last activity",
    ops: [
      { op: "within_days", label: "within the last (days)" },
      { op: "older_than_days", label: "more than (days) ago" },
      { op: "never", label: "never" },
    ],
  },
  {
    field: "last_inbound",
    label: "Last customer message",
    ops: [
      { op: "within_days", label: "within the last (days)" },
      { op: "older_than_days", label: "more than (days) ago" },
      { op: "never", label: "never" },
    ],
  },
  { field: "conversation_status", label: "Conversation status", ops: [{ op: "eq", label: "is" }] },
  {
    field: "opt_in",
    label: "Opt-in",
    ops: [
      { op: "eq", label: "is" },
      { op: "neq", label: "is not" },
    ],
  },
  {
    field: "template_received",
    label: "Template received",
    ops: [
      { op: "has", label: "has received" },
      { op: "not_has", label: "has not received" },
    ],
  },
  {
    field: "created",
    label: "Contact created",
    ops: [
      { op: "after", label: "on/after" },
      { op: "before", label: "before" },
    ],
  },
];

function defaultCondition(field: string): AudienceCondition {
  const spec = FIELD_OPTIONS.find((f) => f.field === field) ?? FIELD_OPTIONS[0];
  return { field: spec.field, op: spec.ops[0].op, value: "" };
}

/**
 * Builds an audience definition; the count shown is the backend's exact
 * count for the selected business (contacts are never loaded in the browser).
 */
export function AudienceBuilder({
  value,
  onChange,
  canSave,
}: {
  value: AudienceDefinition;
  onChange: (next: AudienceDefinition) => void;
  canSave: boolean;
}) {
  const tags = useApiQuery(() => tagService.list(), []);
  const templates = useApiQuery(() => templateService.list({}), []);
  const saved = useApiQuery(() => audienceService.list(), []);
  const [preview, setPreview] = useState<AudiencePreview | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [saveName, setSaveName] = useState("");
  const debounced = useDebouncedValue(JSON.stringify(value), 400);

  useEffect(() => {
    let cancelled = false;
    campaignService
      .audiencePreview(JSON.parse(debounced) as AudienceDefinition)
      .then((p) => {
        if (!cancelled) {
          setPreview(p);
          setError(null);
        }
      })
      .catch((cause) => !cancelled && setError(toApiError(cause)));
    return () => {
      cancelled = true;
    };
  }, [debounced]);

  const update = (index: number, patch: Partial<AudienceCondition>) =>
    onChange({
      ...value,
      conditions: value.conditions.map((c, i) => (i === index ? { ...c, ...patch } : c)),
    });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span>Match</span>
        <NativeSelect
          aria-label="Match"
          value={value.match}
          onChange={(e) => onChange({ ...value, match: e.target.value as "all" | "any" })}
        >
          <option value="all">all conditions</option>
          <option value="any">any condition</option>
        </NativeSelect>
        {saved.data && saved.data.length ? (
          <NativeSelect
            aria-label="Load saved audience"
            value=""
            onChange={(e) => {
              const found = saved.data?.find((a) => a.id === e.target.value);
              if (found) onChange(found.definition);
            }}
          >
            <option value="">Load saved audience…</option>
            {saved.data.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </NativeSelect>
        ) : null}
      </div>

      {value.conditions.length === 0 ? (
        <p className="text-muted-foreground text-xs">
          No conditions: every reachable contact of this business.
        </p>
      ) : null}
      {value.conditions.map((condition, index) => {
        const spec = FIELD_OPTIONS.find((f) => f.field === condition.field) ?? FIELD_OPTIONS[0];
        const needsValue = !["exists", "not_exists", "never"].includes(condition.op);
        return (
          <div key={index} className="flex flex-wrap items-center gap-2">
            <NativeSelect
              aria-label="Field"
              value={condition.field}
              onChange={(e) =>
                onChange({
                  ...value,
                  conditions: value.conditions.map((c, i) =>
                    i === index ? defaultCondition(e.target.value) : c,
                  ),
                })
              }
            >
              {FIELD_OPTIONS.map((f) => (
                <option key={f.field} value={f.field}>
                  {f.label}
                </option>
              ))}
            </NativeSelect>
            {condition.field === "attribute" ? (
              <Input
                aria-label="Attribute name"
                className="h-8 w-32"
                placeholder="attribute"
                value={condition.key ?? ""}
                onChange={(e) => update(index, { key: e.target.value })}
              />
            ) : null}
            <NativeSelect
              aria-label="Operator"
              value={condition.op}
              onChange={(e) => update(index, { op: e.target.value })}
            >
              {spec.ops.map((o) => (
                <option key={o.op} value={o.op}>
                  {o.label}
                </option>
              ))}
            </NativeSelect>
            {needsValue ? (
              <ValueInput
                condition={condition}
                onChange={(v) => update(index, { value: v })}
                tags={tags.data ?? []}
                templates={templates.data?.results ?? []}
              />
            ) : null}
            <Button
              variant="ghost"
              size="xs"
              aria-label="Remove condition"
              onClick={() =>
                onChange({ ...value, conditions: value.conditions.filter((_, i) => i !== index) })
              }
            >
              ×
            </Button>
          </div>
        );
      })}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onChange({ ...value, conditions: [...value.conditions, defaultCondition("tag")] })}
          disabled={value.conditions.length >= 20}
        >
          + Condition
        </Button>
        {canSave ? (
          <span className="flex items-center gap-1">
            <Input
              aria-label="Save audience as"
              className="h-8 w-44"
              placeholder="Save as audience…"
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
            />
            <Button
              variant="ghost"
              size="sm"
              disabled={!saveName.trim()}
              onClick={() =>
                void audienceService
                  .create(saveName.trim(), value)
                  .then(() => {
                    setSaveName("");
                    saved.reload();
                  })
                  .catch((cause) => setError(toApiError(cause)))
              }
            >
              Save
            </Button>
          </span>
        ) : null}
      </div>

      {error ? <ApiErrorNotice error={error} /> : null}
      {preview ? (
        <div className="bg-muted/50 rounded-md p-3 text-sm" role="status">
          <p>
            <strong className="tabular-nums">{preview.count.toLocaleString()}</strong> recipient(s)
            {preview.count_is_exact ? " (exact count)" : " (estimate)"} of{" "}
            {preview.reachable_contacts_in_business.toLocaleString()} reachable contacts. Always excluded:{" "}
            {preview.excluded_always.join(", ")}.
          </p>
          {preview.sample.length ? (
            <p className="text-muted-foreground mt-1 text-xs">
              Sample: {preview.sample.map((s) => s.name || s.phone_number).join(", ")}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function ValueInput({
  condition,
  onChange,
  tags,
  templates,
}: {
  condition: AudienceCondition;
  onChange: (value: string | number) => void;
  tags: { id: string; name: string }[];
  templates: { id: string; name: string; language: string }[];
}) {
  const value = condition.value ?? "";
  switch (condition.field) {
    case "tag":
      return (
        <NativeSelect aria-label="Tag" value={String(value)} onChange={(e) => onChange(e.target.value)}>
          <option value="">Choose tag…</option>
          {tags.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </NativeSelect>
      );
    case "template_received":
      return (
        <NativeSelect aria-label="Template" value={String(value)} onChange={(e) => onChange(e.target.value)}>
          <option value="">Choose template…</option>
          {templates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name} ({t.language})
            </option>
          ))}
        </NativeSelect>
      );
    case "conversation_status":
      return (
        <NativeSelect aria-label="Status" value={String(value)} onChange={(e) => onChange(e.target.value)}>
          <option value="">Choose…</option>
          {["open", "pending", "resolved", "archived"].map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </NativeSelect>
      );
    case "opt_in":
      return (
        <NativeSelect aria-label="Opt-in" value={String(value)} onChange={(e) => onChange(e.target.value)}>
          <option value="">Choose…</option>
          <option value="opted_in">opted in</option>
          <option value="unknown">unknown</option>
        </NativeSelect>
      );
    case "last_activity":
    case "last_inbound":
      return (
        <Input
          type="number"
          aria-label="Days"
          className="h-8 w-24"
          min={0}
          max={3650}
          value={String(value)}
          onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
        />
      );
    case "created":
      return (
        <Input
          type="date"
          aria-label="Date"
          className="h-8 w-40"
          value={String(value)}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    default:
      return (
        <Input
          aria-label="Value"
          className="h-8 w-44"
          value={String(value)}
          onChange={(e) => onChange(e.target.value)}
        />
      );
  }
}
