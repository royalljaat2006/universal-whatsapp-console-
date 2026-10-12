"use client";

import { createContext, useCallback, useContext, useMemo, useSyncExternalStore } from "react";

/**
 * Simple vs Advanced mode.
 *
 * A *presentation* preference only: it decides how much of the console is
 * shown, never what a person may do. Every permission check (navigation,
 * pages, API calls) is identical in both modes and the server remains the
 * authority, so switching mode can neither reveal data nor grant an action.
 * Advanced shows everything the person already had; Simple shows the everyday
 * essentials in plainer wording and leaves the rest one click away.
 *
 * Stored per browser (a convenience, like the sidebar collapse), read through
 * useSyncExternalStore so there is no setState-in-effect, with an in-memory
 * fallback so the switch still works when storage is blocked.
 */

export type UiMode = "simple" | "advanced";

export const UI_MODE_KEY = "console:ui-mode";
const EVENT = "console:ui-mode-changed";
const DEFAULT_MODE: UiMode = "simple";

let inMemory: UiMode | null = null;

export function parseMode(value: unknown): UiMode | null {
  return value === "simple" || value === "advanced" ? value : null;
}

export function readStoredMode(): UiMode {
  if (inMemory) return inMemory;
  try {
    return parseMode(window.localStorage.getItem(UI_MODE_KEY)) ?? DEFAULT_MODE;
  } catch {
    return DEFAULT_MODE;
  }
}

export function writeStoredMode(mode: UiMode): void {
  inMemory = mode;
  try {
    window.localStorage.setItem(UI_MODE_KEY, mode);
  } catch {
    // Not persisted; the in-memory value still applies for this page load.
  }
  window.dispatchEvent(new Event(EVENT));
}

/** Test helper: forget the in-memory value so storage is the source again. */
export function resetModeForTests(): void {
  inMemory = null;
}

function subscribe(notify: () => void): () => void {
  window.addEventListener(EVENT, notify);
  window.addEventListener("storage", notify);
  return () => {
    window.removeEventListener(EVENT, notify);
    window.removeEventListener("storage", notify);
  };
}

interface UiModeValue {
  mode: UiMode;
  isSimple: boolean;
  setMode: (mode: UiMode) => void;
}

const UiModeContext = createContext<UiModeValue | null>(null);

export function UiModeProvider({ children }: { children: React.ReactNode }) {
  // Server render and first client render both use the default, so hydration matches.
  const mode = useSyncExternalStore(subscribe, readStoredMode, () => DEFAULT_MODE);
  const setMode = useCallback((next: UiMode) => writeStoredMode(next), []);
  const value = useMemo(() => ({ mode, isSimple: mode === "simple", setMode }), [mode, setMode]);
  return <UiModeContext.Provider value={value}>{children}</UiModeContext.Provider>;
}

export function useUiMode(): UiModeValue {
  const context = useContext(UiModeContext);
  if (!context) throw new Error("useUiMode must be used inside UiModeProvider");
  return context;
}
