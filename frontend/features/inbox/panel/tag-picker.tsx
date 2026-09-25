"use client";

import { Badge } from "@/components/ui/badge";
import { NativeSelect } from "@/components/ui/native-select";
import type { Tag, TagBrief } from "@/types/api";

/** Business-scoped tags attached to a contact or conversation. */
export function TagPicker({
  label,
  current,
  available,
  canEdit,
  onAdd,
  onRemove,
}: {
  label: string;
  current: TagBrief[];
  available: Tag[];
  canEdit: boolean;
  onAdd: (tagId: string) => void;
  onRemove: (tagId: string) => void;
}) {
  const unused = available.filter((t) => !current.some((c) => c.id === t.id));
  return (
    <div className="flex flex-col gap-1">
      <span className="text-muted-foreground text-xs">{label}</span>
      <div className="flex flex-wrap gap-1">
        {current.length === 0 ? <span className="text-muted-foreground text-xs">None</span> : null}
        {current.map((t) => (
          <Badge key={t.id} variant="outline" className="gap-1">
            {t.name}
            {canEdit ? (
              <button
                type="button"
                aria-label={`Remove tag ${t.name}`}
                className="hover:text-destructive"
                onClick={() => onRemove(t.id)}
              >
                ×
              </button>
            ) : null}
          </Badge>
        ))}
      </div>
      {canEdit && unused.length > 0 ? (
        <NativeSelect
          aria-label={`Add ${label.toLowerCase()}`}
          className="h-7 text-xs"
          value=""
          onChange={(e) => e.target.value && onAdd(e.target.value)}
        >
          <option value="">+ Add tag…</option>
          {unused.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </NativeSelect>
      ) : null}
      {canEdit && available.length === 0 ? (
        <span className="text-muted-foreground text-[11px]">No tags yet — create them in Settings.</span>
      ) : null}
    </div>
  );
}
