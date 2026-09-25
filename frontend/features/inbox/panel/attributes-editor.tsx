"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Row = { key: string; value: string };

const toRows = (attributes: Record<string, string>): Row[] =>
  Object.entries(attributes).map(([key, value]) => ({ key, value: String(value) }));

/** Edit free-form contact attributes (stored as JSON on the contact). */
export function AttributesEditor({
  attributes,
  canEdit,
  onSave,
}: {
  attributes: Record<string, string>;
  canEdit: boolean;
  onSave: (next: Record<string, string>) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [saving, setSaving] = useState(false);
  const entries = toRows(attributes);

  if (!editing) {
    return (
      <div className="flex flex-col gap-1">
        <span className="flex items-center justify-between">
          <span className="text-muted-foreground text-xs">Attributes</span>
          {canEdit ? (
            <Button
              variant="ghost"
              size="xs"
              onClick={() => {
                setRows(entries.length ? entries : [{ key: "", value: "" }]);
                setEditing(true);
              }}
            >
              Edit
            </Button>
          ) : null}
        </span>
        {entries.length === 0 ? <span className="text-muted-foreground text-xs">None</span> : null}
        <dl className="grid grid-cols-[auto_1fr] gap-x-2 text-xs">
          {entries.map((r) => (
            <div key={r.key} className="contents">
              <dt className="text-muted-foreground">{r.key}</dt>
              <dd className="break-words">{r.value}</dd>
            </div>
          ))}
        </dl>
      </div>
    );
  }

  const set = (i: number, patch: Partial<Row>) =>
    setRows((current) => current.map((row, j) => (j === i ? { ...row, ...patch } : row)));

  async function save() {
    const next: Record<string, string> = {};
    for (const row of rows) if (row.key.trim()) next[row.key.trim()] = row.value;
    setSaving(true);
    const ok = await onSave(next);
    setSaving(false);
    if (ok) setEditing(false);
  }

  return (
    <div className="flex flex-col gap-1">
      <span className="text-muted-foreground text-xs">Attributes</span>
      {rows.map((row, i) => (
        <div key={i} className="flex gap-1">
          <Input
            aria-label="Attribute name"
            className="h-7 text-xs"
            placeholder="name"
            title="Letter first, then letters, digits or _ (max 40)"
            pattern="[A-Za-z][A-Za-z0-9_]{0,39}"
            maxLength={40}
            value={row.key}
            onChange={(e) => set(i, { key: e.target.value })}
          />
          <Input
            aria-label="Attribute value"
            className="h-7 text-xs"
            placeholder="value"
            maxLength={500}
            value={row.value}
            onChange={(e) => set(i, { value: e.target.value })}
          />
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="Remove attribute"
            onClick={() => setRows((current) => current.filter((_, j) => j !== i))}
          >
            ×
          </Button>
        </div>
      ))}
      <div className="flex gap-1">
        <Button variant="outline" size="xs" onClick={() => setRows((r) => [...r, { key: "", value: "" }])}>
          + Row
        </Button>
        <Button size="xs" onClick={() => void save()} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
        <Button variant="ghost" size="xs" onClick={() => setEditing(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
