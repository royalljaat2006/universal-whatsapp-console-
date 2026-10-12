import { describe, expect, it } from "vitest";

import type { ConnectionHealth, ConnectionHealthCheck } from "@/types/api";

import { checkedAtSnapshot, countIssues, fixWhere, refreshTookEffect, sortChecks, topIssues } from "./model";

const check = (over: Partial<ConnectionHealthCheck>): ConnectionHealthCheck => ({
  code: "c",
  state: "ok",
  title: "T",
  detail: "D",
  next_step: "",
  fix_in: "none",
  basis: "provider",
  action: "",
  ...over,
});

function health(over: Partial<ConnectionHealth> = {}): ConnectionHealth {
  return {
    generated_at: "2026-10-12T10:00:00Z",
    overall: "attention",
    summary: "s",
    accounts: [],
    numbers: [],
    recent_alerts: [],
    notes: [],
    ...over,
  };
}

const row = (id: string, at: string | null, checks: ConnectionHealthCheck[] = []) => ({
  id,
  label: id,
  display_phone_number: id,
  verified_name: "",
  name: id,
  business_id: "b",
  business_name: "B",
  business_account_id: "a",
  overall: "healthy" as const,
  last_checked_at: at,
  checks,
});

describe("sortChecks", () => {
  it("puts problems first, then warnings, then unknown, then ok, keeping order within a group", () => {
    const sorted = sortChecks([
      check({ code: "ok1", state: "ok" }),
      check({ code: "w1", state: "warning" }),
      check({ code: "u1", state: "unknown" }),
      check({ code: "p1", state: "problem" }),
      check({ code: "w2", state: "warning" }),
    ]);
    expect(sorted.map((c) => c.code)).toEqual(["p1", "w1", "w2", "u1", "ok1"]);
  });
});

describe("fixWhere", () => {
  it("names the place to fix it", () => {
    expect(fixWhere(check({ state: "problem", fix_in: "meta" }))).toBe("Fix in Meta's WhatsApp Manager");
    expect(fixWhere(check({ state: "warning", fix_in: "console" }))).toBe("Fix here");
    expect(fixWhere(check({ state: "ok", fix_in: "meta" }))).toBeNull();
    expect(fixWhere(check({ state: "problem", fix_in: "none" }))).toBeNull();
  });
});

describe("issue counting", () => {
  const h = health({
    numbers: [
      row("n1", null, [check({ state: "problem" }), check({ state: "ok" })]),
      row("n2", null, [check({ state: "warning", title: "W" }), check({ state: "unknown" })]),
    ],
    accounts: [row("a1", null, [check({ state: "warning" })])],
  });

  it("counts only problems and warnings (unknown is neither an issue nor fine)", () => {
    expect(countIssues(h)).toEqual({ problems: 1, warnings: 2 });
  });

  it("lists the most important first and respects the limit", () => {
    const top = topIssues(h, 2);
    expect(top).toHaveLength(2);
    expect(top[0].check.state).toBe("problem");
  });
});

describe("refreshTookEffect: only true when Meta was really re-read", () => {
  const before = checkedAtSnapshot(
    health({ accounts: [row("a", "2026-10-12T09:00:00Z")], numbers: [row("n", "2026-10-12T09:00:00Z")] }),
  );

  it("is true when every account and number advanced", () => {
    const after = health({
      accounts: [row("a", "2026-10-12T10:00:00Z")],
      numbers: [row("n", "2026-10-12T10:00:01Z")],
    });
    expect(refreshTookEffect(before, after)).toBe(true);
  });

  it("is false when any row did not advance", () => {
    const after = health({
      accounts: [row("a", "2026-10-12T10:00:00Z")],
      numbers: [row("n", "2026-10-12T09:00:00Z")],
    });
    expect(refreshTookEffect(before, after)).toBe(false);
  });

  it("is false when nothing changed or a row has never been checked", () => {
    const same = health({
      accounts: [row("a", "2026-10-12T09:00:00Z")],
      numbers: [row("n", "2026-10-12T09:00:00Z")],
    });
    expect(refreshTookEffect(before, same)).toBe(false);
    expect(refreshTookEffect(before, health({ accounts: [row("a", null)], numbers: [] }))).toBe(false);
  });

  it("is true for a row that had never been checked and now has been", () => {
    const empty = checkedAtSnapshot(health({ accounts: [row("a", null)] }));
    expect(refreshTookEffect(empty, health({ accounts: [row("a", "2026-10-12T10:00:00Z")] }))).toBe(true);
  });

  it("is false when there is nothing to refresh", () => {
    expect(refreshTookEffect(new Map(), health())).toBe(false);
  });
});
