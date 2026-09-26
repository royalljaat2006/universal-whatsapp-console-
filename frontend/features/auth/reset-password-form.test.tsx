import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";

import { ResetPasswordForm } from "./reset-password-form";

const replace = vi.fn();
let params = new URLSearchParams({ uid: "abc123", token: "tok456" });
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => params,
}));

const confirmPasswordReset = vi.fn();
vi.mock("@/services/auth", () => ({
  authService: { confirmPasswordReset: (...args: unknown[]) => confirmPasswordReset(...args) },
}));

describe("ResetPasswordForm", () => {
  it("submits the uid/token from the URL with the new password", async () => {
    confirmPasswordReset.mockResolvedValueOnce(undefined);
    render(<ResetPasswordForm />);
    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "brand-new-password-99" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Update password" }));
    await waitFor(() =>
      expect(confirmPasswordReset).toHaveBeenCalledWith({
        uid: "abc123",
        token: "tok456",
        password: "brand-new-password-99",
      }),
    );
    expect(await screen.findByText(/Password updated/)).toBeInTheDocument();
  });

  it("shows an error for an expired or invalid link", async () => {
    confirmPasswordReset.mockRejectedValueOnce(
      new ApiError(403, "INVALID_TOKEN", "This link is invalid or has expired."),
    );
    render(<ResetPasswordForm />);
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "another-password-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Update password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This link is invalid or has expired.");
  });

  it("shows a clear message when the link is missing its token", () => {
    params = new URLSearchParams();
    render(<ResetPasswordForm />);
    expect(screen.getByText(/link is incomplete/)).toBeInTheDocument();
    params = new URLSearchParams({ uid: "abc123", token: "tok456" });
  });
});
