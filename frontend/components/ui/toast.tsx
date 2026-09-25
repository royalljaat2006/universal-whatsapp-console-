"use client";

import { CheckCircle2Icon, InfoIcon, TriangleAlertIcon, XIcon } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState } from "react";

import { cn } from "@/lib/utils";

type ToastTone = "success" | "error" | "info";
interface ToastItem {
  id: number;
  tone: ToastTone;
  title: string;
  description?: string;
}

interface ToastApi {
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
  info: (title: string, description?: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);
let nextId = 1;

const ICONS = { success: CheckCircle2Icon, error: TriangleAlertIcon, info: InfoIcon };
const TONE = {
  success: "text-success",
  error: "text-destructive",
  info: "text-info",
};

/**
 * Minimal toast system (no dependency). Toasts confirm the outcome of a real
 * request; they are announced through an aria-live region.
 */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const dismiss = useCallback((id: number) => setItems((all) => all.filter((t) => t.id !== id)), []);
  const push = useCallback(
    (tone: ToastTone, title: string, description?: string) => {
      const id = nextId++;
      setItems((all) => [...all.slice(-3), { id, tone, title, description }]);
      setTimeout(() => dismiss(id), tone === "error" ? 7000 : 4000);
    },
    [dismiss],
  );
  const api = useMemo<ToastApi>(
    () => ({
      success: (t, d) => push("success", t, d),
      error: (t, d) => push("error", t, d),
      info: (t, d) => push("info", t, d),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        aria-live="polite"
        aria-relevant="additions"
        className="pointer-events-none fixed right-4 bottom-4 z-[60] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
      >
        {items.map((t) => {
          const Icon = ICONS[t.tone];
          return (
            <div
              key={t.id}
              role={t.tone === "error" ? "alert" : "status"}
              className="bg-popover animate-in fade-in slide-in-from-bottom-2 pointer-events-auto flex items-start gap-3 rounded-lg border px-3 py-2.5 shadow-lg"
            >
              <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", TONE[t.tone])} />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{t.title}</p>
                {t.description ? <p className="text-caption mt-0.5">{t.description}</p> : null}
              </div>
              <button
                type="button"
                aria-label="Dismiss notification"
                className="text-muted-foreground hover:text-foreground rounded p-0.5"
                onClick={() => dismiss(t.id)}
              >
                <XIcon aria-hidden className="size-3.5" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

const NOOP: ToastApi = { success: () => {}, error: () => {}, info: () => {} };

export function useToast(): ToastApi {
  return useContext(ToastContext) ?? NOOP;
}
