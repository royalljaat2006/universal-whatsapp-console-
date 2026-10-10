import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";

import { LoginForm } from "./login-form";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

const login = vi.fn();
const verifyMfa = vi.fn();
vi.mock("@/services/auth", () => ({
  authService: {
    login: (...args: unknown[]) => login(...args),
    verifyMfa: (...args: unknown[]) => verifyMfa(...args),
  },
}));

describe("LoginForm", () => {
  it("toggles password visibility accessibly", () => {
    render(<LoginForm />);
    const password = screen.getByLabelText("Password");
    expect(password).toHaveAttribute("type", "password");
    fireEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(password).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Hide password" })).toHaveAttribute("aria-pressed", "true");
  });

  it("shows the backend error and marks fields invalid", async () => {
    login.mockRejectedValueOnce(new ApiError(400, "INVALID_CREDENTIALS", "Invalid email or password."));
    render(<LoginForm />);
    fireEvent.change(screen.getByLabelText("Work email"), { target: { value: "a@b.co" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
    expect(screen.getByLabelText("Work email")).toHaveAttribute("aria-invalid", "true");
    expect(replace).not.toHaveBeenCalled();
  });

  it("signs in and redirects", async () => {
    login.mockResolvedValueOnce({});
    render(<LoginForm />);
    fireEvent.change(screen.getByLabelText("Work email"), { target: { value: " a@b.co " } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(login).toHaveBeenCalledWith("a@b.co", "pw");
  });

  describe("two-step verification", () => {
    async function reachSecondStep() {
      login.mockResolvedValueOnce({ mfa_required: true });
      render(<LoginForm />);
      fireEvent.change(screen.getByLabelText("Work email"), { target: { value: "a@b.co" } });
      fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
      fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
      await screen.findByLabelText("Authentication code");
    }

    it("asks for a code instead of signing in when the backend requires one", async () => {
      replace.mockClear();
      await reachSecondStep();
      expect(replace).not.toHaveBeenCalled();
      expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    });

    it("verifies the authenticator code and redirects", async () => {
      replace.mockClear();
      verifyMfa.mockResolvedValueOnce({});
      await reachSecondStep();
      fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: " 123456 " } });
      fireEvent.click(screen.getByRole("button", { name: "Verify and sign in" }));
      await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
      expect(verifyMfa).toHaveBeenCalledWith({ code: "123456" });
    });

    it("can switch to a recovery code", async () => {
      verifyMfa.mockResolvedValueOnce({});
      await reachSecondStep();
      fireEvent.click(screen.getByRole("button", { name: "Use a recovery code" }));
      fireEvent.change(screen.getByLabelText("Recovery code"), { target: { value: "abcde-fghjk" } });
      fireEvent.click(screen.getByRole("button", { name: "Verify and sign in" }));
      await waitFor(() => expect(verifyMfa).toHaveBeenLastCalledWith({ recovery_code: "abcde-fghjk" }));
    });

    it("shows a wrong-code error and stays on the step", async () => {
      replace.mockClear();
      verifyMfa.mockRejectedValueOnce(new ApiError(401, "INVALID_MFA_CODE", "That verification code is incorrect."));
      await reachSecondStep();
      fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "000000" } });
      fireEvent.click(screen.getByRole("button", { name: "Verify and sign in" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("That verification code is incorrect.");
      expect(screen.getByLabelText("Authentication code")).toBeInTheDocument();
      expect(replace).not.toHaveBeenCalled();
    });

    it("returns to the password step when the attempt expired", async () => {
      verifyMfa.mockRejectedValueOnce(new ApiError(401, "MFA_SESSION_EXPIRED", "Your sign-in attempt expired."));
      await reachSecondStep();
      fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "000000" } });
      fireEvent.click(screen.getByRole("button", { name: "Verify and sign in" }));
      expect(await screen.findByLabelText("Password")).toBeInTheDocument();
    });
  });
});
