"use client";

import { useEffect, useRef } from "react";

const EVENT = "console:search";

/** Header search → the current page's search box (and ?q= for deep links). */
export function emitSearch(query: string): void {
  window.dispatchEvent(new CustomEvent<string>(EVENT, { detail: query }));
}

/**
 * Receive the global search term: once from ``?q=`` after mount, then on
 * every header search while the page stays mounted.
 */
export function useGlobalSearch(onSearch: (query: string) => void): void {
  const handler = useRef(onSearch);
  useEffect(() => {
    handler.current = onSearch;
  });
  useEffect(() => {
    const initial = new URLSearchParams(window.location.search).get("q");
    if (initial) handler.current(initial);
    const listener = (event: Event) => handler.current((event as CustomEvent<string>).detail);
    window.addEventListener(EVENT, listener);
    return () => window.removeEventListener(EVENT, listener);
  }, []);
}
