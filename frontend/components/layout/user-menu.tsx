"use client";

import { Building2Icon, CheckIcon, LogOutIcon } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { InitialsAvatar } from "@/components/ui/initials-avatar";
import { useSession } from "@/stores/session-context";

export function UserMenu() {
  const { user, memberships, active, selectOrganization, logout } = useSession();
  if (!user) return null;
  const name = user.full_name || user.email;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={`Account menu for ${name}`}
        className="hover:bg-muted focus-visible:ring-ring/50 flex items-center gap-2 rounded-md px-1.5 py-1 outline-none focus-visible:ring-2"
      >
        <InitialsAvatar name={name} seed={user.email} size="sm" />
        <span className="hidden max-w-36 flex-col text-left leading-tight lg:flex">
          <span className="truncate text-[13px] font-medium">{name}</span>
          <span className="text-caption truncate capitalize">{active?.role ?? ""}</span>
        </span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuGroup>
          <DropdownMenuLabel>
            <span className="text-foreground block truncate text-sm font-medium">{name}</span>
            <span className="block truncate">{user.email}</span>
          </DropdownMenuLabel>
        </DropdownMenuGroup>
        {memberships.length > 1 ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuLabel>Organization</DropdownMenuLabel>
              {memberships.map((m) => (
                <DropdownMenuItem
                  key={m.organization.id}
                  onClick={() => selectOrganization(m.organization.id)}
                >
                  <Building2Icon aria-hidden />
                  <span className="flex-1 truncate">{m.organization.name}</span>
                  {m.organization.id === active?.organization.id ? (
                    <CheckIcon aria-label="Current organization" className="text-primary" />
                  ) : null}
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
          </>
        ) : null}
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => void logout()}>
          <LogOutIcon aria-hidden />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
