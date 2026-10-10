import type { IntentId, ParsedCommand, RangeKey } from "./types";

/**
 * Deterministic command understanding. No model, no network: a request either
 * matches a supported intent by explicit rules or it is refused / treated as a
 * plain search and labelled as such. The parse result never carries ids or
 * permissions - those are resolved later from trusted session context and
 * enforced by the server.
 */

export const NAVIGATION_TARGETS: Record<string, string> = {
  inbox: "/inbox",
  conversations: "/inbox",
  contacts: "/contacts",
  templates: "/templates",
  campaigns: "/campaigns",
  analytics: "/analytics",
  numbers: "/numbers",
  "phone numbers": "/numbers",
  notifications: "/notifications",
  settings: "/settings",
  overview: "/",
  home: "/",
};

const RANGE_RULES: { key: RangeKey; pattern: RegExp }[] = [
  { key: "yesterday", pattern: /\byesterday\b/ },
  {
    key: "30d",
    pattern: /\b(last|past)\s+(30|thirty)\s+days?\b|\b(this|last|past)\s+month\b|\b30\s*d(ays?)?\b/,
  },
  { key: "7d", pattern: /\b(last|past)\s+(7|seven)\s+days?\b|\b(this|last|past)\s+week\b|\b7\s*d(ays?)?\b/ },
  { key: "today", pattern: /\btoday\b|\bso far\b|\bright now\b|\bcurrent(ly)?\b/ },
];

// Imperative verbs that ask for an action we do not implement. Anchored to the
// START of the request so a read like "message delivery performance" is not
// mistaken for "message someone", and checked before the noun rules so
// "cancel the campaign" can never fall through to a harmless-looking listing.
const UNSUPPORTED_ACTION =
  /^(?:(?:please|kindly)\s+|(?:can|could|would) you\s+|i (?:want|need) to\s+)?(delete|remove|erase|create|make|add|invite|update|edit|change|rename|import|export|schedule|resume|cancel|stop|reply|respond|send|text|broadcast|message|assign|archive|block|unblock|connect|disconnect)\b/;

export function normalise(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s'&.-]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function detectRange(t: string): { range: RangeKey; defaulted: boolean } {
  for (const { key, pattern } of RANGE_RULES) {
    if (pattern.test(t)) return { range: key, defaulted: false };
  }
  return { range: "today", defaulted: true };
}

function stripWords(t: string, words: RegExp): string {
  return t.replace(words, " ").replace(/\s+/g, " ").trim();
}

function campaignSubject(t: string): string {
  return stripWords(
    t,
    /\b(please|can you|could you|the|a|an|my|our|campaign|campaigns|launch|start|go live with|send|pause|hold|now)\b/g,
  );
}

function make(intent: IntentId, text: string, t: string, extra: Partial<ParsedCommand> = {}): ParsedCommand {
  const { range, defaulted } = detectRange(t);
  return { intent, text, range, rangeDefaulted: defaulted, subject: "", ...extra };
}

export function parseCommand(input: string): ParsedCommand {
  const text = input.trim().replace(/\s+/g, " ");
  const t = normalise(text);

  if (t.length < 2) {
    return make("unsupported", text, t, { reason: "Type what you would like to do or find." });
  }

  // Consequential actions first, so "launch" is never mistaken for a read.
  if (/\b(launch|go live|start)\b.*\bcampaign\b|\bcampaign\b.*\b(launch|go live)\b/.test(t)) {
    return make("launch_campaign", text, t, { subject: campaignSubject(t) });
  }
  if (/\b(pause|hold)\b.*\bcampaign\b|\bcampaign\b.*\bpause\b/.test(t)) {
    return make("pause_campaign", text, t, { subject: campaignSubject(t) });
  }

  // Explicit "open X" requests, or a bare page name that has no data meaning of
  // its own. "show campaigns" is a question about data, not a navigation.
  const nav =
    /^(?:open|go to|goto|take me to)\s+(?:the\s+|my\s+)?(inbox|conversations|contacts|templates|campaigns|analytics|phone numbers|numbers|notifications|settings|overview|home)(?:\s+page)?$/.exec(
      t,
    ) ?? /^(inbox|contacts|analytics|numbers|notifications|settings|overview|home)$/.exec(t);
  if (nav) return make("navigate", text, t, { href: NAVIGATION_TARGETS[nav[1]] });

  if (UNSUPPORTED_ACTION.test(t)) {
    return make("unsupported", text, t, {
      reason:
        "I can look things up and launch or pause a campaign, but I can't do that from here yet. Nothing was changed.",
    });
  }

  if (/\b(fail|failed|failing|failure|failures|undelivered|not delivered|errors?)\b/.test(t)) {
    return make("failed_messages", text, t);
  }
  if (/\b(cost|costs|spend|spent|spending|price|pricing|charges?|billing)\b/.test(t)) {
    return make("costs", text, t);
  }
  if (/\bcampaigns?\b/.test(t)) {
    if (/\b(running|active|ongoing|in progress|live|currently|current|sending)\b/.test(t)) {
      return make("campaigns_running", text, t);
    }
    return make("campaigns_overview", text, t);
  }
  if (/\btemplates?\b/.test(t)) {
    if (
      /\b(attention|rejected|paused|disabled|problems?|issues?|review|pending|quality|need|needs|wrong|broken|flagged|low)\b/.test(
        t,
      )
    ) {
      return make("templates_attention", text, t);
    }
    return make("search", text, t, { subject: stripWords(t, /\b(show|find|list|the|my|all|templates?)\b/g) });
  }
  if (/\b(unread|waiting|awaiting|unanswered|need(s)? (a )?repl(y|ies))\b/.test(t)) {
    return make("unread", text, t);
  }
  if (
    /\b(deliver(y|ed)?|performance|read rate|delivery rate|how (are|is|did)|messages? (sent|today|volume)|message stats|health)\b/.test(
      t,
    )
  ) {
    return make("performance", text, t);
  }

  const subject = stripWords(t, /\b(show|find|search|look up|lookup|get|me|for|the|my|all|a|an)\b/g);
  if (subject.length < 2) {
    return make("unsupported", text, t, { reason: "I couldn't tell what to look for." });
  }
  return make("search", text, t, { subject, fallbackSearch: true });
}

export const SUPPORTED_EXAMPLES: string[] = [
  "Show today's message delivery performance",
  "Find messages that failed this week",
  "Show campaigns currently running",
  "Find templates that need attention",
  "Compare messaging costs across my businesses",
  "Show unread conversations",
  "Launch campaign <name>",
  "Pause campaign <name>",
  "Find <contact name or phone number>",
  "Open templates",
];
