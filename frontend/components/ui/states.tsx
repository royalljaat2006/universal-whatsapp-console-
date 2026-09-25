"use client";

import {
  AlertTriangleIcon,
  CloudOffIcon,
  LockIcon,
  type LucideIcon,
  PlugZapIcon,
  RefreshCwIcon,
  SearchXIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { ApiError } from "@/lib/api/errors";
import { cn } from "@/lib/utils";

/** Honest empty state: explains why nothing is shown and what to do next. */
export function EmptyState({
  icon: Icon = SearchXIcon,
  title,
  description,
  action,
  className,
  compact = false,
}: {
  icon?: LucideIcon;
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
  compact?: boolean;
}) {
  return (
    <div
      role="status"
      className={cn(
        "flex flex-col items-center justify-center text-center",
        compact ? "gap-1.5 px-4 py-6" : "gap-2 px-6 py-12",
        className,
      )}
    >
      <span className="bg-muted text-muted-foreground mb-1 flex size-10 items-center justify-center rounded-full">
        <Icon aria-hidden className="size-5" />
      </span>
      <p className="text-section-title">{title}</p>
      {description ? <p className="text-supporting max-w-sm">{description}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

function describe(error: ApiError): { icon: LucideIcon; title: string } {
  if (error.status === 0 || error.code === "NETWORK_ERROR")
    return { icon: CloudOffIcon, title: "Connection failed" };
  if (error.status === 401 || error.code === "AUTHENTICATION_REQUIRED")
    return { icon: LockIcon, title: "Your session has ended" };
  if (error.status === 403) return { icon: LockIcon, title: "You don't have access to this" };
  if (error.status === 404) return { icon: SearchXIcon, title: "Not found in this business" };
  if (error.code === "CONFIGURATION_REQUIRED")
    return { icon: PlugZapIcon, title: "WhatsApp connection unavailable" };
  return { icon: AlertTriangleIcon, title: "Something went wrong" };
}

/**
 * Error display for API failures. Shows the backend's user-safe message and
 * the request id for support — never stack traces or provider internals.
 */
export function ErrorState({
  error,
  onRetry,
  inline = false,
  className,
}: {
  error: ApiError;
  onRetry?: () => void;
  inline?: boolean;
  className?: string;
}) {
  const { icon: Icon, title } = describe(error);
  if (inline) {
    return (
      <div
        role="alert"
        className={cn(
          "border-destructive/25 bg-danger-soft/60 flex items-start gap-3 rounded-lg border px-3 py-2.5",
          className,
        )}
      >
        <Icon aria-hidden className="text-destructive mt-0.5 size-4 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">{error.message || title}</p>
          <p className="text-caption font-mono">
            {error.code}
            {error.requestId ? ` · request ${error.requestId}` : ""}
          </p>
        </div>
        {onRetry ? (
          <Button variant="outline" size="xs" onClick={onRetry}>
            <RefreshCwIcon aria-hidden /> Retry
          </Button>
        ) : null}
      </div>
    );
  }
  return (
    <div role="alert" className={cn("flex flex-col items-center gap-2 px-6 py-12 text-center", className)}>
      <span className="bg-danger-soft text-destructive mb-1 flex size-10 items-center justify-center rounded-full">
        <Icon aria-hidden className="size-5" />
      </span>
      <p className="text-section-title">{title}</p>
      <p className="text-supporting max-w-md">{error.message}</p>
      <p className="text-caption font-mono">
        {error.code}
        {error.requestId ? ` · request ${error.requestId}` : ""}
      </p>
      {onRetry ? (
        <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
          <RefreshCwIcon aria-hidden /> Retry
        </Button>
      ) : null}
    </div>
  );
}

export function TableSkeleton({ rows = 6, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col divide-y">
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="flex items-center gap-4 px-4 py-3">
          {Array.from({ length: columns }, (_, c) => (
            <Skeleton key={c} className={cn("h-3.5", c === 0 ? "w-1/4" : "flex-1")} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function ConversationSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div role="status" aria-label="Loading conversations" className="flex flex-col">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3 px-3 py-3">
          <Skeleton className="size-9 rounded-full" />
          <div className="flex flex-1 flex-col gap-1.5">
            <Skeleton className="h-3 w-2/5" />
            <Skeleton className="h-3 w-4/5" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function PageSkeleton() {
  return (
    <div role="status" aria-label="Loading page" className="flex flex-col gap-4">
      <Skeleton className="h-7 w-56" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-20" />
        ))}
      </div>
      <Skeleton className="h-64" />
    </div>
  );
}
