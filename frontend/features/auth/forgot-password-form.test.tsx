import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";

import { ForgotPasswordForm } from "./forgot-password-form";

const requestPasswordReset = vi.fn();
vi.mock("@/services/auth", () => ({
  authService: { requestPasswordReset: (...args: unknown[]) => requestPasswordReset(...args) },
}));

describe("ForgotPasswordForm", () => {
  it("shows the same confirmation regardless of whether the account exists", async () => {
    requestPasswordReset.mockResolvedValueOnce(undefined);
    render(<ForgotPasswordForm />);
    fireEvent.change(screen.getByLabelText("Work email"), { target: { value: "someone@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));
    await waitFor(() =>
      expect(screen.getByText(/If an account exists for/)).toHaveTextContent("someone@example.com"),
    );
    expect(requestPasswordReset).toHaveBeenCalledWith("someone@example.com");
  });

  it("shows an error only for a genuine request failure (e.g. rate limited)", async () => {
    requestPasswordReset.mockRejectedValueOnce(new ApiError(429, "RATE_LIMITED", "Too many attempts."));
    render(<ForgotPasswordForm />);
    fireEvent.change(screen.getByLabelText("Work email"), { target: { value: "someone@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts.");
    expect(screen.queryByText(/If an account exists for/)).not.toBeInTheDocument();
  });
});
