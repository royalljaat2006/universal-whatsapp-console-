"use client";

import { ExternalLinkIcon } from "lucide-react";

import { StatusBadge } from "@/components/ui/status-badge";
import type { TemplateGuidance } from "@/types/api";

import { STATE_LABEL, STATE_TONE } from "./model";

/**
 * What a template's status means, in plain words, and what to do next. Shows
 * Meta's own reason code verbatim, and says whether Meta has CONFIRMED the
 * status after we submitted (a successful request is never treated as approval).
 */
export function GuidancePanel({
  guidance,
  action,
}: {
  guidance: TemplateGuidance;
  action?: React.ReactNode;
}) {
  const g = guidance;
  return (
    <section aria-label="What this status means" className="flex flex-col gap-2 rounded-md border px-3 py-3">
      <p className="flex flex-wrap items-center gap-2">
        <StatusBadge tone={STATE_TONE[g.state]}>{STATE_LABEL[g.state]}</StatusBadge>
        {g.meta_status ? <span className="text-caption">Meta says: {g.meta_status}</span> : null}
        {g.state !== "draft" ? (
          <span className="text-caption">
            {g.verified_by_meta ? "Confirmed by Meta" : "Not yet confirmed by Meta"}
          </span>
        ) : null}
      </p>
      <p className="text-sm">{g.explanation}</p>
      {g.next_step ? (
        <p className="text-sm">
          <span className="font-medium">What to do: </span>
          {g.next_step}
        </p>
      ) : null}
      {g.reason ? (
        <div className="bg-danger-soft/60 rounded-md px-3 py-2 text-sm">
          <p>
            <span className="font-medium">Meta&apos;s reason: </span>
            <span className="font-mono text-xs">{g.reason.code || "none given"}</span>
          </p>
          <p className="mt-1">{g.reason.meaning}</p>
          <p className="mt-1">
            <span className="font-medium">How to fix it: </span>
            {g.reason.how_to_fix}
          </p>
        </div>
      ) : null}
      {g.review_note ? <p className="text-caption">{g.review_note}</p> : null}
      {g.links.length ? (
        <ul className="flex flex-wrap gap-x-4 gap-y-1">
          {g.links.map((link) => (
            <li key={link.url}>
              <a
                href={link.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary inline-flex items-center gap-1 text-sm font-medium hover:underline"
              >
                {link.label} <ExternalLinkIcon aria-hidden className="size-3.5" />
                {" "}<span className="sr-only">(opens in a new tab)</span>
              </a>
            </li>
          ))}
        </ul>
      ) : null}
      {action}
    </section>
  );
}
