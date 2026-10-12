import { describe, expect, it } from "vitest";

import { parseCommand } from "./parse";

describe("parseCommand", () => {
  it.each([
    ["Show today's message delivery performance", "performance"],
    ["How is delivery going?", "performance"],
    ["Find messages that failed and explain the available recovery options.", "failed_messages"],
    ["any errors this week", "failed_messages"],
    ["Show campaigns currently running", "campaigns_running"],
    ["what campaigns are active", "campaigns_running"],
    ["Show campaigns", "campaigns_overview"],
    ["Find templates that need attention", "templates_attention"],
    ["which templates were rejected", "templates_attention"],
    ["Compare messaging costs for my selected businesses", "costs"],
    ["how much did we spend yesterday", "costs"],
    ["Show unread conversations", "unread"],
    ["Is everything healthy with my WhatsApp numbers?", "health"],
    ["any problems with my numbers", "health"],
    ["check my number quality rating", "health"],
    ["is whatsapp working", "health"],
    ["connection status", "health"],
    ["find templates with issues", "templates_attention"],
    ["any failed messages", "failed_messages"],
    ["Is my WhatsApp set up and ready?", "setup"],
    ["help me get started", "setup"],
    ["connect my whatsapp", "setup"],
    ["onboarding status", "setup"],
    ["I want to send a test message", "test_message"],
    ["send a test message to my phone", "test_message"],
    ["test message", "test_message"],
    ["Which messages failed and can I retry them?", "failed_messages"],
    ["why did my message fail", "failed_messages"],
    ["retry all failed messages", "failed_messages"],
    ["how is delivery going", "performance"],
  ])("%s -> %s", (input, intent) => {
    expect(parseCommand(input).intent).toBe(intent);
  });

  it("reads the period when one is given and says when it defaulted", () => {
    expect(parseCommand("failed messages yesterday")).toMatchObject({
      range: "yesterday",
      rangeDefaulted: false,
    });
    expect(parseCommand("failed messages last 7 days")).toMatchObject({ range: "7d", rangeDefaulted: false });
    expect(parseCommand("costs this month")).toMatchObject({ range: "30d", rangeDefaulted: false });
    expect(parseCommand("delivery performance")).toMatchObject({ range: "today", rangeDefaulted: true });
  });

  it("recognises consequential actions and extracts only the campaign name", () => {
    expect(parseCommand("Launch campaign Spring Sale")).toMatchObject({
      intent: "launch_campaign",
      subject: "spring sale",
    });
    expect(parseCommand("please pause the Diwali campaign")).toMatchObject({
      intent: "pause_campaign",
      subject: "diwali",
    });
    expect(parseCommand("launch campaign").subject).toBe("");
  });

  it("never treats a launch/pause request as a read", () => {
    for (const text of ["go live with campaign X", "start campaign X", "launch the X campaign now"]) {
      expect(parseCommand(text).intent).toBe("launch_campaign");
    }
  });

  it("navigates only for explicit open-this-page requests", () => {
    expect(parseCommand("open templates")).toMatchObject({ intent: "navigate", href: "/templates" });
    expect(parseCommand("go to the inbox")).toMatchObject({ intent: "navigate", href: "/inbox" });
    expect(parseCommand("analytics")).toMatchObject({ intent: "navigate", href: "/analytics" });
    expect(parseCommand("show campaigns").intent).toBe("campaigns_overview");
  });

  it.each([
    "delete all contacts",
    "send a message to Asha",
    "invite bob@example.com",
    "export my contacts",
    "reply to the last customer",
    "cancel the campaign",
  ])("refuses unsupported action: %s", (input) => {
    const parsed = parseCommand(input);
    expect(parsed.intent).toBe("unsupported");
    expect(parsed.reason).toBeTruthy();
  });

  it("treats a plain name as a labelled search, not a guess at an action", () => {
    expect(parseCommand("Asha Sharma")).toMatchObject({
      intent: "search",
      subject: "asha sharma",
      fallbackSearch: true,
    });
    expect(parseCommand("find 919876543210")).toMatchObject({ intent: "search", subject: "919876543210" });
  });

  it("writing a template goes to the Template Studio; changing or removing one is still refused", () => {
    for (const input of [
      "create a new template",
      "I want to write a template for order updates",
      "draft a template",
      "new template",
      "open the template studio",
    ]) {
      expect(parseCommand(input).intent).toBe("template_studio");
    }
    for (const input of ["delete the template order_shipped", "edit template welcome", "approve my template"]) {
      expect(parseCommand(input).intent).not.toBe("template_studio");
    }
  });

  it("a test-message request is never parsed as a send, and other sends are still refused", () => {
    expect(parseCommand("send a test message").intent).toBe("test_message");
    expect(parseCommand("send a message to Asha").intent).toBe("unsupported");
    expect(parseCommand("connect all numbers").intent).toBe("unsupported");
    expect(parseCommand("disconnect my whatsapp").intent).toBe("unsupported");
  });

  it("rejects empty or too-short input", () => {
    expect(parseCommand("").intent).toBe("unsupported");
    expect(parseCommand(" x ").intent).toBe("unsupported");
    expect(parseCommand("show me the").intent).toBe("unsupported");
  });
});
