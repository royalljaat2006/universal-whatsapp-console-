import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Roles } from "./settings-sections";

const list = vi.fn();
const permissions = vi.fn();
const create = vi.fn();
const remove = vi.fn();
vi.mock("@/services/roles", () => ({
  roleService: {
    list: (...args: unknown[]) => list(...args),
    permissions: (...args: unknown[]) => permissions(...args),
    create: (...args: unknown[]) => create(...args),
    update: vi.fn(),
    remove: (...args: unknown[]) => remove(...args),
  },
}));

const ROLES = [
  { id: "r1", key: "owner", name: "Organization Owner", description: "", is_system: true, permissions: ["billing.manage", "contacts.view"], created_at: "2026-01-01T00:00:00Z" },
  { id: "r2", key: "billing-clerk", name: "Billing Clerk", description: "Views costs only", is_system: false, permissions: ["billing.view"], created_at: "2026-01-02T00:00:00Z" },
];
const CATALOGUE = [
  { code: "billing.view", description: "View usage costs and billing" },
  { code: "billing.manage", description: "Manage pricing rules and billing settings" },
  { code: "contacts.view", description: "View contacts" },
];

describe("Roles", () => {
  it("lists roles with a system badge, hiding management controls when canManage is false", async () => {
    list.mockResolvedValueOnce(ROLES);
    permissions.mockResolvedValueOnce(CATALOGUE);
    render(<Roles canManage={false} />);
    expect(await screen.findByText("Organization Owner")).toBeInTheDocument();
    expect(screen.getByText("Billing Clerk")).toBeInTheDocument();
    expect(screen.getByText("default")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New role" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });

  it("creates a role with the selected permissions", async () => {
    list.mockResolvedValueOnce(ROLES).mockResolvedValueOnce([
      ...ROLES,
      { id: "r3", key: "support", name: "Support", description: "", is_system: false, permissions: ["contacts.view"], created_at: "2026-01-03T00:00:00Z" },
    ]);
    permissions.mockResolvedValue(CATALOGUE);
    create.mockResolvedValueOnce({ id: "r3", key: "support" });

    render(<Roles canManage={true} />);
    await screen.findByText("Organization Owner");

    fireEvent.click(screen.getByRole("button", { name: "New role" }));
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Support" } });
    fireEvent.click(screen.getByText("View contacts"));
    fireEvent.click(screen.getByRole("button", { name: "Create role" }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        name: "Support",
        description: "",
        permissions: ["contacts.view"],
      }),
    );
  });

  it("deletes a non-system role after confirmation", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    list.mockResolvedValueOnce(ROLES).mockResolvedValueOnce([ROLES[0]]);
    permissions.mockResolvedValueOnce(CATALOGUE);
    remove.mockResolvedValueOnce(undefined);

    render(<Roles canManage={true} />);
    await screen.findByText("Billing Clerk");
    const deleteButtons = screen.getAllByRole("button", { name: "Delete" });
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => expect(remove).toHaveBeenCalledWith("r2"));
    confirmSpy.mockRestore();
  });
});
