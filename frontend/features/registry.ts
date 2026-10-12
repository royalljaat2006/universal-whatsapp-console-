import {
  AlertTriangleIcon,
  BarChart3Icon,
  FileTextIcon,
  InboxIcon,
  LayoutDashboardIcon,
  type LucideIcon,
  MegaphoneIcon,
  PlugIcon,
  SettingsIcon,
  SmartphoneIcon,
  UsersIcon,
} from "lucide-react";

import type { CapabilityState } from "@/components/status/capability-status";

/**
 * Console sections and their honest implementation state. A section flips to
 * "live" only when its backend API exists — never by rendering sample data.
 * Navigation only shows live sections the member has permission for.
 */
export interface FeatureSection {
  href: string;
  label: string;
  icon: LucideIcon;
  permission: string;
  live: boolean;
  state?: CapabilityState;
  description: string;
  /** Shown in Simple mode. Advanced mode shows every live section the person may use. */
  essential: boolean;
  /** Plainer wording used in Simple mode, when it differs. */
  simpleLabel?: string;
}

export const FEATURE_SECTIONS: FeatureSection[] = [
  {
    href: "/",
    essential: true,
    label: "Overview",
    simpleLabel: "Home",
    icon: LayoutDashboardIcon,
    permission: "organization.view",
    live: true,
    description: "Business summary, connection health and platform status.",
  },
  {
    href: "/setup",
    essential: true,
    label: "Set up WhatsApp",
    icon: PlugIcon,
    permission: "numbers.view",
    live: true,
    description: "Connect your account, check it really works, and send a verified test message.",
  },
  {
    href: "/inbox",
    essential: true,
    label: "Inbox",
    icon: InboxIcon,
    permission: "conversations.view",
    live: true,
    description: "Realtime conversations for the selected business.",
  },
  {
    href: "/failed-messages",
    essential: true,
    label: "Failed messages",
    icon: AlertTriangleIcon,
    permission: "conversations.view",
    live: true,
    description: "Messages that did not go through, why, and the safe next step.",
  },
  {
    href: "/contacts",
    essential: true,
    label: "Contacts",
    icon: UsersIcon,
    permission: "contacts.view",
    live: true,
    description: "Contacts created from real conversations, with tags, notes and history.",
  },
  {
    href: "/templates",
    essential: true,
    label: "Templates",
    icon: FileTextIcon,
    permission: "templates.view",
    live: true,
    description: "Templates synced from Meta for the selected business's WABAs.",
  },
  {
    href: "/campaigns",
    essential: true,
    label: "Campaigns",
    icon: MegaphoneIcon,
    permission: "campaigns.view",
    live: true,
    description: "Audience-based template campaigns with scheduling, live progress and reports.",
  },
  {
    href: "/analytics",
    essential: false,
    label: "Analytics",
    icon: BarChart3Icon,
    permission: "analytics.view",
    live: true,
    description: "Message, number, template and cost analytics from aggregated real data.",
  },
  {
    href: "/numbers",
    essential: true,
    label: "WhatsApp numbers",
    icon: SmartphoneIcon,
    permission: "numbers.view",
    live: true,
    description: "WhatsApp Business Accounts and phone numbers.",
  },
  {
    href: "/settings",
    essential: true,
    label: "Settings",
    icon: SettingsIcon,
    permission: "organization.view",
    live: true,
    description: "Workspace, business access, teams, tags and audit log.",
  },
];

export function sectionFor(href: string): FeatureSection | undefined {
  return FEATURE_SECTIONS.find((section) => section.href === href);
}

export function sectionForPath(pathname: string): FeatureSection | undefined {
  if (pathname === "/") return FEATURE_SECTIONS[0];
  return FEATURE_SECTIONS.find((s) => s.href !== "/" && pathname.startsWith(s.href));
}

export type NavMode = "simple" | "advanced";

/**
 * The sections to show in the navigation. The permission check ALWAYS applies
 * first, in both modes; Simple mode can only hide more, never reveal or allow
 * anything (so ``simple`` is always a subset of ``advanced``).
 */
export function visibleSections(
  mode: NavMode,
  hasPermission: (code: string) => boolean,
): FeatureSection[] {
  return FEATURE_SECTIONS.filter(
    (s) => s.live && hasPermission(s.permission) && (mode === "advanced" || s.essential),
  );
}

export function labelFor(section: FeatureSection, mode: NavMode): string {
  return mode === "simple" && section.simpleLabel ? section.simpleLabel : section.label;
}
