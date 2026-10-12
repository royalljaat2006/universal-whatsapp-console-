import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";

import { HealthPanel } from "./health-panel";
import { HealthSummaryCard } from "./health-summary-card";

const permissions = new Set<string>();
vi.mock("@/stores/session-context", () => ({
  useSession: () => ({
    business: { id: "b1", name: "Kiosk" },
    hasPermission: (c: string) => permissions.has(c),
  }),
}));

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

const healthFn = vi.fn();
const verify = vi.fn();
const sync = vi.fn();
vi.mock("@/services/whatsapp", () => ({
  whatsappService: {
    health: (...a: unknown[]) => healthFn(...a),
    requestVerify: (...a: unknown[]) => verify(...a),
    requestSync: (...a: unknown[]) => sync(...a),
  },
}));

const check = (over: Record<string, unknown>) => ({
  code: "x",
  state: "ok",
  title: "A fine thing",
  detail: "detail",
  next_step: "",
  fix_in: "none",
  basis: "provider",
  action: "",
  ...over,
});

function data(over: Record<string, unknown> = {}, checkedAt = "2026-10-12T09:00:00Z") {
  return {
    generated_at: "2026-10-12T10:00:00Z",
    overall: "problem",
    summary: "1 thing needs fixing and 1 needs attention.",
    accounts: [
      {
        id: "a1",
        name: "Main account",
        business_id: "b1",
        business_name: "Kiosk",
        overall: "attention",
        last_checked_at: checkedAt,
        checks: [check({ code: "freshness", state: "warning", title: "Status may be out of date" })],
      },
    ],
    numbers: [
      {
        id: "n1",
        label: "Support line",
        display_phone_number: "+91 90000 00001",
        verified_name: "",
        business_id: "b1",
        business_name: "Kiosk",
        business_account_id: "a1",
        overall: "problem",
        last_checked_at: checkedAt,
        checks: [
          check({ code: "ok1", state: "ok", title: "Ready to send and receive" }),
          check({
            code: "quality",
            state: "problem",
            title: "Quality is low",
            detail: "Low quality can lead to a reduced limit.",
            next_step: "Pause marketing sends.",
            fix_in: "meta",
          }),
          check({
            code: "limit",
            state: "unknown",
            title: "Messaging limit",
            detail: "Meta has not reported a limit.",
          }),
        ],
      },
    ],
    recent_alerts: [
      {
        id: "e1",
        severity: "warning",
        summary: "Quality dropped",
        occurred_at: "2026-10-12T08:00:00Z",
        business_name: "Kiosk",
      },
    ],
    notes: ["Statuses are what Meta last reported to this console, not a live check."],
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  permissions.clear();
  permissions.add("numbers.view");
  verify.mockResolvedValue({ status: "queued" });
  sync.mockResolvedValue({ status: "queued" });
});
afterEach(() => vi.useRealTimers());

describe("HealthPanel", () => {
  it("leads with what needs action and says where to fix it", async () => {
    healthFn.mockResolvedValue(data());
    render(<HealthPanel />);
    expect(await screen.findByText("1 thing needs fixing and 1 needs attention.")).toBeInTheDocument();
    const number = within(screen.getByRole("region", { name: /Health of \+91 90000 00001/ }));
    const items = number.getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(items[0]).toContain("Quality is low"); // problem first
    expect(items[0]).toContain("Pause marketing sends.");
    expect(items[0]).toContain("Fix in Meta's WhatsApp Manager");
    expect(number.getByText("Messaging limit")).toBeInTheDocument(); // unknown is shown, not hidden as fine
    expect(screen.getByText(/not a live check/)).toBeInTheDocument();
    expect(screen.getByText("Quality dropped")).toBeInTheDocument();
  });

  it("keeps the fine things one click away rather than cluttering the page", async () => {
    healthFn.mockResolvedValue(data());
    render(<HealthPanel />);
    const summary = await screen.findByText(/1 thing that are fine/);
    expect(summary.closest("details")).not.toHaveAttribute("open");
  });

  it("offers Refresh from Meta only to people who may manage numbers", async () => {
    healthFn.mockResolvedValue(data());
    const { unmount } = render(<HealthPanel />);
    await screen.findByText(/needs fixing/);
    expect(screen.queryByRole("button", { name: /Refresh from Meta/ })).not.toBeInTheDocument();
    unmount();

    permissions.add("numbers.manage");
    render(<HealthPanel />);
    expect(await screen.findByRole("button", { name: /Refresh from Meta/ })).toBeInTheDocument();
  });

  it("shows an honest error with retry, never an invented all-clear", async () => {
    healthFn.mockRejectedValueOnce(new ApiError(500, "UPSTREAM_UNAVAILABLE", "Server unavailable."));
    render(<HealthPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Server unavailable.");
    healthFn.mockResolvedValue(data());
    fireEvent.click(screen.getByRole("button", { name: /retry|try again/i }));
    expect(await screen.findByText(/needs fixing/)).toBeInTheDocument();
  });

  describe("refreshing from Meta", () => {
    beforeEach(() => {
      permissions.add("numbers.manage");
    });

    it("asks Meta (verify + sync) and says 'Updated' only after every timestamp advanced", async () => {
      healthFn.mockResolvedValueOnce(data()); // initial load: checked 09:00
      render(<HealthPanel />);
      const button = await screen.findByRole("button", { name: /Refresh from Meta/ });

      vi.useFakeTimers({ shouldAdvanceTime: true });
      healthFn.mockResolvedValue(data({}, "2026-10-12T10:05:00Z")); // later: advanced
      fireEvent.click(button);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000);
      });
      await waitFor(() =>
        expect(toast.success).toHaveBeenCalledWith("Updated from Meta", expect.any(String)),
      );
      expect(verify).toHaveBeenCalledWith("a1");
      expect(sync).toHaveBeenCalledWith("a1");
      expect(toast.info).not.toHaveBeenCalled();
    });

    it("does NOT claim success when Meta never answered; it says it is still waiting", async () => {
      healthFn.mockResolvedValue(data()); // timestamps never move
      render(<HealthPanel />);
      const button = await screen.findByRole("button", { name: /Refresh from Meta/ });

      vi.useFakeTimers({ shouldAdvanceTime: true });
      fireEvent.click(button);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30000);
      });
      await waitFor(() =>
        expect(toast.info).toHaveBeenCalledWith("Still waiting for Meta", expect.any(String)),
      );
      expect(toast.success).not.toHaveBeenCalled();
    });

    it("reports a failed refresh request as an error, not as success", async () => {
      healthFn.mockResolvedValue(data());
      verify.mockRejectedValue(new ApiError(403, "PERMISSION_DENIED", "You do not have permission."));
      render(<HealthPanel />);
      fireEvent.click(await screen.findByRole("button", { name: /Refresh from Meta/ }));
      await waitFor(() =>
        expect(toast.error).toHaveBeenCalledWith("Couldn't refresh", "You do not have permission."),
      );
      expect(toast.success).not.toHaveBeenCalled();
    });
  });
});

describe("HealthSummaryCard", () => {
  const sample = data() as never;

  it("shows a loading state, not a reassuring default", () => {
    render(<HealthSummaryCard health={undefined} loading error={undefined} />);
    expect(screen.getByText(/Checking your WhatsApp connection/)).toBeInTheDocument();
  });

  it("shows an error plainly", () => {
    render(<HealthSummaryCard health={undefined} loading={false} error={new ApiError(500, "X", "Boom")} />);
    expect(screen.getByText("Couldn't check WhatsApp health")).toBeInTheDocument();
    expect(screen.getByText("Boom")).toBeInTheDocument();
  });

  it("renders nothing when no WhatsApp account is connected (the setup checklist covers it)", () => {
    const { container } = render(
      <HealthSummaryCard
        health={data({ numbers: [], accounts: [] }) as never}
        loading={false}
        error={undefined}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("lists the top issues with the next step and a link to the details", () => {
    render(<HealthSummaryCard health={sample} loading={false} error={undefined} />);
    expect(screen.getByText("Support line: Quality is low")).toBeInTheDocument();
    expect(screen.getByText("Pause marketing sends.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /See details/ })).toHaveAttribute("href", "/numbers");
  });
});
