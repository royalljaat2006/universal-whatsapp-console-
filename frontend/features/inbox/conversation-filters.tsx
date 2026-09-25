"use client";

import { useEffect, useState } from "react";

import { NativeSelect } from "@/components/ui/native-select";
import { SearchInput } from "@/components/ui/page";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { useGlobalSearch } from "@/lib/global-search";
import type { ConversationFilters as Filters } from "@/services/inbox";
import type { AssigneeOptions, Tag } from "@/types/api";

/**
 * Search + filters for the conversation list. Values are sent to the backend
 * as query parameters; the server applies them inside the caller's business
 * scope (conversations/api.py), so nothing is filtered client-side.
 */
export function ConversationFilters({
  value,
  onChange,
  tags,
  assignees,
}: {
  value: Filters;
  onChange: (next: Filters) => void;
  tags: Tag[];
  assignees: AssigneeOptions | undefined;
}) {
  const [search, setSearch] = useState(value.q ?? "");
  const debounced = useDebouncedValue(search.trim(), 350);
  useGlobalSearch(setSearch);

  useEffect(() => {
    if ((value.q ?? "") !== debounced) onChange({ ...value, q: debounced || undefined });
    // Only the debounced text should trigger this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  const set = (patch: Partial<Filters>) => onChange({ ...value, ...patch });

  return (
    <div className="flex flex-col gap-2 border-b px-3 py-2.5">
      <SearchInput
        aria-label="Search conversations"
        placeholder="Search name, phone, message…"
        className="w-full"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <div className="grid grid-cols-2 gap-1.5 [&_select]:h-7 [&_select]:text-xs">
        <NativeSelect
          aria-label="Status"
          value={value.status ?? ""}
          onChange={(e) => set({ status: (e.target.value || undefined) as Filters["status"] })}
        >
          <option value="">Any status</option>
          <option value="open">Open</option>
          <option value="pending">Pending</option>
          <option value="resolved">Resolved</option>
          <option value="archived">Archived</option>
        </NativeSelect>
        <NativeSelect
          aria-label="Assigned"
          value={value.assigned ?? ""}
          onChange={(e) => set({ assigned: e.target.value || undefined })}
        >
          <option value="">Anyone</option>
          <option value="me">Assigned to me</option>
          <option value="none">Unassigned</option>
          {assignees?.members.map((m) => (
            <option key={m.membership_id} value={m.membership_id}>
              {m.name}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label="Tag"
          value={value.tag ?? ""}
          onChange={(e) => set({ tag: e.target.value || undefined })}
        >
          <option value="">Any tag</option>
          {tags.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </NativeSelect>
        <label className="text-muted-foreground flex items-center gap-2 px-1 text-xs">
          <input
            className="accent-primary"
            type="checkbox"
            checked={value.unread === "1"}
            onChange={(e) => set({ unread: e.target.checked ? "1" : undefined })}
          />
          Unread only
        </label>
      </div>
    </div>
  );
}
