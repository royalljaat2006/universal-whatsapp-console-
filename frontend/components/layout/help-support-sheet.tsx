"use client";

import { usePathname } from "next/navigation";

import { Button } from "@/components/ui/button";
import { DetailList, Panel } from "@/components/ui/page";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useSession } from "@/stores/session-context";

const SUPPORT_EMAIL = process.env.NEXT_PUBLIC_SUPPORT_EMAIL ?? "";

/** Contextual support: prefills a mailto with the page, organization and
 * business the person is actually looking at, so they don't have to explain
 * it. Shows "not configured yet" rather than a broken mailto: link when
 * NEXT_PUBLIC_SUPPORT_EMAIL is unset. */
export function HelpSupportSheet({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { user, active, business } = useSession();
  const pathname = usePathname();

  const subject = `Support request — ${active?.organization.name ?? "console"}`;
  const body = [
    `Organization: ${active?.organization.name ?? "—"}`,
    `Business: ${business?.name ?? "—"}`,
    `Page: ${pathname}`,
    `Signed in as: ${user?.email ?? "—"}`,
    "",
    "Describe what you were trying to do and what happened:",
    "",
  ].join("\n");
  const mailto = SUPPORT_EMAIL
    ? `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
    : null;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full sm:max-w-sm">
        <SheetHeader className="border-b">
          <SheetTitle>Help &amp; support</SheetTitle>
          <SheetDescription>Get help with this workspace.</SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-4 px-4 pb-6">
          <Panel title="Your context" bodyClassName="p-4">
            <DetailList
              items={[
                { label: "Organization", value: active?.organization.name ?? "—" },
                { label: "Business", value: business?.name ?? "—" },
                { label: "Page", value: <span className="font-mono text-xs">{pathname}</span> },
              ]}
            />
          </Panel>
          {mailto ? (
            <Button render={<a href={mailto} />} nativeButton={false} className="w-fit">
              Contact support
            </Button>
          ) : (
            <p className="text-supporting">
              Support contact isn&rsquo;t configured yet. Ask an administrator to set
              NEXT_PUBLIC_SUPPORT_EMAIL.
            </p>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
