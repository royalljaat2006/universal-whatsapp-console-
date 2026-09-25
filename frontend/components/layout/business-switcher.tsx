"use client";

import { Building2Icon, CheckIcon, ChevronsUpDownIcon } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { useSession } from "@/stores/session-context";

function numbersLabel(count: number): string {
  return `${count} WhatsApp number${count === 1 ? "" : "s"}`;
}

/**
 * Workspace switcher. Lists only the businesses the server says this member
 * may access, with their real connected-number counts. Switching re-scopes
 * every request (X-Business-ID) and remounts business-scoped pages.
 */
export function BusinessSwitcher({ collapsed = false }: { collapsed?: boolean }) {
  const { businesses, business, selectBusiness } = useSession();

  if (businesses.length === 0) {
    return (
      <div className="text-caption rounded-md border border-dashed px-2.5 py-2">
        {collapsed ? "—" : "No business access"}
      </div>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={`Business: ${business?.name ?? "none"}. Switch business`}
        className={cn(
          "hover:bg-sidebar-accent focus-visible:ring-ring/50 flex w-full items-center gap-2.5 rounded-md border px-2 py-1.5 text-left outline-none focus-visible:ring-2",
          collapsed && "justify-center px-1",
        )}
      >
        <span className="bg-brand-soft text-primary flex size-7 shrink-0 items-center justify-center rounded-md text-xs font-semibold">
          {business?.name.slice(0, 2).toUpperCase() ?? <Building2Icon className="size-4" />}
        </span>
        {collapsed ? null : (
          <>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-medium">
                {business?.name ?? "Select business"}
              </span>
              {business ? (
                <span className="text-caption block truncate">
                  {numbersLabel(business.phone_number_count)}
                </span>
              ) : null}
            </span>
            <ChevronsUpDownIcon aria-hidden className="text-muted-foreground size-4 shrink-0" />
          </>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-64" align="start">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Businesses</DropdownMenuLabel>
          {businesses.map((b) => (
            <DropdownMenuItem key={b.id} onClick={() => selectBusiness(b.id)} className="py-1.5">
              <span className="bg-muted flex size-7 shrink-0 items-center justify-center rounded-md text-[11px] font-semibold">
                {b.name.slice(0, 2).toUpperCase()}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium">{b.name}</span>
                <span className="text-caption block">{numbersLabel(b.phone_number_count)}</span>
              </span>
              {b.id === business?.id ? (
                <CheckIcon aria-label="Current business" className="text-primary size-4" />
              ) : null}
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
