import { describe, expect, it } from "vitest";

import { FEATURE_SECTIONS, labelFor, visibleSections } from "./registry";

const everything = () => true;
const only =
  (...codes: string[]) =>
  (code: string) =>
    codes.includes(code);

describe("visibleSections", () => {
  it("advanced shows every live section the person may use (navigation unchanged)", () => {
    expect(visibleSections("advanced", everything).map((s) => s.href)).toEqual(
      FEATURE_SECTIONS.filter((s) => s.live).map((s) => s.href),
    );
  });

  it("simple shows the essentials and hides analytics", () => {
    const hrefs = visibleSections("simple", everything).map((s) => s.href);
    expect(hrefs).toEqual(["/", "/setup", "/inbox", "/failed-messages", "/contacts", "/templates", "/campaigns", "/numbers", "/settings"]);
    expect(hrefs).not.toContain("/analytics");
  });

  it("simple is always a subset of advanced, for any permission set", () => {
    const permissionSets = [
      [],
      ["organization.view"],
      ["organization.view", "conversations.view"],
      FEATURE_SECTIONS.map((s) => s.permission),
      ["analytics.view"],
    ];
    for (const set of permissionSets) {
      const simple = visibleSections("simple", only(...set)).map((s) => s.href);
      const advanced = visibleSections("advanced", only(...set)).map((s) => s.href);
      expect(advanced).toEqual(expect.arrayContaining(simple));
    }
  });

  it("mode never grants anything: no permission means no entry in either mode", () => {
    for (const mode of ["simple", "advanced"] as const) {
      expect(visibleSections(mode, only("conversations.view")).map((s) => s.href)).toEqual(["/inbox", "/failed-messages"]);
      expect(visibleSections(mode, () => false)).toEqual([]);
    }
  });

  it("uses plainer wording only in simple mode", () => {
    const home = FEATURE_SECTIONS[0];
    expect(labelFor(home, "simple")).toBe("Home");
    expect(labelFor(home, "advanced")).toBe("Overview");
    const inbox = FEATURE_SECTIONS.find((s) => s.href === "/inbox")!;
    expect(labelFor(inbox, "simple")).toBe(labelFor(inbox, "advanced"));
  });
});
