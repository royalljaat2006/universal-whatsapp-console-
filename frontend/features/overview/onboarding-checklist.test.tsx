import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Business, Member, PhoneNumber } from "@/types/api";

import { OnboardingChecklist } from "./onboarding-checklist";

const members = vi.fn();
vi.mock("@/services/organizations", () => ({
  organizationService: { members: (...args: unknown[]) => members(...args) },
}));

let sessionState: {
  businesses: Business[];
  hasPermission: (code: string) => boolean;
  loading: boolean;
};
vi.mock("@/stores/session-context", () => ({
  useSession: () => sessionState,
}));

function business(overrides: Partial<Business> = {}): Business {
  return {
    id: "b1",
    name: "Acme",
    slug: "acme",
    description: "",
    status: "active",
    timezone: "UTC",
    business_account_count: 0,
    phone_number_count: 0,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

function member(id: string): Member {
  return {
    id,
    email: `${id}@example.com`,
    full_name: id,
    role: "agent",
    status: "active",
    joined_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
  };
}

function numberRow(): PhoneNumber {
  return {
    id: "n1",
    business_account_id: "w1",
    display_phone_number: "+1 555 0100",
    verified_name: "Acme",
    label: "",
    status: "connected",
    quality_rating: "green",
    created_at: new Date().toISOString(),
  } as PhoneNumber;
}

describe("OnboardingChecklist", () => {
  beforeEach(() => {
    members.mockReset();
    members.mockResolvedValue({ next: null, previous: null, results: [] });
  });

  it("shows nothing while the session is still loading", () => {
    sessionState = { businesses: [], hasPermission: () => true, loading: true };
    const { container } = render(<OnboardingChecklist numbers={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("prompts to create a business when there are none yet", () => {
    sessionState = { businesses: [], hasPermission: () => true, loading: false };
    render(<OnboardingChecklist numbers={undefined} />);
    expect(screen.getByText("Create a business")).toBeInTheDocument();
    // Steps gated on having a business don't appear yet.
    expect(screen.queryByText("Connect a WhatsApp number")).not.toBeInTheDocument();
  });

  it("hides a step the caller lacks permission to verify", () => {
    sessionState = {
      businesses: [business()],
      hasPermission: (code) => code !== "members.view",
      loading: false,
    };
    render(<OnboardingChecklist numbers={[numberRow()]} />);
    expect(screen.queryByText("Invite your team")).not.toBeInTheDocument();
  });

  it("renders nothing once every visible step is complete", async () => {
    members.mockResolvedValueOnce({ next: null, previous: null, results: [member("a"), member("b")] });
    sessionState = { businesses: [business()], hasPermission: () => true, loading: false };
    const { container } = render(<OnboardingChecklist numbers={[numberRow()]} />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("keeps showing an incomplete step alongside completed ones", async () => {
    members.mockResolvedValueOnce({ next: null, previous: null, results: [member("a")] });
    sessionState = { businesses: [business()], hasPermission: () => true, loading: false };
    render(<OnboardingChecklist numbers={[numberRow()]} />);
    await waitFor(() => expect(screen.getByText("Invite your team")).toBeInTheDocument());
    expect(screen.getByText("2 of 3 steps complete")).toBeInTheDocument();
  });
});
