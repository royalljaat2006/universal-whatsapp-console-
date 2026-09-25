import type { CurrencyAmount } from "@/types/api";

export const NO_DATA = "No data available";

/** A rate the backend returned as ``null`` means "no data", never 0 %. */
export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export function count(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString();
}

export function money(amounts: CurrencyAmount[] | undefined): string {
  if (!amounts || amounts.length === 0) return "—";
  return amounts
    .map((a) => `${a.currency} ${Number(a.amount).toLocaleString(undefined, { maximumFractionDigits: 4 })}`)
    .join(" · ");
}

/** YYYY-MM-DD for "now" in an IANA zone (business-local calendar day). */
export function todayIn(timeZone: string | undefined, now: Date = new Date()): string {
  try {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: timeZone || undefined,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(now);
  } catch {
    return new Intl.DateTimeFormat("en-CA").format(now);
  }
}

export function shiftDate(isoDate: string, days: number): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d + days));
  return date.toISOString().slice(0, 10);
}

export type RangePreset = "today" | "yesterday" | "7d" | "30d" | "custom";

export function presetRange(
  preset: Exclude<RangePreset, "custom">,
  timeZone: string | undefined,
  now: Date = new Date(),
): { date_from: string; date_to: string } {
  const today = todayIn(timeZone, now);
  switch (preset) {
    case "today":
      return { date_from: today, date_to: today };
    case "yesterday": {
      const y = shiftDate(today, -1);
      return { date_from: y, date_to: y };
    }
    case "7d":
      return { date_from: shiftDate(today, -6), date_to: today };
    case "30d":
      return { date_from: shiftDate(today, -29), date_to: today };
  }
}

export function durationLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}
