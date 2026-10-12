"use client";

import { MenuIcon, PanelLeftCloseIcon, PanelLeftOpenIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";

import { Wordmark } from "@/components/brand/logo";
import { BusinessSwitcher } from "@/components/layout/business-switcher";
import { Notifications } from "@/components/layout/notifications";
import { UserMenu } from "@/components/layout/user-menu";
import { Button } from "@/components/ui/button";
import { SearchInput } from "@/components/ui/page";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { PageSkeleton } from "@/components/ui/states";
import { ToastProvider } from "@/components/ui/toast";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { labelFor, sectionForPath, visibleSections } from "@/features/registry";
import { emitSearch } from "@/lib/global-search";
import { cn } from "@/lib/utils";
import { useSession } from "@/stores/session-context";
import { useUiMode } from "@/stores/ui-mode";

const COLLAPSE_KEY = "wp.sidebar.collapsed";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

function Navigation({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  const pathname = usePathname();
  const { hasPermission } = useSession();
  const current = sectionForPath(pathname);
  const { mode } = useUiMode();
  const items = visibleSections(mode, hasPermission);
  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {items.map((section) => {
        const Icon = section.icon;
        const isActive = current?.href === section.href;
        const link = (
          <Link
            href={section.href}
            onClick={onNavigate}
            aria-current={isActive ? "page" : undefined}
            aria-label={collapsed ? labelFor(section, mode) : undefined}
            className={cn(
              "group/nav focus-visible:ring-ring/50 relative flex h-9 items-center gap-3 rounded-md px-2.5 text-[13px] font-medium transition-colors outline-none focus-visible:ring-2",
              isActive
                ? "bg-sidebar-accent text-sidebar-accent-foreground"
                : "text-sidebar-foreground/80 hover:bg-muted hover:text-foreground",
              collapsed && "justify-center px-0",
            )}
          >
            {isActive ? (
              <span aria-hidden className="bg-primary absolute inset-y-1.5 left-0 w-0.5 rounded-full" />
            ) : null}
            <Icon
              aria-hidden
              className={cn(
                "size-4 shrink-0",
                isActive ? "text-primary" : "text-muted-foreground group-hover/nav:text-foreground",
              )}
            />
            {collapsed ? null : <span className="truncate">{labelFor(section, mode)}</span>}
          </Link>
        );
        return collapsed ? (
          <Tooltip key={section.href}>
            <TooltipTrigger render={<span className="block" />}>{link}</TooltipTrigger>
            <TooltipContent side="right">{labelFor(section, mode)}</TooltipContent>
          </Tooltip>
        ) : (
          <div key={section.href}>{link}</div>
        );
      })}
    </nav>
  );
}

function SidebarBody({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  return (
    <div className="flex h-full flex-col gap-4 p-3">
      <Link
        href="/"
        onClick={onNavigate}
        className={cn("flex h-9 items-center px-1", collapsed && "justify-center px-0")}
        aria-label="Overview"
      >
        <Wordmark collapsed={collapsed} />
      </Link>
      <BusinessSwitcher collapsed={collapsed} />
      <Navigation collapsed={collapsed} onNavigate={onNavigate} />
    </div>
  );
}

function HeaderSearch() {
  const pathname = usePathname();
  const router = useRouter();
  const [value, setValue] = useState("");
  const inInbox = pathname.startsWith("/inbox");
  const target = inInbox ? "/inbox" : "/contacts";
  return (
    <form
      role="search"
      className="hidden w-full max-w-sm md:block"
      onSubmit={(event) => {
        event.preventDefault();
        const q = value.trim();
        if (!q) return;
        if (pathname.startsWith(target)) emitSearch(q);
        else router.push(`${target}?q=${encodeURIComponent(q)}`);
      }}
    >
      <label htmlFor="global-search" className="sr-only">
        {inInbox ? "Search conversations" : "Search contacts"}
      </label>
      <SearchInput
        id="global-search"
        className="w-full"
        placeholder={
          inInbox ? "Search conversations by name, phone or message…" : "Search contacts by name or phone…"
        }
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
    </form>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { user, active, business, loading } = useSession();
  const { mode } = useUiMode();
  // The shell renders only after the client-side session load, so reading
  // the stored preference in the initializer cannot cause a hydration mismatch.
  const [collapsed, setCollapsed] = useState(() => typeof window !== "undefined" && readCollapsed());
  const [drawerOpen, setDrawerOpen] = useState(false);

  function toggle() {
    setCollapsed((c) => {
      try {
        window.localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1");
      } catch {
        // storage unavailable: preference lasts for this page only
      }
      return !c;
    });
  }

  if (loading) {
    return (
      <div className="flex min-h-screen">
        <div className="bg-sidebar hidden w-60 border-r md:block" />
        <div className="flex-1 p-8">
          <PageSkeleton />
        </div>
      </div>
    );
  }
  if (!user) return null; // redirected to /login by the session provider

  const section = sectionForPath(pathname);
  const fullBleed = pathname.startsWith("/inbox");

  return (
    <ToastProvider>
      <TooltipProvider>
        <a
          href="#main"
          className="bg-primary text-primary-foreground sr-only z-50 rounded px-3 py-2 focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
        >
          Skip to content
        </a>
        <div className="flex h-dvh overflow-hidden">
          <aside
            className={cn(
              "bg-sidebar hidden shrink-0 flex-col border-r transition-[width] duration-200 ease-out md:flex",
              collapsed ? "w-16" : "w-60",
            )}
          >
            <div className="min-h-0 flex-1 overflow-y-auto">
              <SidebarBody collapsed={collapsed} />
            </div>
            <div className={cn("border-t p-2", collapsed && "flex justify-center")}>
              <Button
                variant="ghost"
                size={collapsed ? "icon" : "sm"}
                onClick={toggle}
                aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                aria-expanded={!collapsed}
                className={cn(!collapsed && "w-full justify-start")}
              >
                {collapsed ? <PanelLeftOpenIcon aria-hidden /> : <PanelLeftCloseIcon aria-hidden />}
                {collapsed ? null : "Collapse"}
              </Button>
            </div>
          </aside>

          <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
            <SheetContent side="left" className="w-72 p-0 md:hidden">
              <SheetTitle className="sr-only">Navigation</SheetTitle>
              <SidebarBody collapsed={false} onNavigate={() => setDrawerOpen(false)} />
            </SheetContent>
          </Sheet>

          <div className="flex min-w-0 flex-1 flex-col">
            <header className="bg-surface/95 supports-backdrop-filter:bg-surface/80 flex h-14 shrink-0 items-center gap-3 border-b px-3 backdrop-blur md:px-5">
              <Button
                variant="ghost"
                size="icon"
                className="md:hidden"
                aria-label="Open navigation"
                onClick={() => setDrawerOpen(true)}
              >
                <MenuIcon aria-hidden />
              </Button>
              <div className="flex min-w-0 items-center gap-2">
                <span className="text-sm font-semibold">{section ? labelFor(section, mode) : ""}</span>
                {business ? (
                  <span className="text-caption hidden items-center gap-1.5 truncate sm:flex">
                    <span aria-hidden>/</span>
                    <span className="text-foreground font-medium">{business.name}</span>
                    <span>
                      · {business.phone_number_count} number{business.phone_number_count === 1 ? "" : "s"}
                    </span>
                  </span>
                ) : null}
              </div>
              <div className="ml-auto flex flex-1 items-center justify-end gap-1.5">
                <HeaderSearch />
                <Notifications />
                <UserMenu />
              </div>
            </header>
            <main
              id="main"
              className={cn("min-h-0 flex-1", fullBleed ? "overflow-hidden" : "overflow-y-auto")}
            >
              {active ? (
                <div className={cn(fullBleed ? "h-full" : "mx-auto w-full max-w-[1400px] px-4 py-6 md:px-8")}>
                  {children}
                </div>
              ) : (
                <div className="p-8">
                  <p className="text-supporting">
                    Your account has no active organization membership.
                    {user?.is_staff ? (
                      <>
                        {" "}
                        As a platform administrator, use the{" "}
                        <Link href="/platform" className="text-primary underline underline-offset-2">
                          platform console
                        </Link>{" "}
                        instead.
                      </>
                    ) : (
                      " Ask an administrator to add you."
                    )}
                  </p>
                </div>
              )}
            </main>
          </div>
        </div>
      </TooltipProvider>
    </ToastProvider>
  );
}
