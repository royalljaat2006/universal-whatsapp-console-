"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api/errors";

export interface QueryState<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  reload: () => void;
}

interface Settled<T> {
  key: string;
  data?: T;
  error?: ApiError;
}

/**
 * Minimal data-fetching hook (loading / error / reload, abort on unmount).
 * ``deps`` must be JSON-serialisable (ids, filters). Deliberately small; a
 * caching library can replace it when the inbox needs shared caches and
 * optimistic updates (docs/adr/ADR-009).
 */
export function useApiQuery<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
  enabled = true,
): QueryState<T> {
  const [version, setVersion] = useState(0);
  const [settled, setSettled] = useState<Settled<T>>({ key: "" });
  const key = JSON.stringify([deps, version]);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    fetcher(controller.signal)
      .then((data) => setSettled({ key, data, error: undefined }))
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        const error = cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause));
        setSettled((prev) => ({ key, data: prev.data, error }));
      });
    return () => controller.abort();
    // `fetcher` is intentionally excluded: callers pass inline closures and
    // `key` already captures everything that should trigger a refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return {
    data: settled.data,
    error: settled.error,
    loading: enabled && settled.key !== key,
    reload,
  };
}
