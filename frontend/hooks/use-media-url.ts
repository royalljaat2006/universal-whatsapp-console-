"use client";

import { useEffect, useState } from "react";

import { apiBlob } from "@/lib/api/client";

/** Object URL for a protected media path; revoked on change/unmount. */
export function useMediaUrl(path: string | null, enabled = true) {
  const [state, setState] = useState<{ path: string | null; url: string | null; failed: boolean }>({
    path: null,
    url: null,
    failed: false,
  });

  useEffect(() => {
    if (!path || !enabled) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    apiBlob(path, controller.signal)
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        setState({ path, url: objectUrl, failed: false });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ path, url: null, failed: true });
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path, enabled]);

  const current = state.path === path;
  return {
    url: current ? state.url : null,
    failed: current && state.failed,
    loading: Boolean(path) && !current,
  };
}
