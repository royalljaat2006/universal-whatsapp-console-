import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";

import { initials } from "./initials-avatar";
import { EmptyState, ErrorState } from "./states";
import { StatusBadge, toneFor } from "./status-badge";

describe("StatusBadge", () => {
  it("always renders the state as text (never colour alone)", () => {
    render(<StatusBadge tone={toneFor("connected")}>connected</StatusBadge>);
    expect(screen.getByText("connected")).toBeInTheDocument();
  });

  it("maps backend vocabularies to semantic tones", () => {
    expect(toneFor("APPROVED")).toBe("success");
    expect(toneFor("pending")).toBe("warning");
    expect(toneFor("disconnected")).toBe("danger");
    expect(toneFor("running")).toBe("info");
    expect(toneFor("something-new")).toBe("neutral");
  });
});

describe("ErrorState", () => {
  it("explains connection failures and offers retry", () => {
    const onRetry = vi.fn();
    render(<ErrorState error={new ApiError(0, "NETWORK_ERROR", "The server could not be reached.")} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Connection failed");
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("shows access denial with the backend's safe message and request id", () => {
    render(<ErrorState error={new ApiError(403, "PERMISSION_DENIED", "Missing permission: campaigns.launch", {}, "req-1")} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("You don't have access to this");
    expect(alert).toHaveTextContent("Missing permission: campaigns.launch");
    expect(alert).toHaveTextContent("req-1");
  });

  it("maps a missing WhatsApp configuration", () => {
    render(<ErrorState error={new ApiError(503, "CONFIGURATION_REQUIRED", "not configured")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("WhatsApp connection unavailable");
  });
});

describe("EmptyState", () => {
  it("announces an honest empty state", () => {
    render(<EmptyState title="No conversations yet" description="They appear when customers write." />);
    expect(screen.getByRole("status")).toHaveTextContent("No conversations yet");
  });
});

describe("initials", () => {
  it("derives initials from real names only", () => {
    expect(initials("Asha Rao")).toBe("AR");
    expect(initials("kiosk")).toBe("KI");
    expect(initials("")).toBe("?");
  });
});
