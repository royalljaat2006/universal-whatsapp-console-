import { cn } from "@/lib/utils";

/** Product mark: a message bubble with a signal stroke (not a third-party logo). */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={cn("size-8", className)}>
      <rect width="32" height="32" rx="8" className="fill-primary" />
      <path
        d="M9 10.5A3.5 3.5 0 0 1 12.5 7h7A3.5 3.5 0 0 1 23 10.5v6a3.5 3.5 0 0 1-3.5 3.5H15l-4.2 3.3a.6.6 0 0 1-1-.5V19.7A3.5 3.5 0 0 1 9 16.5z"
        fill="white"
      />
      <path
        d="M13 13.7l2.1 2.1 4-4.3"
        fill="none"
        stroke="var(--primary)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Wordmark({ className, collapsed = false }: { className?: string; collapsed?: boolean }) {
  return (
    <span className={cn("flex items-center gap-2.5", className)}>
      <LogoMark className="size-7" />
      {collapsed ? null : (
        <span className="flex flex-col leading-tight">
          <span className="text-[13px] font-semibold tracking-tight">WhatsApp Console</span>
          <span className="text-muted-foreground text-[11px]">Messaging operations</span>
        </span>
      )}
    </span>
  );
}
