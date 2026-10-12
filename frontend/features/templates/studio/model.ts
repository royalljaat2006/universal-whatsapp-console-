import type { Tone } from "@/components/ui/status-badge";
import type { DraftStatus, StudioButton, StudioContent, TemplateState } from "@/types/api";

export const STATE_LABEL: Record<TemplateState, string> = {
  draft: "Draft",
  submitted: "Sent to Meta",
  pending: "In review at Meta",
  approved: "Approved",
  rejected: "Rejected",
  paused: "Paused",
  disabled: "Disabled",
  unknown: "Status unclear",
};

export const STATE_TONE: Record<TemplateState, Tone> = {
  draft: "neutral",
  submitted: "info",
  pending: "info",
  approved: "success",
  rejected: "danger",
  paused: "warning",
  disabled: "danger",
  unknown: "warning",
};

export const DRAFT_STATUS_LABEL: Record<DraftStatus, string> = {
  draft: "Draft (not sent to Meta)",
  submitting: "Sending to Meta…",
  submitted: "Sent to Meta",
  unconfirmed: "Not sure if Meta received it",
};

export function emptyContent(): StudioContent {
  return { header_text: "", body: "", examples: {}, footer: "", buttons: [] };
}

/** Variable keys written in a text, in order of first appearance (a hint for the example inputs; the server decides what is valid). */
export function variableKeys(text: string): string[] {
  const keys: string[] = [];
  for (const match of text.matchAll(/\{\{\s*([^{}]*?)\s*\}\}/g)) {
    if (!keys.includes(match[1])) keys.push(match[1]);
  }
  return keys;
}

/** The next positional variable number for a body, so "Add a variable" never produces a gap. */
export function nextVariableNumber(body: string): number {
  const numbers = variableKeys(body)
    .filter((k) => /^\d+$/.test(k))
    .map(Number);
  return numbers.length ? Math.max(...numbers) + 1 : 1;
}

export function newButton(type: StudioButton["type"]): StudioButton {
  if (type === "URL") return { type, text: "", url: "https://", url_example: "" };
  if (type === "PHONE_NUMBER") return { type, text: "", phone_number: "" };
  return { type, text: "" };
}

/** Drop example values for variables that no longer appear, so stale examples are never saved. */
export function pruneExamples(content: StudioContent): StudioContent {
  const used = new Set([...variableKeys(content.body), ...variableKeys(content.header_text)]);
  return {
    ...content,
    examples: Object.fromEntries(Object.entries(content.examples).filter(([k]) => used.has(k))),
  };
}

export function fieldIssues<T extends { field: string }>(issues: T[], prefix: string): T[] {
  return issues.filter((i) => i.field === prefix || i.field.startsWith(`${prefix}.`));
}
