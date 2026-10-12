import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";
import { UiModeProvider, resetModeForTests, writeStoredMode } from "@/stores/ui-mode";

import { FailedMessagesView } from "./failed-messages-view";

const permissions = new Set<string>();
vi.mock("@/stores/session-context", () => ({
  useSession: () => ({
    business: { id: "b1", name: "Kiosk" },
    hasPermission: (c: string) => permissions.has(c),
  }),
}));
const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

const failures = vi.fn();
const detail = vi.fn();
const retry = vi.fn();
vi.mock("@/services/recovery", () => ({
  recoveryService: {
    failures: (...a: unknown[]) => failures(...a),
    detail: (...a: unknown[]) => detail(...a),
    retry: (...a: unknown[]) => retry(...a),
  },
}));

const recovery = (over: Record<string, unknown> = {}) => ({
  category: "rate_limit",
  label: "Rate limit",
  kind: "retryable",
  explanation: "This message was sent faster than WhatsApp allows, so it was not sent.",
  next_step: "Retry it now.",
  provider_code: "131056",
  safe_error: "Too many messages to this customer",
  can_retry: true,
  blockers: [],
  links: [{ label: "Meta error code reference", url: "https://developers.facebook.com/x" }],
  retried_as: null,
  ...over,
});

const message = (id: string, over: Record<string, unknown> = {}, rec: Record<string, unknown> = {}) => ({
  id,
  conversation_id: "c1",
  business_id: "b1",
  message_type: "text",
  status: "failed",
  preview: "Your order has shipped",
  occurred_at: "2026-10-12T08:00:00Z",
  failed_at: "2026-10-12T08:00:05Z",
  recipient: { name: "Asha Rao", phone_number: "919876543210" },
  from_number: "+91 90000 11111",
  recovery: recovery(rec),
  ...over,
});

const list = (results: unknown[], over: Record<string, unknown> = {}) => ({
  generated_at: "2026-10-12T10:00:00Z",
  days: 7,
  total: results.length,
  shown: results.length,
  kinds: {},
  results,
  ...over,
});

const PREVIEW = {
  ...message("m1"),
  message_text: "Your order has shipped",
  retry_preview: {
    destination: {
      name: "Asha Rao",
      phone_number: "919876543210",
      from_number: "+91 90000 11111",
      from_label: "Support line",
    },
    message_text: "Your order has shipped",
    duplicate_risk: { level: "low", text: "WhatsApp did not send the original." },
    cost: { status: "estimate", amount: "0.115", currency: "INR", note: "Rate-card estimate." },
    confirmation_required: true,
  },
};

function renderView() {
  return render(
    <UiModeProvider>
      <FailedMessagesView />
    </UiModeProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  resetModeForTests();
  window.localStorage.clear();
  permissions.clear();
  permissions.add("conversations.view");
  permissions.add("messages.send");
});
afterEach(() => vi.useRealTimers());

describe("FailedMessagesView", () => {
  it("shows loading, then each failure with category, time, plain reason and one next step", async () => {
    failures.mockResolvedValue(list([message("m1")]));
    renderView();
    expect(screen.getByRole("status")).toHaveTextContent("Loading failed messages");
    const card = await screen.findByRole("article", { name: "Failed message to Asha Rao" });
    expect(within(card).getByText(/faster than WhatsApp allows/)).toBeInTheDocument();
    expect(within(card).getByText(/What WhatsApp reported: Too many messages/)).toBeInTheDocument();
    expect(within(card).getByText("Retry it now.")).toBeInTheDocument();
    expect(within(card).getByText("Safe to retry")).toBeInTheDocument();
    expect(within(card).getByText(/Failed /)).toBeInTheDocument();
  });

  it("Simple mode hides technical codes; Advanced shows category and code", async () => {
    failures.mockResolvedValue(list([message("m1")]));
    const { unmount } = renderView();
    await screen.findByRole("article");
    expect(screen.queryByText(/code 131056/)).not.toBeInTheDocument();
    unmount();
    writeStoredMode("advanced");
    renderView();
    expect(await screen.findByText(/Rate limit · code 131056/)).toBeInTheDocument();
  });

  it("shows an honest empty state and an error with a retry", async () => {
    failures.mockResolvedValueOnce(list([]));
    const { unmount } = renderView();
    expect(await screen.findByText("No failed messages")).toBeInTheDocument();
    unmount();

    failures.mockRejectedValueOnce(new ApiError(500, "SERVER_ERROR", "Something broke."));
    renderView();
    expect(await screen.findByRole("alert")).toHaveTextContent("Something broke.");
    failures.mockResolvedValueOnce(list([]));
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByText("No failed messages")).toBeInTheDocument();
  });

  it("offers Review retry only when the server says it is allowed", async () => {
    failures.mockResolvedValue(
      list([
        message("m1"),
        message(
          "m2",
          { recipient: { name: "Bela", phone_number: "919811111111" } },
          {
            kind: "unknown",
            category: "unknown",
            label: "Unknown error",
            can_retry: false,
            blockers: [{ code: "not_retryable", message: "This kind of failure is not safe to retry." }],
          },
        ),
      ]),
    );
    renderView();
    const first = await screen.findByRole("article", { name: "Failed message to Asha Rao" });
    const second = screen.getByRole("article", { name: "Failed message to Bela" });
    expect(within(first).getByRole("button", { name: /Review retry/ })).toBeInTheDocument();
    expect(within(second).queryByRole("button", { name: /Review retry/ })).not.toBeInTheDocument();
    expect(within(second).getByText(/Why no retry: This kind of failure is not safe to retry\./)).toBeInTheDocument();
    expect(within(second).getByText("Outcome unclear")).toBeInTheDocument();
  });

  it("without send permission no retry button appears, and the reason is stated", async () => {
    permissions.delete("messages.send");
    failures.mockResolvedValue(list([message("m1")]));
    renderView();
    const card = await screen.findByRole("article");
    expect(within(card).queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
    expect(within(card).getByText(/have permission to send/)).toBeInTheDocument();
  });

  it("an external failure points to Meta in a new tab and has no retry", async () => {
    failures.mockResolvedValue(
      list([
        message(
          "m1",
          {},
          {
            kind: "external",
            can_retry: false,
            category: "policy_window",
            next_step: "Open WhatsApp Manager in Meta. This cannot be fixed from this console.",
            links: [
              {
                label: "Open WhatsApp Manager (Meta)",
                url: "https://business.facebook.com/latest/whatsapp_manager",
              },
            ],
            blockers: [{ code: "not_retryable", message: "This kind of failure is not safe to retry." }],
          },
        ),
      ]),
    );
    renderView();
    const link = await screen.findByRole("link", { name: /Open WhatsApp Manager \(Meta\)/ });
    expect(link).toHaveAttribute("href", "https://business.facebook.com/latest/whatsapp_manager");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(screen.queryByRole("button", { name: /Review retry/ })).not.toBeInTheDocument();
    expect(screen.getByText("Fix in Meta")).toBeInTheDocument();
  });
});

describe("retry confirmation", () => {
  beforeEach(() => {
    failures.mockResolvedValue(list([message("m1")]));
    detail.mockResolvedValue(PREVIEW);
  });

  async function openDialog() {
    renderView();
    fireEvent.click(await screen.findByRole("button", { name: /Review retry/ }));
    return screen.findByRole("dialog");
  }

  it("shows recipient, sender, message, duplicate risk and cost, and sends NOTHING until confirmed", async () => {
    const dialog = await openDialog();
    expect(await within(dialog).findByText("Confirm and retry")).toBeInTheDocument();
    expect(within(dialog).getByText(/Asha Rao · 919876543210/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Support line · \+91 90000 11111/)).toBeInTheDocument();
    expect(within(dialog).getByText("Your order has shipped")).toBeInTheDocument();
    expect(within(dialog).getByText(/did not send the original/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Estimated INR 0\.115/)).toBeInTheDocument();
    expect(detail).toHaveBeenCalledWith("m1");
    expect(retry).not.toHaveBeenCalled();
    expect(dialog).toHaveAccessibleName("Retry this message?");
    expect(dialog).toHaveAccessibleDescription(/Nothing is sent until you confirm/);
  });

  it("Cancel closes without sending", async () => {
    const dialog = await openDialog();
    await within(dialog).findByText("Confirm and retry");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(retry).not.toHaveBeenCalled();
  });

  it("confirming calls the retry once, then says only that it was QUEUED, not delivered", async () => {
    retry.mockResolvedValue({
      created: true,
      message: { id: "r1", status: "queued", delivered_at: null },
      status_note: "Retry queued. This only means it was accepted for sending.",
    });
    const dialog = await openDialog();
    fireEvent.click(await within(dialog).findByText("Confirm and retry"));
    await waitFor(() => expect(retry).toHaveBeenCalledTimes(1));
    expect(retry).toHaveBeenCalledWith("m1");
    expect(toast.info).toHaveBeenCalledWith("Retry queued", expect.stringContaining("only means"));
    expect(toast.success).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("a refused retry (409) shows the server reason in the dialog and refreshes the list", async () => {
    retry.mockRejectedValue(
      new ApiError(409, "RETRY_NOT_ALLOWED", "A retry was already requested for this message."),
    );
    const dialog = await openDialog();
    fireEvent.click(await within(dialog).findByText("Confirm and retry"));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("already requested");
    await waitFor(() => expect(failures.mock.calls.length).toBeGreaterThan(1));
    expect(toast.info).not.toHaveBeenCalled();
  });

  it("when the server has no retry preview it explains why instead of offering to send", async () => {
    detail.mockResolvedValue({
      ...PREVIEW,
      retry_preview: null,
      recovery: recovery({
        can_retry: false,
        blockers: [{ code: "already_retried", message: "A retry was already requested." }],
      }),
    });
    const dialog = await openDialog();
    expect(await within(dialog).findByText("A retry was already requested.")).toBeInTheDocument();
    expect(within(dialog).queryByText("Confirm and retry")).not.toBeInTheDocument();
  });
});

describe("truthful retry status", () => {
  const attempt = (status: string, over: Record<string, unknown> = {}) => ({
    id: "r1",
    status,
    status_label: status,
    created_at: "2026-10-12T09:00:00Z",
    accepted_at: null,
    sent_at: null,
    delivered_at: null,
    read_at: null,
    failed_at: null,
    failure: null,
    ...over,
  });
  const withAttempt = (a: unknown) => list([message("m1", {}, { can_retry: false, retried_as: a })]);

  it("an accepted retry is shown as accepted, not delivered, and the page keeps checking", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    failures.mockResolvedValueOnce(withAttempt(attempt("accepted")));
    renderView();
    expect(await screen.findByText("Accepted, not delivered yet")).toBeInTheDocument();
    expect(screen.queryByText("Delivered")).not.toBeInTheDocument();

    failures.mockResolvedValueOnce(
      withAttempt(attempt("delivered", { delivered_at: "2026-10-12T09:01:00Z" })),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });
    expect(await screen.findByText("Delivered")).toBeInTheDocument();
    expect(screen.queryByText("Accepted, not delivered yet")).not.toBeInTheDocument();
  });

  it("stops polling once nothing is in flight", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    failures.mockResolvedValue(withAttempt(attempt("delivered")));
    renderView();
    await screen.findByText("Delivered");
    const calls = failures.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(failures.mock.calls.length).toBe(calls);
  });

  it("a retry that failed again is shown as failed with its reason", async () => {
    failures.mockResolvedValue(
      withAttempt(
        attempt("failed", {
          failure: {
            category: "invalid_recipient",
            label: "Invalid recipient",
            safe_error: "Not on WhatsApp",
            can_retry: false,
          },
        }),
      ),
    );
    renderView();
    expect(await screen.findByText("Retry failed")).toBeInTheDocument();
    expect(screen.getByText(/The retry failed too: Not on WhatsApp/)).toBeInTheDocument();
  });
});
