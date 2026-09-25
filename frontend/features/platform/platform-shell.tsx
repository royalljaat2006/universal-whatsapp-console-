"use client";

import { ActivityIcon, Building2Icon, LockIcon, LogOutIcon, ScrollTextIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Wordmark } from "@/components/brand/logo";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/states";
import { ToastProvider } from "@/components/ui/toast";
import { cn } from "@/lib/utils";
import { useSession } from "@/stores/session-context";

const NAV = [
  { href: "/platform", label: "Organizations", icon: Building2Icon },
  { href: "/platform/audit-logs", label: "Audit log", icon: ScrollTextIcon },
  { href: "/platform/operations", label: "Operations", icon: ActivityIcon },
];

/**
 * Separate shell from the tenant console's AppShell: this surface has no
 * organization or business context at all (see
 * docs/productization/tenant-isolation.md — it's the one deliberately
 * cross-tenant part of the app), gated on is_staff rather than a membership.
 */
function PlatformFrame({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { user, logout } = useSession();
  return (
    <div className="bg-background flex min-h-dvh flex-col">
      <header className="bg-surface flex h-14 shrink-0 items-center gap-4 border-b px-4 md:px-6">
        <Wordmark />
        <span className="text-caption bg-brand-soft text-primary rounded-full px-2 py-0.5 font-medium">
          Platform console
        </span>
        <nav aria-label="Platform" className="ml-4 flex items-center gap-1">
          {NAV.map((item) => {
            const isActive =
              item.href === "/platform" ? pathname === "/platform" : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "flex h-8 items-center gap-1.5 rounded-md px-3 text-[13px] font-medium transition-colors",
                  isActive
                    ? "bg-brand-soft text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <item.icon aria-hidden className="size-4" />
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-supporting hidden sm:inline">{user?.email}</span>
          <Button variant="outline" size="sm" onClick={() => void logout()}>
            <LogOutIcon aria-hidden /> Sign out
          </Button>
        </div>
      </header>
      <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 py-6 md:px-8">{children}</main>
    </div>
  );
}

export function PlatformShell({ children }: { children: React.ReactNode }) {
  const { user, loading } = useSession();

  if (loading) return null;

  if (!user?.is_staff) {
    return (
      <div className="flex min-h-dvh items-center justify-center p-8">
        <EmptyState
          icon={LockIcon}
          title="Platform access required"
          description="This console is restricted to platform administrators. If you believe this is a mistake, ask a platform administrator to grant your account staff access."
        />
      </div>
    );
  }

  return (
    <ToastProvider>
      <PlatformFrame>{children}</PlatformFrame>
    </ToastProvider>
  );
}
