import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";
import { UiModeProvider, resetModeForTests, writeStoredMode } from "@/stores/ui-mode";

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
const recoveryFailures = vi.fn();
const recoveryRetry = vi.fn();
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
vi.mock("@/services/recovery", () => ({
  recoveryService: {
    failures: (...a: unknown[]) => recoveryFailures(...a),
    retry: (...a: unknown[]) => recoveryRetry(...a),
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
  templateStudioService: { drafts: (...a: unknown[]) => studioDrafts(...a) },
}));
vi.mock("@/services/inbox", () => ({
  inboxService: { conversations: vi.fn().mockResolvedValue({ results: [], next: null }) },
}));
vi.mock("@/services/whatsapp", () => ({
  whatsappService: {
    numbers: vi.fn().mockResolvedValue({ results: [], next: null }),
    health: (...a: unknown[]) => healthFn(...a),
    setup: (...a: unknown[]) => setupFn(...a),
  },
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

const healthFn = vi.fn();
const setupFn = vi.fn();
const studioDrafts = vi.fn();

const check = (over: Record<string, unknown>) => ({
  code: "quality",
  state: "ok",
  title: "Quality is high",
  detail: "d",
  next_step: "",
  fix_in: "none",
  basis: "provider",
  action: "",
  ...over,
});

const HEALTHY = {
  generated_at: "2026-10-12T10:00:00Z",
  overall: "healthy",
  summary: "Everything Meta has reported looks healthy.",
  accounts: [],
  numbers: [
    {
      id: "n1",
      label: "Support line",
      display_phone_number: "+91 90000 00001",
      verified_name: "",
      business_id: "b1",
      business_name: "Kiosk",
      business_account_id: "a1",
      overall: "healthy",
      last_checked_at: "2026-10-12T09:59:00Z",
      checks: [check({})],
    },
  ],
  recent_alerts: [],
  notes: ["Statuses are what Meta last reported to this console, not a live check."],
};

const RED = {
  ...HEALTHY,
  overall: "problem",
  summary: "1 thing needs fixing.",
  numbers: [
    {
      ...HEALTHY.numbers[0],
      overall: "problem",
      checks: [
        check({
          state: "problem",
          title: "Quality is low",
          next_step: "Pause marketing sends and review who you message.",
          fix_in: "meta",
        }),
      ],
    },
  ],
};

function renderHome() {
  return render(
    <UiModeProvider>
      <CommandHome />
    </UiModeProvider>,
  );
}

function ask(text: string) {
  fireEvent.change(screen.getByLabelText("Type a command or a question"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /Run/ }));
}

beforeEach(() => {
  vi.clearAllMocks();
  resetModeForTests();
  window.localStorage.clear();
  permissions.clear();
  healthFn.mockResolvedValue(HEALTHY);
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
    renderHome();
    ask("Show today's message delivery performance");
    expect(await screen.findByRole("heading", { name: "Delivery performance today" })).toBeInTheDocument();
    expect(screen.getByText("90.0%")).toBeInTheDocument();
    expect(overview).toHaveBeenCalled();
  });

  it("refuses an unsupported request honestly and calls nothing", async () => {
    renderHome();
    ask("delete all my contacts");
    expect(await screen.findByRole("heading", { name: "I can't do that from here" })).toBeInTheDocument();
    expect(screen.getByText(/Nothing was changed/)).toBeInTheDocument();
    expect(campaignLaunch).not.toHaveBeenCalled();
    expect(failures).not.toHaveBeenCalled();
  });

  it("explains missing permission instead of calling the API", async () => {
    permissions.delete("analytics.view");
    permissions.delete("conversations.view");
    renderHome();
    ask("Find messages that failed");
    expect(await screen.findByRole("heading", { name: "You don't have access to that" })).toBeInTheDocument();
    expect(failures).not.toHaveBeenCalled();
    expect(recoveryFailures).not.toHaveBeenCalled();
  });

  it("shows an honest empty state, not zeros, when there is no data", async () => {
    overview.mockResolvedValue({ ...OVERVIEW, has_data: false });
    renderHome();
    ask("delivery performance");
    expect(await screen.findByRole("heading", { name: "No message activity today" })).toBeInTheDocument();
    expect(screen.queryByText("0.0%")).not.toBeInTheDocument();
  });

  it("surfaces a server error with a retry that re-runs the same command", async () => {
    recoveryFailures.mockRejectedValueOnce(
      new ApiError(403, "PERMISSION_DENIED", "You do not have permission."),
    );
    renderHome();
    ask("failed messages");
    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have permission.");
    recoveryFailures.mockResolvedValueOnce({ total: 0, shown: 0, kinds: {}, results: [] });
    fireEvent.click(screen.getByRole("button", { name: /retry|try again/i }));
    expect(await screen.findByRole("heading", { name: "No failed messages in the last 24 hours" })).toBeInTheDocument();
    expect(recoveryFailures).toHaveBeenCalledTimes(2);
  });

  describe("template studio", () => {
    it("points to the studio, lists real drafts, and creates or submits nothing", async () => {
      permissions.add("templates.manage");
      studioDrafts.mockResolvedValue({
        results: [
          {
            id: "d1",
            name: "order_shipped",
            language: "en_US",
            status: "draft",
            guidance: { state: "draft", explanation: "This is a draft." },
          },
          {
            id: "d2",
            name: "festive_offer",
            language: "en_US",
            status: "submitted",
            guidance: { state: "pending", explanation: "Meta is reviewing this template." },
          },
        ],
      });
      renderHome();
      ask("I want to create a new template");
      expect(await screen.findByRole("heading", { name: "Write a template in the Template Studio" })).toBeInTheDocument();
      expect(screen.getByText("Draft: not sent to Meta")).toBeInTheDocument();
      expect(screen.getByText("Meta is reviewing this template.")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Open the Template Studio" })).toHaveAttribute("href", "/templates?new=1");
      expect(screen.getByText(/not approved until Meta says so/)).toBeInTheDocument();
    });

    it("explains missing permission instead of calling the API", async () => {
      permissions.delete("templates.manage");
      renderHome();
      ask("create a template");
      expect(await screen.findByRole("heading", { name: "You don't have access to that" })).toBeInTheDocument();
      expect(studioDrafts).not.toHaveBeenCalled();
    });
  });

  describe("setup and test message", () => {
    const step = (id: string, state: string, over: Record<string, unknown> = {}) => ({
      id,
      title: `Title ${id}`,
      state,
      detail: `Detail ${id}`,
      next_step: "",
      fix_in: "none",
      action: "",
      evidence: {},
      ...over,
    });

    it("answers from the real setup status and lists only what is not done", async () => {
      permissions.add("numbers.manage");
      setupFn.mockResolvedValue({
        overall: "in_progress",
        summary: "Next: a test message that really arrives.",
        steps: [step("connection", "verified"), step("test_message", "pending", { next_step: "Preview a test message." })],
      });
      renderHome();
      ask("Is my WhatsApp set up and ready?");
      expect(await screen.findByRole("heading", { name: "WhatsApp setup isn't finished" })).toBeInTheDocument();
      expect(screen.getByText("Title test_message")).toBeInTheDocument();
      expect(screen.queryByText("Title connection")).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Open Set up WhatsApp" })).toHaveAttribute("href", "/setup");
    });

    it("says it is set up only when the server says ready", async () => {
      setupFn.mockResolvedValue({
        overall: "ready",
        summary: "WhatsApp is set up and a test message arrived.",
        steps: [step("connection", "verified")],
      });
      renderHome();
      ask("is whatsapp set up");
      expect(await screen.findByRole("heading", { name: "WhatsApp is set up" })).toBeInTheDocument();
    });

    it("a test-message request only points to the preview-and-approve page and sends nothing", async () => {
      permissions.add("messages.send");
      renderHome();
      ask("I want to send a test message");
      expect(await screen.findByRole("heading", { name: "Send a test message from the Set up page" })).toBeInTheDocument();
      expect(screen.getByText(/Nothing is sent from this box/)).toBeInTheDocument();
      expect(setupFn).not.toHaveBeenCalled();
    });

    it("explains missing permission instead of calling the API", async () => {
      permissions.delete("numbers.view");
      renderHome();
      ask("set up whatsapp");
      expect(await screen.findByRole("heading", { name: "You don't have access to that" })).toBeInTheDocument();
      expect(setupFn).not.toHaveBeenCalled();
    });
  });

  describe("failed messages and recovery", () => {
    const row = (over: Record<string, unknown>, recovery: Record<string, unknown>) => ({
      id: "m1",
      conversation_id: "c1",
      business_id: "b1",
      message_type: "text",
      status: "failed",
      preview: "Your order shipped",
      occurred_at: "2026-10-12T08:00:00Z",
      failed_at: "2026-10-12T08:00:05Z",
      recipient: { name: "Asha", phone_number: "919876543210" },
      from_number: "+91 90000 11111",
      recovery: {
        category: "rate_limit",
        label: "Rate limit",
        kind: "retryable",
        explanation: "x",
        next_step: "Retry it now.",
        provider_code: "131056",
        safe_error: "Too many messages",
        can_retry: true,
        blockers: [],
        links: [],
        retried_as: null,
        ...recovery,
      },
      ...over,
    });

    it("lists real failures with each next step and never offers a retry from the command box", async () => {
      recoveryFailures.mockResolvedValue({
        total: 2,
        shown: 2,
        kinds: { retryable: 1, not_retryable: 1 },
        results: [
          row({}, {}),
          row(
            { id: "m2", recipient: { name: "", phone_number: "919811111111" } },
            {
              category: "invalid_recipient",
              label: "Invalid recipient",
              kind: "not_retryable",
              next_step: "Check the contact's number.",
              can_retry: false,
            },
          ),
        ],
      });
      renderHome();
      ask("Find messages that failed this week");
      expect(await screen.findByRole("heading", { name: "Failed messages in the last 7 days" })).toBeInTheDocument();
      expect(recoveryFailures).toHaveBeenCalledWith({ days: 7, limit: 5 });
      expect(screen.getByText(/Rate limit: Retry it now\./)).toBeInTheDocument();
      expect(screen.getByText(/Invalid recipient: Check the contact's number\./)).toBeInTheDocument();
      expect(screen.getAllByText("Safe to retry").length).toBeGreaterThan(0);
      expect(screen.getByRole("link", { name: "Open failed messages" })).toHaveAttribute("href", "/failed-messages");
      expect(recoveryRetry).not.toHaveBeenCalled();
      expect(failures).not.toHaveBeenCalled();
    });

    it("falls back to the analytics summary for someone who cannot see conversations", async () => {
      permissions.delete("conversations.view");
      failures.mockResolvedValue({ total: 0, groups: [], recent: [] });
      renderHome();
      ask("failed messages this week");
      expect(await screen.findByRole("heading", { name: "No failed messages in the last 7 days" })).toBeInTheDocument();
      expect(recoveryFailures).not.toHaveBeenCalled();
    });
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
      renderHome();
      ask("launch campaign Spring Sale");
      expect(await screen.findByRole("heading", { name: "Launch “Spring Sale”?" })).toBeInTheDocument();
      expect(screen.getByText("Recipients: 120")).toBeInTheDocument();
      expect(screen.getByText(/12 recipients have no recorded opt-in/)).toBeInTheDocument();
      expect(campaignLaunch).not.toHaveBeenCalled();
    });

    it("cancelling never launches", async () => {
      renderHome();
      ask("launch campaign Spring Sale");
      await screen.findByRole("heading", { name: "Launch “Spring Sale”?" });
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(campaignLaunch).not.toHaveBeenCalled();
      expect(screen.queryByRole("heading", { name: "Launch “Spring Sale”?" })).not.toBeInTheDocument();
    });

    it("launches exactly once on confirm and reports the real result", async () => {
      renderHome();
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
      renderHome();
      ask("launch campaign Spring Sale");
      const button = await screen.findByRole("button", { name: "Launch campaign" });
      expect(button).toBeDisabled();
      expect(screen.getByRole("alert")).toHaveTextContent("The template is not approved.");
    });

    it("keeps the preview and shows the error when the launch call fails", async () => {
      campaignLaunch.mockRejectedValueOnce(new ApiError(409, "CONFLICT", "Campaign already launched."));
      renderHome();
      ask("launch campaign Spring Sale");
      fireEvent.click(await screen.findByRole("button", { name: "Launch campaign" }));
      await waitFor(() => expect(screen.getByText("Campaign already launched.")).toBeInTheDocument());
      expect(screen.getByRole("button", { name: "Launch campaign" })).toBeInTheDocument();
    });

    it("needs the launch permission before it even previews", async () => {
      permissions.delete("campaigns.launch");
      renderHome();
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
      renderHome();
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
    renderHome();
    ask("Show campaigns currently running");
    expect(await screen.findByText("Live One")).toBeInTheDocument();
    expect(screen.queryByText("Draft One")).not.toBeInTheDocument();
  });
});

describe("WhatsApp health in the command home", () => {
  it("answers from the real health endpoint, with the fix and where to do it", async () => {
    healthFn.mockResolvedValue(RED);
    renderHome();
    ask("Is everything healthy with my WhatsApp numbers?");
    expect(await screen.findByRole("heading", { name: "WhatsApp health: Problem" })).toBeInTheDocument();
    // The Home health card shows the same issue; look inside the command RESULT.
    const result = within(screen.getByRole("region", { name: "WhatsApp health: Problem" }));
    expect(result.getByText("Support line: Quality is low")).toBeInTheDocument();
    expect(result.getByText("Pause marketing sends and review who you message.")).toBeInTheDocument();
    expect(result.getByText("Fix in Meta's WhatsApp Manager")).toBeInTheDocument();
    expect(result.getByText(/not a live check/)).toBeInTheDocument();
  });

  it("says plainly when nothing is connected instead of reporting health", async () => {
    healthFn.mockResolvedValue({ ...HEALTHY, overall: "unknown", numbers: [], accounts: [] });
    renderHome();
    ask("any problems with my numbers");
    expect(
      await screen.findByRole("heading", { name: "No WhatsApp number is connected yet" }),
    ).toBeInTheDocument();
  });

  it("is refused clearly without the numbers permission, and never calls the API", async () => {
    permissions.delete("numbers.view");
    healthFn.mockClear();
    renderHome();
    ask("check whatsapp health");
    expect(await screen.findByRole("heading", { name: "You don't have access to that" })).toBeInTheDocument();
    expect(healthFn).not.toHaveBeenCalled();
  });

  it("offers a health suggestion only when real data shows something needs attention", async () => {
    healthFn.mockResolvedValue(RED);
    renderHome();
    expect(await screen.findByRole("button", { name: /Check WhatsApp health/ })).toBeInTheDocument();
  });

  it("does not offer that suggestion when everything is healthy", async () => {
    renderHome();
    await screen.findByText("Everything Meta has reported looks healthy.");
    expect(screen.queryByRole("button", { name: /Check WhatsApp health/ })).not.toBeInTheDocument();
  });

  it("shows the health card on Home with the plain summary", async () => {
    healthFn.mockResolvedValue(RED);
    renderHome();
    expect(await screen.findByRole("region", { name: "WhatsApp health" })).toHaveTextContent(
      "1 thing needs fixing.",
    );
  });
});

describe("Simple and Advanced mode on the home screen", () => {
  it("simple mode hides the dense business-overview disclosure", async () => {
    renderHome();
    await screen.findByText("Everything Meta has reported looks healthy.");
    expect(screen.queryByRole("button", { name: "Business overview" })).not.toBeInTheDocument();
  });

  it("advanced mode brings it back", async () => {
    writeStoredMode("advanced");
    renderHome();
    expect(await screen.findByRole("button", { name: "Business overview" })).toBeInTheDocument();
  });
});
