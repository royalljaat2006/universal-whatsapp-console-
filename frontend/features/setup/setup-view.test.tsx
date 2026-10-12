import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";
import { UiModeProvider, resetModeForTests, writeStoredMode } from "@/stores/ui-mode";

import { SetupView } from "./setup-view";

const permissions = new Set<string>();
vi.mock("@/stores/session-context", () => ({
  useSession: () => ({
    business: { id: "b1", name: "Kiosk" },
    hasPermission: (c: string) => permissions.has(c),
  }),
}));
const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock("@/components/ui/toast", () => ({ useToast: () => toast }));
vi.mock("../numbers/embedded-signup-button", () => ({
  EmbeddedSignupButton: () => <button>Connect WhatsApp</button>,
}));

const setup = vi.fn();
const preview = vi.fn();
const sendTest = vi.fn();
const cancelTest = vi.fn();
const testStatus = vi.fn();
const verify = vi.fn();
const sync = vi.fn();
vi.mock("@/services/whatsapp", () => ({
  whatsappService: {
    setup: (...a: unknown[]) => setup(...a),
    previewTestMessage: (...a: unknown[]) => preview(...a),
    sendTestMessage: (...a: unknown[]) => sendTest(...a),
    cancelTestMessage: (...a: unknown[]) => cancelTest(...a),
    testMessageStatus: (...a: unknown[]) => testStatus(...a),
    requestVerify: (...a: unknown[]) => verify(...a),
    requestSync: (...a: unknown[]) => sync(...a),
  },
}));
const contactsList = vi.fn();
vi.mock("@/services/contacts", () => ({
  contactService: { list: (...a: unknown[]) => contactsList(...a) },
}));

const step = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  title: `Title of ${id}`,
  state: "pending",
  detail: `Detail of ${id}`,
  next_step: "",
  fix_in: "none",
  action: "",
  evidence: {},
  ...over,
});

const NUMBER = {
  id: "n1",
  label: "Support line",
  display_phone_number: "+91 90000 11111",
  status: "connected",
  business_id: "b1",
};

function status(over: Record<string, unknown> = {}) {
  return {
    generated_at: "2026-10-12T10:00:00Z",
    business: { id: "b1", name: "Kiosk" },
    overall: "in_progress",
    summary: "Next: a test message that really arrives.",
    next_step: "test_message",
    steps: [
      step("connection", { state: "verified", evidence: { accounts: 1, last_verified_at: "2026-10-12T09:00:00Z" } }),
      step("numbers", { state: "verified" }),
      step("webhook_subscription", { state: "verified" }),
      step("webhook_delivery", { state: "pending", detail: "No update from Meta has arrived yet." }),
      step("templates", { state: "verified" }),
      step("test_message", { state: "pending", action: "test_message", detail: "No test message has been sent yet." }),
    ],
    server: step("webhook_server", { state: "verified" }),
    capabilities: {
      embedded_signup: { state: "verified", detail: "ok" },
      callback_check: { state: "unsupported", detail: "This console cannot read the callback URL saved in your Meta app." },
    },
    webhook: { callback_url: "https://console.example.com/webhooks/v1/meta/whatsapp/", verify_token_configured: true },
    accounts: [{ id: "a1", name: "WABA", business_id: "b1", connection_state: "connected" }],
    numbers: [NUMBER],
    ...over,
  };
}

const PREVIEW = {
  can_send: true,
  sender: { id: "n1", display_phone_number: "+91 90000 11111", label: "Support line", business: "Kiosk" },
  recipient: { name: "Asha Rao", phone_number: "919876543210" },
  message_type: "template",
  content: "Hello! This is a test from WhatsApp Console.",
  template: { id: "t1", name: "hello_world", language: "en_US", category: "UTILITY" },
  cost: { status: "unknown", note: "No rate is loaded for this recipient's country." },
  restrictions: [{ source: "meta", text: "The recipient must have WhatsApp on that number." }],
  blockers: [],
  authorization: "attested",
  approval: { token: "tok-1", expires_in: 600 },
};

function renderView() {
  return render(
    <UiModeProvider>
      <SetupView />
    </UiModeProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  resetModeForTests();
  window.localStorage.clear();
  permissions.clear();
  ["numbers.view", "numbers.manage", "messages.send"].forEach((p) => permissions.add(p));
  setup.mockResolvedValue(status());
  contactsList.mockResolvedValue({ results: [], next: null });
});
afterEach(() => vi.useRealTimers());

describe("SetupView", () => {
  it("shows every step with its real state in words, and highlights where to continue", async () => {
    renderView();
    expect(await screen.findByText("Next: a test message that really arrives.")).toBeInTheDocument();
    expect(screen.getByText(/5 of 6 steps done|4 of 6 steps done/)).toBeInTheDocument();
    expect(screen.getAllByText("Done").length).toBe(4);
    expect(screen.getAllByText("Not yet").length).toBe(2);
    const current = screen.getAllByRole("listitem").find((li) => li.getAttribute("aria-current") === "step");
    expect(current).toHaveTextContent("Title of test_message");
    expect(screen.getByText(/leave and come back/)).toBeInTheDocument();
  });

  it("shows loading, then an error with a retry", async () => {
    setup.mockRejectedValueOnce(new ApiError(500, "SERVER_ERROR", "Something broke."));
    renderView();
    expect(screen.getByRole("status")).toHaveTextContent("Checking your setup");
    expect(await screen.findByRole("alert")).toHaveTextContent("Something broke.");
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByText(/steps done/)).toBeInTheDocument();
  });

  it("failed and unsupported states are stated plainly, with where to fix them", async () => {
    setup.mockResolvedValue(
      status({
        overall: "blocked",
        steps: [
          step("connection", { state: "failed", detail: "Meta could not verify the connection: The access token has expired.", next_step: "Try again.", fix_in: "console", action: "refresh" }),
          step("numbers", { state: "unknown", detail: "Numbers are listed after the account is connected." }),
          step("webhook_subscription", { state: "failed", fix_in: "administrator", next_step: "An administrator must subscribe the app." }),
          step("webhook_delivery", { state: "unknown" }),
          step("templates", { state: "unknown" }),
          step("test_message", { state: "pending", detail: "Finish the steps above first." }),
        ],
        server: step("webhook_server", { state: "failed", detail: "This server is missing the verify token.", fix_in: "administrator" }),
        capabilities: {
          embedded_signup: { state: "unsupported", detail: "Meta's guided sign-up is not configured on this server." },
          callback_check: { state: "unsupported", detail: "This console cannot read the callback URL." },
        },
      }),
    );
    renderView();
    expect(await screen.findByText(/access token has expired/)).toBeInTheDocument();
    expect(screen.getAllByText("Needs fixing").length).toBeGreaterThanOrEqual(3);
    expect(screen.getAllByText("Can't tell yet").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("Needs your administrator").length).toBeGreaterThan(0);
    expect(screen.getByText(/missing the verify token/)).toBeInTheDocument();
    // Unsupported: explained, with no button that cannot work.
    expect(screen.getByText(/guided sign-up is not configured/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect WhatsApp" })).not.toBeInTheDocument();
  });

  it("offers the connect button only when it is supported and the person may manage numbers", async () => {
    setup.mockResolvedValue(
      status({ steps: [step("connection", { state: "pending" }), ...status().steps.slice(1)] }),
    );
    renderView();
    expect(await screen.findByRole("button", { name: "Connect WhatsApp" })).toBeInTheDocument();
  });

  it("without numbers.manage it explains instead of showing a dead button", async () => {
    permissions.delete("numbers.manage");
    setup.mockResolvedValue(
      status({ steps: [step("connection", { state: "pending" }), ...status().steps.slice(1)] }),
    );
    renderView();
    expect(await screen.findByText(/Only someone who can manage WhatsApp numbers/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect WhatsApp" })).not.toBeInTheDocument();
  });

  it("Simple hides the evidence; Advanced shows what each state is based on", async () => {
    setup.mockResolvedValue(
      status({
        steps: [
          step("connection", { state: "verified", evidence: { accounts: 2, last_verified_at: "2026-10-12T09:00:00Z" } }),
          ...status().steps.slice(1),
        ],
      }),
    );
    const { unmount } = renderView();
    await screen.findByText(/steps done/);
    expect(screen.queryByText("What this is based on")).not.toBeInTheDocument();
    unmount();
    writeStoredMode("advanced");
    renderView();
    expect(await screen.findByText("What this is based on")).toBeInTheDocument();
    expect(screen.getByText("Last checked with Meta")).toBeInTheDocument();
  });

  it("Refresh from Meta says 'Updated' only when Meta's last-checked time really moved", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderView();
    await screen.findByText(/steps done/);
    setup.mockResolvedValue(
      status({
        steps: [
          step("connection", { state: "verified", evidence: { accounts: 1, last_verified_at: "2026-10-12T10:05:00Z" } }),
          ...status().steps.slice(1),
        ],
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Refresh from Meta/ }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2600);
    });
    expect(verify).toHaveBeenCalledWith("a1");
    expect(sync).toHaveBeenCalledWith("a1");
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Updated from Meta", expect.any(String)));
  });

  it("an unchanged last-checked time is never reported as updated", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderView();
    await screen.findByText(/steps done/);
    fireEvent.click(screen.getByRole("button", { name: /Refresh from Meta/ }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(26000);
    });
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith("Still waiting for Meta", expect.any(String)));
    expect(toast.success).not.toHaveBeenCalled();
  });
});

describe("test message: preview, approval gate, truthful progress", () => {
  async function openPreview() {
    preview.mockResolvedValue(PREVIEW);
    renderView();
    const form = await screen.findByRole("form", { name: "Test message" });
    fireEvent.change(within(form).getByLabelText(/Phone number/), { target: { value: "919876543210" } });
    fireEvent.click(within(form).getByLabelText(/agreed to receive a test message/));
    fireEvent.click(within(form).getByRole("button", { name: "Preview test message" }));
    return screen.findByRole("region", { name: "Test message preview" });
  }

  it("the form sends nothing: it only asks the server for a preview", async () => {
    await openPreview();
    expect(preview).toHaveBeenCalledWith({
      phone_number_id: "n1",
      to: "919876543210",
      attest_consent: true,
    });
    expect(sendTest).not.toHaveBeenCalled();
  });

  it("shows recipient, sender, exact content, type, cost and restrictions", async () => {
    const view = await openPreview();
    expect(within(view).getByText(/Asha Rao · 919876543210/)).toBeInTheDocument();
    expect(within(view).getByText(/Support line · \+91 90000 11111 \(Kiosk\)/)).toBeInTheDocument();
    expect(within(view).getByText("Hello! This is a test from WhatsApp Console.")).toBeInTheDocument();
    expect(within(view).getByText(/Template “hello_world” \(utility\)/)).toBeInTheDocument();
    expect(within(view).getByText(/Cost unknown/)).toBeInTheDocument();
    expect(within(view).getByText(/recipient must have WhatsApp/)).toBeInTheDocument();
    expect(within(view).getByText(/agreed to receive a test message/)).toBeInTheDocument();
  });

  it("Send stays disabled until the person ticks a separate approval", async () => {
    const view = await openPreview();
    const send = within(view).getByRole("button", { name: "Send test message" });
    expect(send).toBeDisabled();
    fireEvent.click(send);
    expect(sendTest).not.toHaveBeenCalled();
    fireEvent.click(within(view).getByLabelText(/I approve sending exactly this message/));
    expect(send).toBeEnabled();
  });

  it("cancelling discards the approval and sends nothing", async () => {
    cancelTest.mockResolvedValue({ cancelled: true });
    const view = await openPreview();
    fireEvent.click(within(view).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(cancelTest).toHaveBeenCalledWith("tok-1"));
    expect(sendTest).not.toHaveBeenCalled();
    expect(await screen.findByRole("form", { name: "Test message" })).toBeInTheDocument();
  });

  it("a blocked preview shows why and offers no approval or send", async () => {
    preview.mockResolvedValue({
      ...PREVIEW,
      can_send: false,
      approval: null,
      blockers: [{ code: "consent_required", message: "Confirm that this person agreed to receive a test message." }],
    });
    renderView();
    const form = await screen.findByRole("form", { name: "Test message" });
    fireEvent.change(within(form).getByLabelText(/Phone number/), { target: { value: "919876543210" } });
    fireEvent.click(within(form).getByRole("button", { name: "Preview test message" }));
    const view = await screen.findByRole("region", { name: "Test message preview" });
    expect(within(view).getByRole("alert")).toHaveTextContent("can't be sent yet");
    expect(within(view).queryByRole("button", { name: "Send test message" })).not.toBeInTheDocument();
    expect(within(view).queryByLabelText(/I approve/)).not.toBeInTheDocument();
  });

  it("after approval it sends once and reports only 'queued', then follows WhatsApp's own reports", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    sendTest.mockResolvedValue({
      created: true,
      message_id: "m1",
      status: "queued",
      status_note: "Queued for sending. This does not mean it was delivered.",
    });
    const stored = (s: string, over: Record<string, unknown> = {}) => ({
      id: "m1",
      status: s,
      accepted_at: null,
      sent_at: null,
      delivered_at: null,
      read_at: null,
      failed_at: null,
      error_message: "",
      ...over,
    });
    testStatus.mockResolvedValueOnce(stored("accepted"));
    const view = await openPreview();
    fireEvent.click(within(view).getByLabelText(/I approve sending exactly this message/));
    fireEvent.click(within(view).getByRole("button", { name: "Send test message" }));

    await waitFor(() => expect(sendTest).toHaveBeenCalledTimes(1));
    expect(sendTest).toHaveBeenCalledWith("tok-1");
    expect(toast.info).toHaveBeenCalledWith("Test message queued", expect.stringContaining("does not mean"));
    expect(toast.success).not.toHaveBeenCalled();

    expect(await screen.findByText("Accepted, not delivered yet")).toBeInTheDocument();
    expect(screen.queryByText("Delivered")).not.toBeInTheDocument();

    testStatus.mockResolvedValueOnce(stored("sent"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4100);
    });
    expect(await screen.findByText("Sent, not delivered yet")).toBeInTheDocument();

    testStatus.mockResolvedValue(stored("delivered", { delivered_at: "2026-10-12T10:01:00Z" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4100);
    });
    expect(await screen.findByText("Delivered")).toBeInTheDocument();
  });

  it("a provider failure is shown as failed, with a way to see why", async () => {
    sendTest.mockResolvedValue({ created: true, message_id: "m1", status: "queued", status_note: "n" });
    testStatus.mockResolvedValue({
      id: "m1",
      status: "failed",
      accepted_at: null,
      sent_at: null,
      delivered_at: null,
      read_at: null,
      failed_at: "2026-10-12T10:01:00Z",
      error_message: "The number may not be on WhatsApp",
    });
    const view = await openPreview();
    fireEvent.click(within(view).getByLabelText(/I approve sending exactly this message/));
    fireEvent.click(within(view).getByRole("button", { name: "Send test message" }));
    expect(await screen.findByText("Failed")).toBeInTheDocument();
    expect(screen.getByText(/The number may not be on WhatsApp/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /See why it failed/ })).toHaveAttribute("href", "/failed-messages");
    expect(screen.queryByText("Delivered")).not.toBeInTheDocument();
  });

  it("a refused send (approval expired) shows the server's reason and sends nothing more", async () => {
    sendTest.mockRejectedValue(new ApiError(409, "TEST_APPROVAL_INVALID", "This approval is no longer valid."));
    const view = await openPreview();
    fireEvent.click(within(view).getByLabelText(/I approve sending exactly this message/));
    fireEvent.click(within(view).getByRole("button", { name: "Send test message" }));
    expect(await within(view).findByRole("alert")).toHaveTextContent("no longer valid");
    expect(testStatus).not.toHaveBeenCalled();
  });

  it("without messages.send there is no way to start a test", async () => {
    permissions.delete("messages.send");
    renderView();
    expect(await screen.findByText(/don't have permission to send messages/)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Test message" })).not.toBeInTheDocument();
  });

  it("with no connected number it says to connect one first", async () => {
    setup.mockResolvedValue(status({ numbers: [{ ...NUMBER, status: "disconnected" }] }));
    renderView();
    expect(await screen.findByText(/Connect a number first/)).toBeInTheDocument();
  });
});
