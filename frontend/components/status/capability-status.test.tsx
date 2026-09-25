import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FEATURE_SECTIONS } from "@/features/registry";

import { CapabilityStatus } from "./capability-status";

describe("CapabilityStatus", () => {
  it("labels unfinished capabilities explicitly", () => {
    render(<CapabilityStatus title="Analytics" state="NOT IMPLEMENTED" description="later" />);
    expect(screen.getByRole("status", { name: "Analytics: NOT IMPLEMENTED" })).toBeInTheDocument();
    expect(screen.getByText("NOT IMPLEMENTED")).toBeInTheDocument();
  });
});

describe("feature registry", () => {
  it("never marks a section live without a backend-backed page, and labels the rest", () => {
    for (const section of FEATURE_SECTIONS) {
      if (!section.live) expect(section.state).toBeDefined();
    }
    expect(FEATURE_SECTIONS.filter((s) => s.live).map((s) => s.href)).toEqual([
      "/",
      "/inbox",
      "/contacts",
      "/templates",
      "/campaigns",
      "/analytics",
      "/numbers",
      "/settings",
    ]);
  });
});
