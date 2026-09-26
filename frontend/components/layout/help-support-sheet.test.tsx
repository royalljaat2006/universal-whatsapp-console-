import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/settings" }));
vi.mock("@/stores/session-context", () => ({
  useSession: () => ({
    user: { email: "ada@example.com" },
    active: { organization: { name: "Acme Co" } },
    business: { name: "Kiosk" },
  }),
}));

describe("HelpSupportSheet", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("shows a not-configured message when no support email is set", async () => {
    vi.stubEnv("NEXT_PUBLIC_SUPPORT_EMAIL", "");
    const { HelpSupportSheet } = await import("./help-support-sheet");
    render(<HelpSupportSheet open onOpenChange={vi.fn()} />);
    expect(await screen.findByText(/configured yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Contact support" })).not.toBeInTheDocument();
  });

  it("builds a contextual mailto link when a support email is configured", async () => {
    vi.stubEnv("NEXT_PUBLIC_SUPPORT_EMAIL", "support@example.com");
    const { HelpSupportSheet } = await import("./help-support-sheet");
    render(<HelpSupportSheet open onOpenChange={vi.fn()} />);
    const link = await screen.findByRole("button", { name: "Contact support" });
    const href = decodeURIComponent(link.getAttribute("href") ?? "");
    expect(href).toContain("mailto:support@example.com");
    expect(href).toContain("Organization: Acme Co");
    expect(href).toContain("Business: Kiosk");
    expect(href).toContain("Page: /settings");
    expect(href).toContain("Signed in as: ada@example.com");
  });
});
