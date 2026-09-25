import { describe, expect, it } from "vitest";

import { dayKey, dayLabel, formatBytes, timeLeft } from "./format";

describe("format helpers", () => {
  const now = new Date(2026, 8, 23, 12, 0, 0);

  it("labels timeline days relative to now", () => {
    expect(dayLabel(new Date(2026, 8, 23, 8).toISOString(), now)).toBe("Today");
    expect(dayLabel(new Date(2026, 8, 22, 23).toISOString(), now)).toBe("Yesterday");
    expect(dayKey(new Date(2026, 0, 5, 10).toISOString())).toBe("2026-01-05");
  });

  it("formats sizes and service-window time left", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(timeLeft(new Date(2026, 8, 23, 15, 30).toISOString(), now)).toBe("3h 30m left");
    expect(timeLeft(new Date(2026, 8, 23, 11).toISOString(), now)).toBeNull();
    expect(timeLeft(null, now)).toBeNull();
  });
});
