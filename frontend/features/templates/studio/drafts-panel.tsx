"use client";

import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/page";
import { StatusBadge } from "@/components/ui/status-badge";
import type { TemplateDraft } from "@/types/api";

import { DRAFT_STATUS_LABEL, STATE_LABEL, STATE_TONE } from "./model";

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "";
}

/**
 * The templates this business is writing or has sent from the studio. A draft
 * is local; once sent, the badge shows what Meta reported (or that nothing has
 * been reported yet), never a guess.
 */
export function DraftsPanel({
  drafts,
  canManage,
  onOpen,
  onDelete,
}: {
  drafts: TemplateDraft[];
  canManage: boolean;
  onOpen: (draft: TemplateDraft) => void;
  onDelete: (draft: TemplateDraft) => void;
}) {
  if (drafts.length === 0) return null;
  return (
    <Panel title="Templates you are writing" description="Drafts are not sent to Meta until you choose to send them.">
      <ul className="divide-y">
        {drafts.map((d) => (
          <li key={d.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
            <div className="min-w-0">
              <p className="text-sm font-medium">
                {d.name} <span className="text-caption">({d.language})</span>
              </p>
              <p className="flex flex-wrap items-center gap-2">
                <StatusBadge tone={STATE_TONE[d.guidance.state]}>{STATE_LABEL[d.guidance.state]}</StatusBadge>
                <span className="text-caption">{DRAFT_STATUS_LABEL[d.status]}</span>
                {d.status === "draft" && !d.checks.ok ? (
                  <span className="text-caption">
                    {d.checks.errors.length} thing{d.checks.errors.length === 1 ? "" : "s"} to fix
                  </span>
                ) : null}
                <span className="text-caption">Updated {when(d.updated_at)}</span>
              </p>
              {d.last_error ? <p className="text-supporting">{d.last_error}</p> : null}
            </div>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={() => onOpen(d)}>
                {d.status === "draft" && canManage ? "Continue" : "View"}
                {" "}<span className="sr-only">{d.name}</span>
              </Button>
              {d.status === "draft" && canManage ? (
                <Button size="sm" variant="ghost" onClick={() => onDelete(d)}>
                  Delete{" "}<span className="sr-only">{d.name}</span>
                </Button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
