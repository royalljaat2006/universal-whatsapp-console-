"use client";

import { CheckCircle2Icon, CircleIcon } from "lucide-react";
import Link from "next/link";

import { Panel } from "@/components/ui/page";
import { useApiQuery } from "@/hooks/use-api-query";
import { organizationService } from "@/services/organizations";
import { useSession } from "@/stores/session-context";
import type { PhoneNumber } from "@/types/api";

interface Step {
  id: string;
  label: string;
  description: string;
  done: boolean;
  href: string;
  cta: string;
}

/**
 * Real, data-driven setup checklist — not stored progress. Disappears once
 * every step it can see is done; steps the caller lacks permission to verify
 * are left out entirely rather than guessed at.
 */
export function OnboardingChecklist({ numbers }: { numbers: PhoneNumber[] | undefined }) {
  const { businesses, hasPermission, loading: sessionLoading } = useSession();
  const canViewMembers = hasPermission("members.view");
  const members = useApiQuery(() => organizationService.members(), [], canViewMembers);

  if (sessionLoading) return null;

  const hasBusiness = businesses.length > 0;
  const steps: Step[] = [
    {
      id: "business",
      label: "Create a business",
      description: "A business is the workspace that owns your WhatsApp numbers and conversations.",
      done: hasBusiness,
      href: "/settings",
      cta: "Create business",
    },
  ];

  if (hasBusiness && hasPermission("numbers.view")) {
    steps.push({
      id: "whatsapp",
      label: "Connect a WhatsApp number",
      description: "Connect a WhatsApp Business Account to start sending and receiving messages.",
      done: Boolean(numbers && numbers.length > 0),
      href: "/numbers",
      cta: "Connect WhatsApp",
    });
  }

  if (canViewMembers) {
    steps.push({
      id: "team",
      label: "Invite your team",
      description: "Add teammates so they can help manage conversations and campaigns.",
      done: (members.data?.results.length ?? 0) > 1,
      href: "/settings",
      cta: "Invite teammate",
    });
  }

  const remaining = steps.filter((step) => !step.done);
  if (remaining.length === 0) return null;

  return (
    <Panel
      title="Finish setting up your workspace"
      description={`${steps.length - remaining.length} of ${steps.length} steps complete`}
    >
      <ul className="divide-y">
        {steps.map((step) => (
          <li key={step.id} className="flex items-center justify-between gap-3 px-4 py-3">
            <span className="flex items-start gap-3">
              {step.done ? (
                <CheckCircle2Icon aria-hidden className="text-success mt-0.5 size-5 shrink-0" />
              ) : (
                <CircleIcon aria-hidden className="text-muted-foreground mt-0.5 size-5 shrink-0" />
              )}
              <span>
                <span className="block text-sm font-medium">{step.label}</span>
                <span className="text-caption block">{step.description}</span>
              </span>
            </span>
            {!step.done ? (
              <Link href={step.href} className="text-primary shrink-0 text-xs font-medium hover:underline">
                {step.cta}
              </Link>
            ) : null}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
