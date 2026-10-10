import { count, money, percent, presetRange } from "@/lib/analytics";
import { ApiError } from "@/lib/api/errors";
import { shortTime } from "@/lib/format";
import { analyticsService } from "@/services/analytics";
import { campaignService } from "@/services/campaigns";
import { contactService } from "@/services/contacts";
import { inboxService } from "@/services/inbox";
import { templateService } from "@/services/templates";
import type { Campaign, CampaignStatus } from "@/types/api";

import { SUPPORTED_EXAMPLES } from "./parse";
import type { CommandContext, CommandResult, ParsedCommand, RangeKey, ResultRow, Tone } from "./types";

/**
 * Executes a parsed command through the SAME typed services the rest of the
 * console uses. Nothing here is privileged: every call is made with the
 * session's own credentials, the active organization/business headers are
 * attached by the API client, and the backend decides - a 403/404 from the
 * server is the final answer even if the client-side permission check
 * above it passed. Client-side checks only exist to give a clear message
 * without a wasted round trip.
 *
 * Read commands run immediately. Consequential commands (launch/pause a
 * campaign) never execute from the command text: they return a
 * ``confirm`` result built from the server's own preview, and only the
 * explicit confirmation callback performs the write.
 */

const PERIOD_LABEL: Record<RangeKey, string> = {
  today: "today",
  yesterday: "yesterday",
  "7d": "in the last 7 days",
  "30d": "in the last 30 days",
};

const CAMPAIGN_TONE: Partial<Record<CampaignStatus, Tone>> = {
  running: "success",
  queued: "neutral",
  scheduled: "neutral",
  paused: "warning",
  failed: "danger",
  completed: "success",
};

function denied(permission: string, what: string): CommandResult {
  return {
    kind: "denied",
    title: "You don't have access to that",
    summary: `${what} needs the “${permission}” permission in this workspace. Ask an organization admin if you need it.`,
  };
}

function noBusiness(): CommandResult {
  return {
    kind: "empty",
    title: "Choose a business first",
    summary: "This command works on one business at a time. Pick one from the business switcher.",
  };
}

function campaignRow(c: Campaign): ResultRow {
  return {
    id: c.id,
    title: c.name,
    subtitle: [c.template_name, c.phone_number_display].filter(Boolean).join(" · "),
    meta: c.audience_count !== null ? `${count(c.audience_count)} recipients` : undefined,
    badge: { text: c.status, tone: CAMPAIGN_TONE[c.status] ?? "neutral" },
    href: "/campaigns",
  };
}

// --------------------------------------------------------------------- reads

async function performance(cmd: ParsedCommand, ctx: CommandContext): Promise<CommandResult> {
  if (!ctx.hasPermission("analytics.view")) return denied("analytics.view", "Delivery performance");
  if (!ctx.business) return noBusiness();
  const range = presetRange(cmd.range, ctx.business.timezone || undefined, ctx.now);
  const o = await analyticsService.overview(range);
  const period = PERIOD_LABEL[cmd.range];
  const links = [{ label: "Open analytics", href: "/analytics" }];
  if (!o.has_data) {
    return {
      kind: "empty",
      title: `No message activity ${period}`,
      summary: `${ctx.business.name} has no recorded messages ${period}.`,
      links,
    };
  }
  const total = o.totals.inbound + o.totals.outbound;
  const notes: string[] = [];
  if (o.rates.delivery_rate === null)
    notes.push("Delivery and read rates need at least one outbound message.");
  if (o.freshness.pending_buckets > 0) {
    notes.push("The most recent activity is still being aggregated, so these numbers can still grow.");
  }
  return {
    kind: "answer",
    title: `Delivery performance ${period}`,
    summary: `${ctx.business.name}: ${count(total)} messages (${count(o.totals.outbound)} sent, ${count(o.totals.inbound)} received).`,
    stats: [
      { label: "Delivery rate", value: percent(o.rates.delivery_rate) },
      { label: "Read rate", value: percent(o.rates.read_rate) },
      { label: "Failed", value: count(o.totals.failed), tone: o.totals.failed > 0 ? "danger" : "neutral" },
      { label: "In flight", value: count(o.totals.in_flight) },
      {
        label: "Cost",
        value: o.cost.by_currency.length ? money(o.cost.by_currency) : "—",
        hint: o.cost.state === "pending_rate" ? "Awaiting a rate card" : undefined,
      },
    ],
    notes,
    links,
  };
}

async function failedMessages(cmd: ParsedCommand, ctx: CommandContext): Promise<CommandResult> {
  if (!ctx.hasPermission("analytics.view")) return denied("analytics.view", "Failure details");
  if (!ctx.business) return noBusiness();
  const range = presetRange(cmd.range, ctx.business.timezone || undefined, ctx.now);
  const f = await analyticsService.failures(range);
  const period = PERIOD_LABEL[cmd.range];
  if (f.total === 0) {
    return {
      kind: "empty",
      title: `No failed messages ${period}`,
      summary: `${ctx.business.name} had no message failures ${period}.`,
      links: [{ label: "Open analytics", href: "/analytics" }],
    };
  }
  const retryable = f.groups.filter((g) => g.retryable).reduce((n, g) => n + g.count, 0);
  const permanent = f.total - retryable;
  const rows: ResultRow[] = f.groups.map((g) => ({
    id: g.category,
    title: g.label,
    subtitle: g.retryable
      ? "Usually temporary. Sending again can succeed."
      : "Won't succeed if sent again unchanged. Fix the cause first.",
    meta: g.reasons?.[0]?.message ?? g.codes?.[0]?.code,
    badge: { text: `${count(g.count)}`, tone: g.retryable ? "warning" : "danger" },
  }));
  const notes: string[] = [];
  if (retryable > 0) {
    notes.push(
      "For campaign messages, open the campaign and use “Retry failed”. It only re-sends recipients whose failure is retryable.",
    );
  }
  if (permanent > 0) {
    notes.push(
      "For failures that can't be retried, read the reason on each row (for example template status, number connection or opt-in) and correct that before sending again.",
    );
  }
  const campaignIds = new Set(f.recent.map((r) => r.campaign_id).filter(Boolean));
  return {
    kind: "answer",
    title: `Failed messages ${period}`,
    summary: `${count(f.total)} failed: ${count(retryable)} retryable, ${count(permanent)} not retryable.`,
    stats: [
      { label: "Failed", value: count(f.total), tone: "danger" },
      { label: "Retryable", value: count(retryable) },
      { label: "Not retryable", value: count(permanent) },
    ],
    rows: [
      ...rows,
      ...f.recent.slice(0, 5).map((r) => ({
        id: r.message_id,
        title: r.contact || "Unknown contact",
        subtitle: `${r.error_code}${r.error_message ? `: ${r.error_message}` : ""}`,
        meta: shortTime(r.occurred_at, ctx.now),
      })),
    ],
    notes,
    links: [
      { label: "Open analytics", href: "/analytics" },
      ...(campaignIds.size > 0 ? [{ label: "Open campaigns", href: "/campaigns" }] : []),
    ],
  };
}

const COMPARE_WORDS = /\b(compare|comparison|across|between|each|every|all|versus|vs|per)\b/;

async function costs(cmd: ParsedCommand, ctx: CommandContext): Promise<CommandResult> {
  if (!ctx.hasPermission("analytics.view")) return denied("analytics.view", "Cost reporting");
  const wantsComparison = COMPARE_WORDS.test(cmd.text.toLowerCase());
  const pool = wantsComparison || !ctx.business ? ctx.businesses : [ctx.business];
  const targets = pool.filter((b) => b.status === "active").slice(0, 10);
  if (targets.length === 0) return noBusiness();
  const tz = (b: { timezone: string }) => b.timezone || undefined;
  const settled = await Promise.allSettled(
    targets.map((b) =>
      analyticsService.overview(presetRange(cmd.range, tz(b), ctx.now), { businessId: b.id }),
    ),
  );
  const period = PERIOD_LABEL[cmd.range];
  const rows: ResultRow[] = targets.map((b, i) => {
    const r = settled[i];
    if (r.status === "rejected") {
      return {
        id: b.id,
        title: b.name,
        subtitle: "Couldn't be read",
        meta: r.reason instanceof ApiError ? r.reason.message : "Unexpected error",
        badge: { text: "unavailable", tone: "warning" as Tone },
      };
    }
    const o = r.value;
    return {
      id: b.id,
      title: b.name,
      subtitle: `${count(o.totals.inbound + o.totals.outbound)} messages · ${count(o.totals.outbound)} sent`,
      meta: o.cost.by_currency.length ? money(o.cost.by_currency) : "—",
      badge:
        o.cost.state === "pending_rate"
          ? { text: "awaiting rate card", tone: "warning" as Tone }
          : o.cost.state === "none"
            ? { text: "no billable messages", tone: "neutral" as Tone }
            : undefined,
    };
  });
  const notes = ["Amounts in different currencies are shown separately and never added together."];
  if (wantsComparison && ctx.businesses.length <= 1) {
    notes.push("You only have access to one business, so there is nothing to compare it with.");
  }
  if (pool.length > targets.length) notes.push("Showing the first 10 active businesses.");
  return {
    kind: "answer",
    title: targets.length > 1 ? `Messaging cost by business, ${period}` : `Messaging cost ${period}`,
    summary: `Provider usage cost from recorded message pricing, ${period}.`,
    rows,
    notes,
    links: [{ label: "Open analytics", href: "/analytics" }],
  };
}

async function campaignsRunning(_cmd: ParsedCommand, ctx: CommandContext): Promise<CommandResult> {
  if (!ctx.hasPermission("campaigns.view")) return denied("campaigns.view", "Campaigns");
  if (!ctx.business) return noBusiness();
  const page = await campaignService.list();
  const inProgress = page.results.filter((c) => c.status === "running" || c.status === "queued");
  const paused = page.results.filter((c) => c.status === "paused").length;
  const scheduled = page.results.filter((c) => c.status === "scheduled").length;
  const notes: string[] = [];
  if (page.next) notes.push("Looked at the 25 most recent campaigns.");
  if (paused > 0) notes.push(`${paused} paused campaign${paused === 1 ? "" : "s"} not shown here.`);
  if (scheduled > 0) notes.push(`${scheduled} scheduled to start later.`);
  const links = [{ label: "Open campaigns", href: "/campaigns" }];
  if (inProgress.length === 0) {
    return {
      kind: "empty",
      title: "No campaigns are running",
      summary: `${ctx.business.name} has no running or queued campaigns right now.`,
      notes,
      links,
    };
  }
  return {
    kind: "answer",
    title: `${inProgress.length} campaign${inProgress.length === 1 ? "" : "s"} in progress`,
    rows: inProgress.map(campaignRow),
    notes,
    links,
  };
}

async function campaignsOverview(_cmd: ParsedCommand, ctx: CommandContext): Promise<CommandResult> {
  if (!ctx.hasPermission("campaigns.view")) return denied("campaigns.view", "Campaigns");
  if (!ctx.business) return noBusiness();
  const page = await campaignService.list();
  const links = [{ label: "Open campaigns", href: "/campaigns" }];
  if (page.results.length === 0) {
    return {
      kind: "empty",
      title: "No campaigns yet",
      summary: `${ctx.business.name} has no campaigns.`,
      links,
    };
  }
  const by = (s: CampaignStatus) => page.results.filter((c) => c.status === s).length;
  return {
    kind: "answer",
    title: "Campaigns",
    stats: [
      { label: "Running", value: String(by("running") + by("queued")) },
      { label: "Scheduled", value: String(by("scheduled")) },
      { label: "Draft", value: String(by("draft")) },
      { label: "Paused", value: String(by("paused")), tone: by("paused") ? "warning" : "neutral" },
    ],
    rows: page.results.slice(0, 8).map(campaignRow),
    notes: page.next ? ["Showing the 25 most recent campaigns."] : [],
    links,
  };
}

async function templatesAttention(_cmd: ParsedCommand, ctx: CommandContext): Promise<CommandResult> {
  if (!ctx.hasPermission("templates.view")) return denied("templates.view", "Templates");
  if (!ctx.business) return noBusiness();
  const page = await templateService.list();
  const rows: ResultRow[] = [];
  for (const t of page.results) {
    const status = t.status.toLowerCase();
    const quality = t.quality_score.toLowerCase();
    let reason: string | null = null;
    let tone: Tone = "warning";
    if (t.missing_from_provider) reason = "No longer found at Meta. It may have been deleted there.";
    else if (status === "rejected") {
      reason = t.rejected_reason || "Rejected by Meta.";
      tone = "danger";
    } else if (status === "paused" || status === "disabled") {
      reason = `Meta has ${status} this template.`;
      tone = "danger";
    } else if (status === "pending" || status === "in_appeal") {
      reason = "Waiting for Meta's review. It can't be sent yet.";
      tone = "neutral";
    } else if (quality === "red") {
      reason = "Quality rating is red. Meta may pause it if recipients keep reporting it.";
      tone = "danger";
    }
    if (reason) {
      rows.push({
        id: t.id,
        title: `${t.name} (${t.language})`,
        subtitle: reason,
        badge: { text: t.missing_from_provider ? "missing" : status, tone },
        href: "/templates",
      });
    }
  }
  const links = [{ label: "Open templates", href: "/templates" }];
  if (rows.length === 0) {
    return {
      kind: "empty",
      title: "No templates need attention",
      summary: `All ${page.results.length} templates for ${ctx.business.name} are approved with no quality warnings.`,
      links,
    };
  }
  return {
    kind: "answer",
    title: `${rows.length} template${rows.length === 1 ? "" : "s"} need attention`,
    summary: `Of ${page.results.length} templates for ${ctx.business.name}.`,
    rows,
    notes: page.next ? ["Looked at the first 100 templates."] : [],
    links,
  };
}

async function unread(_cmd: ParsedCommand, ctx: CommandContext): Promise<CommandResult> {
  if (!ctx.hasPermission("conversations.view")) return denied("conversations.view", "Conversations");
  if (!ctx.business) return noBusiness();
  const page = await inboxService.conversations({ unread: "1" });
  const links = [{ label: "Open inbox", href: "/inbox" }];
  if (page.results.length === 0) {
    return { kind: "empty", title: "Nothing unread", summary: "Every conversation has been read.", links };
  }
  return {
    kind: "answer",
    title: `${page.results.length}${page.next ? "+" : ""} unread conversation${page.results.length === 1 ? "" : "s"}`,
    rows: page.results.slice(0, 8).map((c) => ({
      id: c.id,
      title: c.contact_name || c.contact_phone_number,
      subtitle: c.last_message_preview,
      meta: c.last_message_at ? shortTime(c.last_message_at, ctx.now) : undefined,
      badge: { text: `${c.unread_count} unread`, tone: "warning" as Tone },
      href: "/inbox",
    })),
    links,
  };
}

async function search(cmd: ParsedCommand, ctx: CommandContext): Promise<CommandResult> {
  if (!ctx.business) return noBusiness();
  const q = cmd.subject;
  const jobs: { label: string; run: () => Promise<ResultRow[]> }[] = [];
  if (ctx.hasPermission("contacts.view")) {
    jobs.push({
      label: "Contacts",
      run: async () =>
        (await contactService.list({ q })).results.slice(0, 5).map((c) => ({
          id: `contact-${c.id}`,
          title: c.display_name || c.profile_name || c.phone_number,
          subtitle: `Contact · ${c.phone_number}`,
          href: "/contacts",
        })),
    });
  }
  if (ctx.hasPermission("conversations.view")) {
    jobs.push({
      label: "Conversations",
      run: async () =>
        (await inboxService.conversations({ q })).results.slice(0, 5).map((c) => ({
          id: `conv-${c.id}`,
          title: c.contact_name || c.contact_phone_number,
          subtitle: `Conversation · ${c.last_message_preview}`,
          href: "/inbox",
        })),
    });
  }
  if (ctx.hasPermission("templates.view")) {
    jobs.push({
      label: "Templates",
      run: async () =>
        (await templateService.list({ q })).results.slice(0, 5).map((t) => ({
          id: `tpl-${t.id}`,
          title: t.name,
          subtitle: `Template · ${t.language} · ${t.status.toLowerCase()}`,
          href: "/templates",
        })),
    });
  }
  if (ctx.hasPermission("campaigns.view")) {
    jobs.push({
      label: "Campaigns",
      run: async () =>
        (await campaignService.list({ q })).results.slice(0, 5).map((c) => ({
          id: `camp-${c.id}`,
          title: c.name,
          subtitle: `Campaign · ${c.status}`,
          href: "/campaigns",
        })),
    });
  }
  if (jobs.length === 0) return denied("contacts.view", "Searching");
  const settled = await Promise.allSettled(jobs.map((j) => j.run()));
  const rows = settled.flatMap((s) => (s.status === "fulfilled" ? s.value : []));
  const failed = settled.filter((s) => s.status === "rejected").length;
  if (failed === jobs.length) {
    const first = settled.find((s): s is PromiseRejectedResult => s.status === "rejected");
    throw first?.reason ?? new Error("search failed");
  }
  const notes: string[] = [];
  if (cmd.fallbackSearch) notes.push(`I read this as a search for “${q}”. Nothing was changed.`);
  if (failed > 0) notes.push(`${failed} of ${jobs.length} sources couldn't be searched.`);
  if (rows.length === 0) {
    return {
      kind: "empty",
      title: `No matches for “${q}”`,
      summary: `Searched ${jobs.map((j) => j.label.toLowerCase()).join(", ")} in ${ctx.business.name}.`,
      notes,
    };
  }
  return { kind: "answer", title: `Results for “${q}”`, rows, notes };
}

// ------------------------------------------------------- consequential writes

function pickCampaign(matches: Campaign[], subject: string): Campaign | "ambiguous" | null {
  if (matches.length === 0) return null;
  const exact = matches.filter((c) => c.name.toLowerCase() === subject.toLowerCase());
  if (exact.length === 1) return exact[0];
  if (matches.length === 1) return matches[0];
  return "ambiguous";
}

async function launchCampaign(cmd: ParsedCommand, ctx: CommandContext): Promise<CommandResult> {
  if (!ctx.hasPermission("campaigns.launch")) return denied("campaigns.launch", "Launching a campaign");
  if (!ctx.business) return noBusiness();
  const page = await campaignService.list(cmd.subject ? { q: cmd.subject } : { status: "draft" });
  const candidates = cmd.subject ? page.results : page.results.filter((c) => c.status === "draft");
  const chosen = pickCampaign(candidates, cmd.subject);
  if (chosen === null || chosen === "ambiguous") {
    const none = chosen === null;
    return {
      kind: "empty",
      title: none
        ? cmd.subject
          ? `No campaign matches “${cmd.subject}”`
          : "No draft campaigns to launch"
        : "Which campaign do you mean?",
      summary: none
        ? "Nothing was launched."
        : "More than one campaign matches. Repeat the command with the full name. Nothing was launched.",
      rows: none ? undefined : candidates.slice(0, 6).map(campaignRow),
      links: [{ label: "Open campaigns", href: "/campaigns" }],
    };
  }
  const preview = await campaignService.preview(chosen.id);
  const blockers = preview.checks.filter((c) => !c.ok && c.severity === "error");
  const warnings = preview.checks.filter((c) => !c.ok && c.severity === "warning");
  const s = preview.summary;
  const details = [
    `Template: ${chosen.template_name} (${chosen.template_language})`,
    `Sending number: ${chosen.phone_number_display}`,
    s.recipients !== undefined ? `Recipients: ${count(s.recipients)}` : "Recipients: not calculated",
    ...(s.rate_per_minute ? [`Rate: up to ${count(s.rate_per_minute)} messages per minute`] : []),
    ...(s.estimated_duration_minutes
      ? [`Estimated duration: about ${s.estimated_duration_minutes} minutes`]
      : []),
    ...warnings.map((w) => `Warning: ${w.message}`),
  ];
  const blockedReason =
    chosen.status !== "draft"
      ? `“${chosen.name}” is ${chosen.status}, so it can't be launched.`
      : !preview.launchable || blockers.length > 0
        ? blockers.map((b) => b.message).join(" ") || "The launch checks did not pass."
        : undefined;
  return {
    kind: "confirm",
    title: `Launch “${chosen.name}”?`,
    summary: "This sends real WhatsApp messages to the recipients below once it starts.",
    confirmation: {
      label: "Launch campaign",
      details,
      blockedReason,
      run: async () => {
        const result = await campaignService.launch(chosen.id);
        return {
          kind: "done",
          title: `“${chosen.name}” launched`,
          summary: `Status is now ${result.campaign.status}. You can watch progress in Campaigns.`,
          links: [{ label: "Open campaigns", href: "/campaigns" }],
        };
      },
    },
  };
}

async function pauseCampaign(cmd: ParsedCommand, ctx: CommandContext): Promise<CommandResult> {
  if (!ctx.hasPermission("campaigns.launch")) return denied("campaigns.launch", "Pausing a campaign");
  if (!ctx.business) return noBusiness();
  const page = await campaignService.list(cmd.subject ? { q: cmd.subject } : {});
  const candidates = page.results.filter((c) => c.status === "running" || c.status === "queued");
  const chosen = pickCampaign(candidates, cmd.subject);
  if (chosen === null || chosen === "ambiguous") {
    const none = chosen === null;
    return {
      kind: "empty",
      title: none ? "No running campaign to pause" : "Which campaign do you mean?",
      summary: none
        ? cmd.subject
          ? `No running campaign matches “${cmd.subject}”. Nothing was paused.`
          : "Nothing is running. Nothing was paused."
        : "More than one is running. Repeat the command with the campaign name. Nothing was paused.",
      rows: none ? undefined : candidates.slice(0, 6).map(campaignRow),
      links: [{ label: "Open campaigns", href: "/campaigns" }],
    };
  }
  return {
    kind: "confirm",
    title: `Pause “${chosen.name}”?`,
    summary: "The campaign stops sending to remaining recipients until you resume it.",
    confirmation: {
      label: "Pause campaign",
      details: [
        `Status now: ${chosen.status}`,
        `Template: ${chosen.template_name} (${chosen.template_language})`,
        "You can resume it later from Campaigns.",
      ],
      run: async () => {
        const result = await campaignService.pause(chosen.id);
        return {
          kind: "done",
          title: `“${chosen.name}” paused`,
          summary: `Status is now ${result.campaign.status}.`,
          links: [{ label: "Open campaigns", href: "/campaigns" }],
        };
      },
    },
  };
}

// ------------------------------------------------------------------ dispatch

export async function runCommand(cmd: ParsedCommand, ctx: CommandContext): Promise<CommandResult> {
  switch (cmd.intent) {
    case "performance":
      return performance(cmd, ctx);
    case "failed_messages":
      return failedMessages(cmd, ctx);
    case "costs":
      return costs(cmd, ctx);
    case "campaigns_running":
      return campaignsRunning(cmd, ctx);
    case "campaigns_overview":
      return campaignsOverview(cmd, ctx);
    case "templates_attention":
      return templatesAttention(cmd, ctx);
    case "unread":
      return unread(cmd, ctx);
    case "search":
      return search(cmd, ctx);
    case "launch_campaign":
      return launchCampaign(cmd, ctx);
    case "pause_campaign":
      return pauseCampaign(cmd, ctx);
    case "navigate":
      return {
        kind: "done",
        title: "Opening…",
        links: cmd.href ? [{ label: "Go there", href: cmd.href }] : undefined,
        navigateTo: cmd.href,
      };
    case "unsupported":
      return {
        kind: "unsupported",
        title: "I can't do that from here",
        summary: cmd.reason,
        notes: ["Things I can do:", ...SUPPORTED_EXAMPLES],
      };
  }
}
