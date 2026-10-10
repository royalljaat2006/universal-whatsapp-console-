import type { Business } from "@/types/api";

/** Everything the console can do from a typed command. Anything else is
 * answered honestly as unsupported - there is no free-form guessing. */
export type IntentId =
  | "performance"
  | "failed_messages"
  | "costs"
  | "campaigns_running"
  | "campaigns_overview"
  | "templates_attention"
  | "unread"
  | "navigate"
  | "launch_campaign"
  | "pause_campaign"
  | "search"
  | "unsupported";

export type RangeKey = "today" | "yesterday" | "7d" | "30d";

export interface ParsedCommand {
  intent: IntentId;
  /** The user's text, normalised for display. */
  text: string;
  range: RangeKey;
  /** True when the user named no period and the default was applied. */
  rangeDefaulted: boolean;
  /** Free text left after the command words (campaign name, search term). */
  subject: string;
  /** Destination for ``navigate``. */
  href?: string;
  /** Why an ``unsupported`` request was refused. */
  reason?: string;
  /** Set when ``search`` was chosen as the fallback rather than asked for. */
  fallbackSearch?: boolean;
}

export type Tone = "neutral" | "success" | "warning" | "danger";

export interface Stat {
  label: string;
  value: string;
  hint?: string;
  tone?: Tone;
}

export interface ResultRow {
  id: string;
  title: string;
  subtitle?: string;
  meta?: string;
  badge?: { text: string; tone: Tone };
  href?: string;
}

export interface ResultLink {
  label: string;
  href: string;
}

export interface Confirmation {
  /** What the confirm button says, e.g. "Launch campaign". */
  label: string;
  destructive?: boolean;
  /** Facts shown before the user commits (from the server, not guessed). */
  details: string[];
  blockedReason?: string;
  run: () => Promise<CommandResult>;
}

export type ResultKind = "answer" | "confirm" | "done" | "empty" | "unsupported" | "denied";

export interface CommandResult {
  kind: ResultKind;
  title: string;
  summary?: string;
  stats?: Stat[];
  rows?: ResultRow[];
  notes?: string[];
  links?: ResultLink[];
  confirmation?: Confirmation;
  /** Set by the ``navigate`` intent; the UI performs the route change. */
  navigateTo?: string;
}

export interface CommandContext {
  business: Business | null;
  businesses: Business[];
  hasPermission: (code: string) => boolean;
  now?: Date;
}
