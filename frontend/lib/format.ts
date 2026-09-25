import { ApiError } from "@/lib/api/errors";

/** Normalise anything thrown into an ApiError for <ApiErrorNotice>. */
export function toApiError(cause: unknown): ApiError {
  return cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause));
}

/** Time today, otherwise the date — for list rows. */
export function shortTime(value: string | null, now: Date = new Date()): string {
  if (!value) return "";
  const date = new Date(value);
  return date.toDateString() === now.toDateString()
    ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString();
}

/** Stable local-calendar key (YYYY-MM-DD) used to group a timeline by day. */
export function dayKey(value: string): string {
  const d = new Date(value);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function dayLabel(value: string, now: Date = new Date()): string {
  const key = dayKey(value);
  if (key === dayKey(now.toISOString())) return "Today";
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (key === dayKey(yesterday.toISOString())) return "Yesterday";
  return new Date(value).toLocaleDateString([], {
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatBytes(size: number | null | undefined): string {
  if (size == null) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/** Human "3h 12m left" for a service window expiry; null when expired. */
export function timeLeft(expiresAt: string | null, now: Date = new Date()): string | null {
  if (!expiresAt) return null;
  const ms = new Date(expiresAt).getTime() - now.getTime();
  if (ms <= 0) return null;
  const minutes = Math.floor(ms / 60000);
  const hours = Math.floor(minutes / 60);
  return hours > 0 ? `${hours}h ${minutes % 60}m left` : `${minutes}m left`;
}
