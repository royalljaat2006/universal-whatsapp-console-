import {
  BarChart3Icon,
  FileTextIcon,
  InboxIcon,
  LayoutDashboardIcon,
  type LucideIcon,
  MegaphoneIcon,
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
}

export const FEATURE_SECTIONS: FeatureSection[] = [
  {
    href: "/",
    label: "Overview",
    icon: LayoutDashboardIcon,
    permission: "organization.view",
    live: true,
    description: "Business summary, connection health and platform status.",
  },
  {
    href: "/inbox",
    label: "Inbox",
    icon: InboxIcon,
    permission: "conversations.view",
    live: true,
    description: "Realtime conversations for the selected business.",
  },
  {
    href: "/contacts",
    label: "Contacts",
    icon: UsersIcon,
    permission: "contacts.view",
    live: true,
    description: "Contacts created from real conversations, with tags, notes and history.",
  },
  {
    href: "/templates",
    label: "Templates",
    icon: FileTextIcon,
    permission: "templates.view",
    live: true,
    description: "Templates synced from Meta for the selected business's WABAs.",
  },
  {
    href: "/campaigns",
    label: "Campaigns",
    icon: MegaphoneIcon,
    permission: "campaigns.view",
    live: true,
    description: "Audience-based template campaigns with scheduling, live progress and reports.",
  },
  {
    href: "/analytics",
    label: "Analytics",
    icon: BarChart3Icon,
    permission: "analytics.view",
    live: true,
    description: "Message, number, template and cost analytics from aggregated real data.",
  },
  {
    href: "/numbers",
    label: "WhatsApp numbers",
    icon: SmartphoneIcon,
    permission: "numbers.view",
    live: true,
    description: "WhatsApp Business Accounts and phone numbers.",
  },
  {
    href: "/settings",
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
