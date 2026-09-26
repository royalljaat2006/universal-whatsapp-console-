import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EmbeddedSignupButton } from "./embedded-signup-button";

const embeddedSignupConfig = vi.fn();
const completeEmbeddedSignup = vi.fn();
vi.mock("@/services/whatsapp", () => ({
  whatsappService: {
    embeddedSignupConfig: (...args: unknown[]) => embeddedSignupConfig(...args),
    completeEmbeddedSignup: (...args: unknown[]) => completeEmbeddedSignup(...args),
  },
}));

const mockInit = vi.fn();
const mockLogin = vi.fn();
const CONFIG = { app_id: "app-1", config_id: "config-1", graph_api_version: "v26.0", configured: true };

function finishMessage(overrides: { waba_id?: string; phone_number_id?: string } = {}) {
  window.dispatchEvent(
    new MessageEvent("message", {
      origin: "https://www.facebook.com",
      data: JSON.stringify({
        type: "WA_EMBEDDED_SIGNUP",
        event: "FINISH",
        data: { waba_id: "waba-1", phone_number_id: "phone-1", ...overrides },
      }),
    }),
  );
}

function cancelMessage() {
  window.dispatchEvent(
    new MessageEvent("message", {
      origin: "https://www.facebook.com",
      data: JSON.stringify({ type: "WA_EMBEDDED_SIGNUP", event: "CANCEL", data: { current_step: "MODE" } }),
    }),
  );
}

describe("EmbeddedSignupButton", () => {
  beforeEach(() => {
    embeddedSignupConfig.mockReset().mockResolvedValue(CONFIG);
    completeEmbeddedSignup.mockReset();
    mockInit.mockReset();
    mockLogin.mockReset();
    window.FB = { init: mockInit, login: mockLogin };
  });

  it("disables the button when Embedded Signup isn't configured", async () => {
    embeddedSignupConfig.mockResolvedValue({ ...CONFIG, configured: false });
    render(<EmbeddedSignupButton businessId="b1" onConnected={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button")).toBeDisabled());
  });

  it("completes the connection once the FINISH event and login code both arrive (event first)", async () => {
    const onConnected = vi.fn();
    completeEmbeddedSignup.mockResolvedValueOnce({});
    render(<EmbeddedSignupButton businessId="b1" onConnected={onConnected} />);

    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(mockLogin).toHaveBeenCalledTimes(1));
    expect(mockInit).toHaveBeenCalledWith({ appId: "app-1", version: "v26.0", xfbml: true });
    expect(mockLogin.mock.calls[0][1]).toMatchObject({
      config_id: "config-1",
      response_type: "code",
      override_default_response_type: true,
    });

    finishMessage();
    expect(completeEmbeddedSignup).not.toHaveBeenCalled(); // no code yet

    const loginCallback = mockLogin.mock.calls[0][0];
    loginCallback({ authResponse: { code: "auth-code-1" } });

    await waitFor(() =>
      expect(completeEmbeddedSignup).toHaveBeenCalledWith({
        business_id: "b1",
        waba_id: "waba-1",
        phone_number_id: "phone-1",
        code: "auth-code-1",
      }),
    );
    await waitFor(() => expect(onConnected).toHaveBeenCalledTimes(1));
  });

  it("completes the connection when the login code arrives before the FINISH event", async () => {
    const onConnected = vi.fn();
    completeEmbeddedSignup.mockResolvedValueOnce({});
    render(<EmbeddedSignupButton businessId="b2" onConnected={onConnected} />);

    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(mockLogin).toHaveBeenCalledTimes(1));

    const loginCallback = mockLogin.mock.calls[0][0];
    loginCallback({ authResponse: { code: "auth-code-2" } });
    expect(completeEmbeddedSignup).not.toHaveBeenCalled(); // no waba/phone number yet

    finishMessage({ waba_id: "waba-2", phone_number_id: "phone-2" });

    await waitFor(() =>
      expect(completeEmbeddedSignup).toHaveBeenCalledWith({
        business_id: "b2",
        waba_id: "waba-2",
        phone_number_id: "phone-2",
        code: "auth-code-2",
      }),
    );
    await waitFor(() => expect(onConnected).toHaveBeenCalledTimes(1));
  });

  it("does not treat a cancelled signup as a completion", async () => {
    const onConnected = vi.fn();
    render(<EmbeddedSignupButton businessId="b3" onConnected={onConnected} />);

    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(mockLogin).toHaveBeenCalledTimes(1));

    cancelMessage();

    expect(completeEmbeddedSignup).not.toHaveBeenCalled();
    expect(onConnected).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("button")).not.toHaveAttribute("aria-busy"));
  });
});
