"use client";

import { ErrorState } from "@/components/ui/states";
import type { ApiError } from "@/lib/api/errors";

/** Inline API error (kept for existing callers; see ErrorState). */
export function ApiErrorNotice({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return <ErrorState error={error} onRetry={onRetry} inline />;
}
