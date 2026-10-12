"use client";

import { ArrowRightIcon } from "lucide-react";
import Link from "next/link";

import { StatusBadge } from "@/components/ui/status-badge";
import type { ApiError } from "@/lib/api/errors";
import type { ConnectionHealth } from "@/types/api";

import { OVERALL_LABEL, OVERALL_TONE, fixWhere, topIssues } from "./model";

/**
 * Compact health card for Home. Purely presentational: it renders exactly
 * what the health endpoint returned (or an honest loading / error / nothing-
 * connected state) and never fills a gap with a reassuring default.
 */
export function HealthSummaryCard({
  health,
  loading,
  error,
}: {
  health: ConnectionHealth | undefined;
  loading: boolean;
  error: ApiError | undefined;
}) {
  if (loading) {
    return (
      <section aria-label="WhatsApp health" className="bg-surface rounded-lg border p-4 shadow-xs">
        <p className="text-supporting">Checking your WhatsApp connection…</p>
      </section>
    );
  }
  if (error) {
    return (
      <section aria-label="WhatsApp health" className="bg-surface rounded-lg border p-4 shadow-xs">
        <p className="text-sm font-medium">Couldn&apos;t check WhatsApp health</p>
        <p className="text-supporting">{error.message}</p>
      </section>
    );
  }
  if (!health) return null;
  if (health.numbers.length === 0 && health.accounts.length === 0) return null; // the setup checklist covers this

  const issues = topIssues(health, 3);
  return (
    <section
      aria-label="WhatsApp health"
      className="bg-surface flex flex-col gap-3 rounded-lg border p-4 shadow-xs"
    >
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-section-title">WhatsApp health</h2>
          <p className="text-supporting">{health.summary}</p>
        </div>
        <StatusBadge tone={OVERALL_TONE[health.overall]}>{OVERALL_LABEL[health.overall]}</StatusBadge>
      </header>
      {issues.length ? (
        <ul className="divide-y rounded-md border">
          {issues.map(({ subject, check }, index) => {
            const where = fixWhere(check);
            return (
              <li key={`${subject}-${check.code}-${index}`} className="px-3 py-2.5">
                <p className="text-sm font-medium">
                  {subject}: {check.title}
                </p>
                <p className="text-supporting">{check.next_step || check.detail}</p>
                {where ? <p className="text-caption mt-0.5">{where}</p> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      <Link
        href="/numbers"
        className="text-primary inline-flex w-fit items-center gap-1 text-sm font-medium hover:underline"
      >
        See details <ArrowRightIcon aria-hidden className="size-3.5" />
      </Link>
    </section>
  );
}
