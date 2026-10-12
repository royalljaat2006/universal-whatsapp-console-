import { describe, expect, it } from "vitest";

import type { FailedMessage, RetryAttempt } from "@/types/api";

import { KIND_LABEL, costLine, describeAttempt, hasPending, isPending, recipientLabel } from "./model";

const attempt = (status: string, over: Partial<RetryAttempt> = {}): RetryAttempt => ({
  id: "r1",
  status,
  status_label: status,
  created_at: "2026-10-12T10:00:00Z",
  accepted_at: null,
  sent_at: null,
  delivered_at: null,
  read_at: null,
  failed_at: null,
  failure: null,
  ...over,
});

const row = (retried_as: RetryAttempt | null, recipient = { name: "", phone_number: "" }) =>
  ({ recipient, recovery: { retried_as } }) as unknown as FailedMessage;

describe("describeAttempt: never overstates what happened", () => {
  it("accepted and sent are explicitly NOT delivered", () => {
    expect(describeAttempt(attempt("accepted")).label).toMatch(/not delivered yet/i);
    expect(describeAttempt(attempt("sent")).label).toMatch(/not delivered yet/i);
    for (const status of ["queued", "sending", "accepted", "sent"]) {
      expect(describeAttempt(attempt(status)).tone).toBe("info");
    }
  });

  it("only the provider's delivered / read reports are shown as success", () => {
    expect(describeAttempt(attempt("delivered")).tone).toBe("success");
    expect(describeAttempt(attempt("read")).tone).toBe("success");
    for (const status of ["queued", "sending", "accepted", "sent", "failed"]) {
      expect(describeAttempt(attempt(status)).tone).not.toBe("success");
    }
  });

  it("a failed retry shows the safe reason it failed", () => {
    const view = describeAttempt(
      attempt("failed", {
        failure: { category: "x", label: "X", safe_error: "Not on WhatsApp", can_retry: false },
      }),
    );
    expect(view.tone).toBe("danger");
    expect(view.text).toContain("Not on WhatsApp");
  });

  it("an unrecognised status is shown as the server said, with no claim", () => {
    const view = describeAttempt(attempt("weird", { status_label: "Weird" }));
    expect(view).toEqual({ label: "Weird", tone: "neutral", text: "" });
  });
});

describe("pending detection (drives polling)", () => {
  it("only in-flight statuses are pending", () => {
    for (const s of ["queued", "sending", "accepted", "sent"]) expect(isPending(attempt(s))).toBe(true);
    for (const s of ["delivered", "read", "failed"]) expect(isPending(attempt(s))).toBe(false);
    expect(isPending(null)).toBe(false);
  });
  it("a list needs polling only while some retry is in flight", () => {
    expect(hasPending([row(null), row(attempt("delivered"))])).toBe(false);
    expect(hasPending([row(null), row(attempt("accepted"))])).toBe(true);
  });
});

describe("labels", () => {
  it("every kind has a plain label", () => {
    expect(KIND_LABEL).toEqual({
      retryable: "Safe to retry",
      not_retryable: "Fix first",
      unknown: "Outcome unclear",
      external: "Fix in Meta",
    });
  });
  it("recipient falls back from name to phone to a neutral phrase", () => {
    expect(recipientLabel(row(null, { name: "Asha", phone_number: "91" }))).toBe("Asha");
    expect(recipientLabel(row(null, { name: "", phone_number: "91" }))).toBe("91");
    expect(recipientLabel(row(null))).toBe("Unknown recipient");
  });
  it("cost wording separates free, estimate and unknown", () => {
    expect(costLine({ status: "free", note: "n." })).toMatch(/^No charge/);
    expect(costLine({ status: "estimate", amount: "0.115", currency: "INR", note: "n." })).toMatch(
      /^Estimated INR 0\.115/,
    );
    expect(costLine({ status: "unknown", note: "n." })).toMatch(/^Cost unknown/);
  });
});
