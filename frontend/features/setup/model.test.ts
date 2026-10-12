import { describe, expect, it } from "vitest";

import type { SetupStep } from "@/types/api";

import { currentStep, describeTestStatus, evidenceRows, fixWhere, progressOf } from "./model";

const step = (id: string, state: SetupStep["state"], over: Partial<SetupStep> = {}): SetupStep => ({
  id,
  title: id,
  state,
  detail: "",
  next_step: "",
  fix_in: "none",
  action: "",
  evidence: {},
  ...over,
});

describe("describeTestStatus: never overstates", () => {
  it("only delivered and read are success; accepted and sent say not delivered yet", () => {
    expect(describeTestStatus("accepted").label).toMatch(/not delivered yet/);
    expect(describeTestStatus("sent").label).toMatch(/not delivered yet/);
    for (const s of ["queued", "sending", "accepted", "sent", "failed"]) {
      expect(describeTestStatus(s).tone).not.toBe("success");
    }
    expect(describeTestStatus("delivered").tone).toBe("success");
    expect(describeTestStatus("read").tone).toBe("success");
  });
  it("only delivered, read and failed are final", () => {
    for (const s of ["queued", "sending", "accepted", "sent"]) expect(describeTestStatus(s).final).toBe(false);
    for (const s of ["delivered", "read", "failed"]) expect(describeTestStatus(s).final).toBe(true);
  });
  it("shows the reason a message failed", () => {
    expect(describeTestStatus("failed", "Not on WhatsApp").text).toContain("Not on WhatsApp");
  });
});

describe("progress and current step", () => {
  const steps = [step("a", "verified"), step("b", "failed"), step("c", "pending")];
  it("counts only verified steps as done", () => {
    expect(progressOf(steps)).toEqual({ done: 1, total: 3 });
  });
  it("continues at the server's next step, else the first unfinished one", () => {
    expect(currentStep(steps, "c")?.id).toBe("c");
    expect(currentStep(steps, "")?.id).toBe("b");
    expect(currentStep([step("a", "verified")], "")).toBeUndefined();
  });
  it("names where a fix lives", () => {
    expect(fixWhere(step("x", "failed", { fix_in: "meta" }))).toMatch(/Meta's WhatsApp Manager/);
    expect(fixWhere(step("x", "failed", { fix_in: "administrator" }))).toMatch(/administrator/);
    expect(fixWhere(step("x", "failed", { fix_in: "console" }))).toBe("");
  });
});

describe("evidenceRows", () => {
  it("skips empty values instead of inventing them and formats booleans and times", () => {
    const rows = evidenceRows({
      accounts: 2,
      last_received_at: null,
      verify_token_configured: true,
      last_verified_at: "2026-10-12T09:00:00Z",
      statuses: ["CONNECTED"],
    });
    expect(rows.map((r) => r.label)).toEqual([
      "Accounts",
      "Verify token set",
      "Last checked with Meta",
      "Meta status",
    ]);
    expect(rows.find((r) => r.label === "Verify token set")?.value).toBe("Yes");
    expect(rows.find((r) => r.label === "Meta status")?.value).toBe("CONNECTED");
  });
});
