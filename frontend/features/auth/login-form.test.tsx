import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";

import { LoginForm } from "./login-form";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

const login = vi.fn();
vi.mock("@/services/auth", () => ({ authService: { login: (...args: unknown[]) => login(...args) } }));

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
});
