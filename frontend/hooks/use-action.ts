"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/errors";
import { toApiError } from "@/lib/format";

/** Runs a mutation with busy/error tracking, then calls `onDone` (typically a
 * `reload()`) on success. Shared by settings panels that list-then-mutate. */
export function useAction(onDone: () => void) {
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  async function run(action: () => Promise<unknown>): Promise<boolean> {
    setError(null);
    setBusy(true);
    try {
      await action();
      onDone();
      return true;
    } catch (cause) {
      setError(toApiError(cause));
      return false;
    } finally {
      setBusy(false);
    }
  }
  return { run, error, busy };
}
