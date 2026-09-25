import { describe, expect, it } from "vitest";

import { count, durationLabel, money, percent, presetRange, shiftDate, todayIn } from "./analytics";

describe("analytics formatting", () => {
  it("renders missing rates as no-data, never 0 %", () => {
    expect(percent(null)).toBe("—");
    expect(percent(undefined)).toBe("—");
    expect(percent(0)).toBe("0.0%");
    expect(percent(0.4567)).toBe("45.7%");
    expect(count(null)).toBe("—");
  });

  it("formats money per currency and marks absence", () => {
    expect(money([])).toBe("—");
    expect(money([{ currency: "INR", amount: "0.230000" }])).toBe("INR 0.23");
  });

  it("computes business-local date presets", () => {
    // 20:00 UTC on 22 Sep is already 23 Sep in India.
    const now = new Date(Date.UTC(2026, 8, 22, 20, 0));
    expect(todayIn("Asia/Kolkata", now)).toBe("2026-09-23");
    expect(todayIn("UTC", now)).toBe("2026-09-22");
    expect(presetRange("today", "Asia/Kolkata", now)).toEqual({
      date_from: "2026-09-23",
      date_to: "2026-09-23",
    });
    expect(presetRange("yesterday", "Asia/Kolkata", now)).toEqual({
      date_from: "2026-09-22",
      date_to: "2026-09-22",
    });
    expect(presetRange("7d", "Asia/Kolkata", now)).toEqual({
      date_from: "2026-09-17",
      date_to: "2026-09-23",
    });
    expect(presetRange("30d", "UTC", now).date_from).toBe("2026-08-24");
    expect(shiftDate("2026-03-01", -1)).toBe("2026-02-28");
  });

  it("labels durations", () => {
    expect(durationLabel(null)).toBe("—");
    expect(durationLabel(42)).toBe("42s");
    expect(durationLabel(125)).toBe("2m 5s");
    expect(durationLabel(3720)).toBe("1h 2m");
  });
});
