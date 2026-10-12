import type {
  ConnectionHealth,
  ConnectionHealthCheck,
  ConnectionHealthOverall,
  ConnectionHealthState,
} from "@/types/api";

import type { Tone } from "@/components/ui/status-badge";

export const OVERALL_LABEL: Record<ConnectionHealthOverall, string> = {
  healthy: "Healthy",
  attention: "Needs attention",
  problem: "Problem",
  unknown: "Not enough information",
};

export const OVERALL_TONE: Record<ConnectionHealthOverall, Tone> = {
  healthy: "success",
  attention: "warning",
  problem: "danger",
  unknown: "neutral",
};

export const STATE_LABEL: Record<ConnectionHealthState, string> = {
  ok: "OK",
  warning: "Needs attention",
  problem: "Problem",
  unknown: "Unknown",
};

/** Problems first, then warnings, then unknowns, then fine. Stable within a group. */
const ORDER: Record<ConnectionHealthState, number> = { problem: 0, warning: 1, unknown: 2, ok: 3 };

export function sortChecks(checks: ConnectionHealthCheck[]): ConnectionHealthCheck[] {
  return checks
    .map((check, index) => ({ check, index }))
    .sort((a, b) => ORDER[a.check.state] - ORDER[b.check.state] || a.index - b.index)
    .map(({ check }) => check);
}

export function needsAction(check: ConnectionHealthCheck): boolean {
  return check.state === "problem" || check.state === "warning";
}

/** Where the fix happens, in words a non-technical person can act on. */
export function fixWhere(check: ConnectionHealthCheck): string | null {
  if (!needsAction(check) && check.state !== "unknown") return null;
  if (check.fix_in === "meta") return "Fix in Meta's WhatsApp Manager";
  if (check.fix_in === "console") return "Fix here";
  return null;
}

export interface IssueCounts {
  problems: number;
  warnings: number;
}

export function countIssues(health: ConnectionHealth): IssueCounts {
  const checks = [...health.accounts, ...health.numbers].flatMap((row) => row.checks);
  return {
    problems: checks.filter((c) => c.state === "problem").length,
    warnings: checks.filter((c) => c.state === "warning").length,
  };
}

/** The most important things to act on, for compact displays. */
export function topIssues(
  health: ConnectionHealth,
  limit = 3,
): { subject: string; check: ConnectionHealthCheck }[] {
  const rows = [
    ...health.numbers.map((n) => ({
      subject: n.label || n.verified_name || n.display_phone_number,
      checks: n.checks,
    })),
    ...health.accounts.map((a) => ({ subject: a.name || "WhatsApp account", checks: a.checks })),
  ];
  return rows
    .flatMap((row) => row.checks.filter(needsAction).map((check) => ({ subject: row.subject, check })))
    .sort((a, b) => ORDER[a.check.state] - ORDER[b.check.state])
    .slice(0, limit);
}

/** id -> last_checked_at for every account and number, to detect a real refresh. */
export function checkedAtSnapshot(health: ConnectionHealth): Map<string, string | null> {
  return new Map([...health.accounts, ...health.numbers].map((row) => [row.id, row.last_checked_at]));
}

/**
 * True only when EVERY account and number now reports a later "last checked"
 * time than before the refresh was requested. This is what lets the UI say
 * "updated" only when it actually was.
 */
export function refreshTookEffect(before: Map<string, string | null>, after: ConnectionHealth): boolean {
  const rows = [...after.accounts, ...after.numbers];
  if (rows.length === 0) return false;
  return rows.every((row) => {
    const previous = before.get(row.id) ?? null;
    if (row.last_checked_at === null) return false;
    return previous === null || new Date(row.last_checked_at).getTime() > new Date(previous).getTime();
  });
}
