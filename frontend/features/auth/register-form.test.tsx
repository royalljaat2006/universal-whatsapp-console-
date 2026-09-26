import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";

import { RegisterForm } from "./register-form";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

const register = vi.fn();
vi.mock("@/services/auth", () => ({
  authService: { register: (...args: unknown[]) => register(...args) },
}));

function fillForm() {
  fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "Ada Lovelace" } });
  fireEvent.change(screen.getByLabelText("Work email"), { target: { value: " ada@example.com " } });
  fireEvent.change(screen.getByLabelText("Organization name"), { target: { value: " Acme Co " } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct-horse-battery" } });
}

describe("RegisterForm", () => {
  it("creates the account and redirects", async () => {
    register.mockResolvedValueOnce({});
    render(<RegisterForm />);
    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Create workspace" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(register).toHaveBeenCalledWith({
      email: "ada@example.com",
      password: "correct-horse-battery",
      full_name: "Ada Lovelace",
      organization_name: "Acme Co",
    });
  });

  it("shows a conflict error without redirecting", async () => {
    register.mockRejectedValueOnce(
      new ApiError(409, "CONFLICT", "An account with this email already exists."),
    );
    render(<RegisterForm />);
    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Create workspace" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "An account with this email already exists.",
    );
    expect(replace).not.toHaveBeenCalled();
  });

  it("toggles password visibility accessibly", () => {
    render(<RegisterForm />);
    const password = screen.getByLabelText("Password");
    expect(password).toHaveAttribute("type", "password");
    fireEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(password).toHaveAttribute("type", "text");
  });
});
