import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";

import { CommandHome } from "./command-home";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const business = { id: "b1", name: "Kiosk", slug: "kiosk", status: "active", timezone: "UTC" };
const permissions = new Set<string>();
const session = {
  business,
  businesses: [business],
  user: { full_name: "Asha Rao" },
  hasPermission: (code: string) => permissions.has(code),
};
vi.mock("@/stores/session-context", () => ({ useSession: () => session }));
vi.mock("../overview/onboarding-checklist", () => ({ OnboardingChecklist: () => null }));
vi.mock("../overview/overview-view", () => ({ OverviewView: () => <div>overview body</div> }));

const overview = vi.fn();
const failures = vi.fn();
const campaignList = vi.fn();
const campaignPreview = vi.fn();
const campaignLaunch = vi.fn();
const campaignPause = vi.fn();
vi.mock("@/services/analytics", () => ({
  analyticsService: {
    overview: (...a: unknown[]) => overview(...a),
    failures: (...a: unknown[]) => failures(...a),
  },
}));
vi.mock("@/services/campaigns", () => ({
  campaignService: {
    list: (...a: unknown[]) => campaignList(...a),
    preview: (...a: unknown[]) => campaignPreview(...a),
    launch: (...a: unknown[]) => campaignLaunch(...a),
    pause: (...a: unknown[]) => campaignPause(...a),
  },
}));
vi.mock("@/services/templates", () => ({
  templateService: { list: vi.fn().mockResolvedValue({ results: [], next: null }) },
}));
vi.mock("@/services/inbox", () => ({
  inboxService: { conversations: vi.fn().mockResolvedValue({ results: [], next: null }) },
}));
vi.mock("@/services/whatsapp", () => ({
  whatsappService: { numbers: vi.fn().mockResolvedValue({ results: [], next: null }) },
}));
vi.mock("@/services/contacts", () => ({
  contactService: { list: vi.fn().mockResolvedValue({ results: [], next: null }) },
}));

const OVERVIEW = {
  has_data: true,
  totals: { inbound: 4, outbound: 10, failed: 2, in_flight: 1 },
  rates: { delivery_rate: 0.9, read_rate: 0.5 },
  cost: { state: "priced", by_currency: [{ currency: "USD", amount: "1.5" }] },
  freshness: { computed_at: null, pending_buckets: 0 },
};

const CAMPAIGN = {
  id: "c1",
  name: "Spring Sale",
  status: "draft",
  template_name: "promo",
  template_language: "en",
  phone_number_display: "+1 555 0100",
  audience_count: 120,
};

function ask(text: string) {
  fireEvent.change(screen.getByLabelText("Type a command or a question"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /Run/ }));
}

beforeEach(() => {
  vi.clearAllMocks();
  permissions.clear();
  [
    "analytics.view",
    "campaigns.view",
    "campaigns.launch",
    "templates.view",
    "conversations.view",
    "contacts.view",
    "numbers.view",
  ].forEach((p) => permissions.add(p));
  overview.mockResolvedValue(OVERVIEW);
  campaignList.mockResolvedValue({ results: [CAMPAIGN], next: null });
});

describe("CommandHome", () => {
  it("answers a delivery question from the real analytics call", async () => {
    render(<CommandHome />);
    ask("Show today's message delivery performance");
    expect(await screen.findByRole("heading", { name: "Delivery performance today" })).toBeInTheDocument();
    expect(screen.getByText("90.0%")).toBeInTheDocument();
    expect(overview).toHaveBeenCalled();
  });

  it("refuses an unsupported request honestly and calls nothing", async () => {
    render(<CommandHome />);
    ask("delete all my contacts");
    expect(await screen.findByRole("heading", { name: "I can't do that from here" })).toBeInTheDocument();
    expect(screen.getByText(/Nothing was changed/)).toBeInTheDocument();
    expect(campaignLaunch).not.toHaveBeenCalled();
    expect(failures).not.toHaveBeenCalled();
  });

  it("explains missing permission instead of calling the API", async () => {
    permissions.delete("analytics.view");
    render(<CommandHome />);
    ask("Find messages that failed");
    expect(await screen.findByRole("heading", { name: "You don't have access to that" })).toBeInTheDocument();
    expect(failures).not.toHaveBeenCalled();
  });

  it("shows an honest empty state, not zeros, when there is no data", async () => {
    overview.mockResolvedValue({ ...OVERVIEW, has_data: false });
    render(<CommandHome />);
    ask("delivery performance");
    expect(await screen.findByRole("heading", { name: "No message activity today" })).toBeInTheDocument();
    expect(screen.queryByText("0.0%")).not.toBeInTheDocument();
  });

  it("surfaces a server error with a retry that re-runs the same command", async () => {
    failures.mockRejectedValueOnce(new ApiError(403, "PERMISSION_DENIED", "You do not have permission."));
    render(<CommandHome />);
    ask("failed messages");
    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have permission.");
    failures.mockResolvedValueOnce({ total: 0, groups: [], recent: [] });
    fireEvent.click(screen.getByRole("button", { name: /retry|try again/i }));
    expect(await screen.findByRole("heading", { name: "No failed messages today" })).toBeInTheDocument();
    expect(failures).toHaveBeenCalledTimes(2);
  });

  describe("launching a campaign", () => {
    beforeEach(() => {
      campaignPreview.mockResolvedValue({
        launchable: true,
        checks: [
          {
            code: "opt_in",
            ok: false,
            severity: "warning",
            message: "12 recipients have no recorded opt-in.",
          },
        ],
        summary: { recipients: 120, rate_per_minute: 60, estimated_duration_minutes: 2 },
      });
      campaignLaunch.mockResolvedValue({ campaign: { ...CAMPAIGN, status: "running" } });
    });

    it("shows the server's preview and does NOT launch until confirmed", async () => {
      render(<CommandHome />);
      ask("launch campaign Spring Sale");
      expect(await screen.findByRole("heading", { name: "Launch “Spring Sale”?" })).toBeInTheDocument();
      expect(screen.getByText("Recipients: 120")).toBeInTheDocument();
      expect(screen.getByText(/12 recipients have no recorded opt-in/)).toBeInTheDocument();
      expect(campaignLaunch).not.toHaveBeenCalled();
    });

    it("cancelling never launches", async () => {
      render(<CommandHome />);
      ask("launch campaign Spring Sale");
      await screen.findByRole("heading", { name: "Launch “Spring Sale”?" });
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(campaignLaunch).not.toHaveBeenCalled();
      expect(screen.queryByRole("heading", { name: "Launch “Spring Sale”?" })).not.toBeInTheDocument();
    });

    it("launches exactly once on confirm and reports the real result", async () => {
      render(<CommandHome />);
      ask("launch campaign Spring Sale");
      fireEvent.click(await screen.findByRole("button", { name: "Launch campaign" }));
      expect(await screen.findByRole("heading", { name: "“Spring Sale” launched" })).toBeInTheDocument();
      expect(campaignLaunch).toHaveBeenCalledTimes(1);
      expect(campaignLaunch).toHaveBeenCalledWith("c1");
    });

    it("disables confirmation when the server says the campaign can't launch", async () => {
      campaignPreview.mockResolvedValue({
        launchable: false,
        checks: [
          { code: "template", ok: false, severity: "error", message: "The template is not approved." },
        ],
        summary: { recipients: 120 },
      });
      render(<CommandHome />);
      ask("launch campaign Spring Sale");
      const button = await screen.findByRole("button", { name: "Launch campaign" });
      expect(button).toBeDisabled();
      expect(screen.getByRole("alert")).toHaveTextContent("The template is not approved.");
    });

    it("keeps the preview and shows the error when the launch call fails", async () => {
      campaignLaunch.mockRejectedValueOnce(new ApiError(409, "CONFLICT", "Campaign already launched."));
      render(<CommandHome />);
      ask("launch campaign Spring Sale");
      fireEvent.click(await screen.findByRole("button", { name: "Launch campaign" }));
      await waitFor(() => expect(screen.getByText("Campaign already launched.")).toBeInTheDocument());
      expect(screen.getByRole("button", { name: "Launch campaign" })).toBeInTheDocument();
    });

    it("needs the launch permission before it even previews", async () => {
      permissions.delete("campaigns.launch");
      render(<CommandHome />);
      ask("launch campaign Spring Sale");
      expect(
        await screen.findByRole("heading", { name: "You don't have access to that" }),
      ).toBeInTheDocument();
      expect(campaignPreview).not.toHaveBeenCalled();
      expect(campaignLaunch).not.toHaveBeenCalled();
    });

    it("asks which campaign when several match, and launches none", async () => {
      campaignList.mockResolvedValue({
        results: [CAMPAIGN, { ...CAMPAIGN, id: "c2", name: "Spring Sale 2" }],
        next: null,
      });
      render(<CommandHome />);
      ask("launch campaign Spring");
      expect(await screen.findByRole("heading", { name: "Which campaign do you mean?" })).toBeInTheDocument();
      expect(campaignLaunch).not.toHaveBeenCalled();
    });
  });

  it("only lists campaigns the API says are in progress", async () => {
    campaignList.mockResolvedValue({
      results: [
        { ...CAMPAIGN, id: "r", name: "Live One", status: "running" },
        { ...CAMPAIGN, id: "d", name: "Draft One", status: "draft" },
      ],
      next: null,
    });
    render(<CommandHome />);
    ask("Show campaigns currently running");
    expect(await screen.findByText("Live One")).toBeInTheDocument();
    expect(screen.queryByText("Draft One")).not.toBeInTheDocument();
  });
});
