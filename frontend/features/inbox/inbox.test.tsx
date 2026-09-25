import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { InboxConversation, InboxMessage } from "@/types/api";

import { ConversationList } from "./conversation-list";
import { MessageComposer } from "./message-composer";
import { MessageThread } from "./message-thread";

vi.mock("@/services/templates", () => ({
  templateService: {
    list: vi.fn(async () => ({ next: null, previous: null, results: [] })),
    get: vi.fn(),
  },
}));

function conversation(overrides: Partial<InboxConversation> = {}): InboxConversation {
  return {
    id: "c1",
    business_id: "b1",
    contact_id: "k1",
    contact_name: "Asha",
    contact_phone_number: "919999999999",
    phone_number_id: "n1",
    phone_number_display: "+91 99999 00000",
    status: "open",
    unread_count: 0,
    last_message_at: new Date().toISOString(),
    last_inbound_at: new Date().toISOString(),
    last_message_preview: "hello",
    last_message_direction: "inbound",
    service_window_expires_at: null,
    service_window_open: true,
    created_at: new Date().toISOString(),
    assignee: null,
    team: null,
    tags: [],
    send_policy: {
      free_form_allowed: true,
      template_allowed: true,
      window_open: true,
      window_expires_at: new Date(Date.now() + 3 * 3600_000).toISOString(),
      reason: "SERVICE_WINDOW_OPEN",
    },
    ...overrides,
  };
}

function message(overrides: Partial<InboxMessage> = {}): InboxMessage {
  return {
    id: "m1",
    business_id: "b1",
    business_account_id: "w1",
    conversation_id: "c1",
    phone_number_id: "n1",
    contact_id: "k1",
    direction: "inbound",
    message_type: "text",
    status: "received",
    text: "hi",
    template_id: null,
    template: null,
    attachments: [],
    external_id: "wamid.1",
    reply_to_external_id: "",
    occurred_at: new Date(2026, 8, 22, 10).toISOString(),
    sent_at: null,
    delivered_at: null,
    read_at: null,
    failed_at: null,
    error_code: "",
    error_message: "",
    billable: null,
    pricing_category: "",
    cost_status: "none",
    cost: null,
    created_at: new Date(2026, 8, 22, 10).toISOString(),
    ...overrides,
  };
}

describe("MessageComposer", () => {
  it("offers free-form tabs while the backend says the window is open", () => {
    render(
      <MessageComposer conversation={conversation()} businessAccountId="w1" canSend onQueued={() => {}} />,
    );
    expect(screen.getByRole("tab", { name: "Text" })).toBeEnabled();
    expect(screen.getByRole("tab", { name: "Attach" })).toBeEnabled();
    expect(screen.getByRole("status")).toHaveTextContent(/window open/i);
    expect(screen.getByLabelText("Message")).toBeInTheDocument();
  });

  it("locks to templates when the policy requires one", async () => {
    const closed = conversation({
      send_policy: {
        free_form_allowed: false,
        template_allowed: true,
        window_open: false,
        window_expires_at: null,
        reason: "TEMPLATE_REQUIRED_WINDOW_CLOSED",
      },
    });
    render(<MessageComposer conversation={closed} businessAccountId="w1" canSend onQueued={() => {}} />);
    expect(screen.getByRole("tab", { name: "Text" })).toBeDisabled();
    expect(screen.getByRole("tab", { name: "Attach" })).toBeDisabled();
    expect(screen.getByRole("tab", { name: "Template" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("status")).toHaveTextContent(/only an approved template/i);
    expect(screen.queryByLabelText("Message")).not.toBeInTheDocument();
    expect(await screen.findByText(/no approved templates are synced/i)).toBeInTheDocument();
  });

  it("hides sending entirely without messages.send", () => {
    render(
      <MessageComposer
        conversation={conversation()}
        businessAccountId="w1"
        canSend={false}
        onQueued={() => {}}
      />,
    );
    expect(screen.getByText(/do not have permission/i)).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });
});

describe("MessageThread", () => {
  it("groups by day and renders media, template and failure states as stored", () => {
    const messages = [
      message(),
      message({
        id: "m2",
        occurred_at: new Date(2026, 8, 23, 9).toISOString(),
        text: "",
        message_type: "image",
        attachments: [
          {
            id: "a1",
            role: "body",
            kind: "image",
            mime_type: "image/jpeg",
            file_size: 100,
            filename: "",
            caption: "",
            storage_status: "pending",
            last_error: "",
            media_asset_id: null,
            content_url: null,
          },
        ],
      }),
      message({
        id: "m3",
        direction: "outbound",
        occurred_at: new Date(2026, 8, 23, 9, 5).toISOString(),
        message_type: "template",
        text: "Hi Asha",
        status: "failed",
        error_code: "131026",
        error_message: "The recipient cannot receive this message.",
        template: { name: "order_update", language: "en", category: "UTILITY" },
      }),
    ];
    const onLoadOlder = vi.fn();
    render(<MessageThread messages={messages} hasOlder onLoadOlder={onLoadOlder} />);
    expect(screen.getAllByText(/Sep|Today|Yesterday|2026/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/downloading from WhatsApp/i)).toBeInTheDocument();
    expect(screen.getByText(/Template · order_update/)).toBeInTheDocument();
    expect(screen.getByText("The recipient cannot receive this message.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load older messages" }));
    expect(onLoadOlder).toHaveBeenCalledOnce();
  });
});

describe("ConversationList", () => {
  it("shows tags, assignee and a closed window, and pages with load more", () => {
    const onLoadMore = vi.fn();
    render(
      <ConversationList
        conversations={[
          conversation({
            unread_count: 2,
            assignee: { membership_id: "m", name: "Ravi", email: "r@example.com" },
            tags: [{ id: "t1", name: "VIP", color: "" }],
            send_policy: { ...conversation().send_policy, window_open: false },
          }),
        ]}
        selectedId="c1"
        onSelect={() => {}}
        hasMore
        onLoadMore={onLoadMore}
        filtered={false}
      />,
    );
    expect(screen.getByText("VIP")).toBeInTheDocument();
    expect(screen.getByText(/Ravi/)).toBeInTheDocument();
    expect(screen.getByText(/window closed/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(onLoadMore).toHaveBeenCalledOnce();
  });

  it("distinguishes an empty business from an empty filter result", () => {
    const props = { selectedId: null, onSelect: () => {}, hasMore: false, onLoadMore: () => {} };
    const { rerender } = render(<ConversationList conversations={[]} filtered={false} {...props} />);
    expect(screen.getByText(/No conversations yet/)).toBeInTheDocument();
    rerender(<ConversationList conversations={[]} filtered {...props} />);
    expect(screen.getByText(/match these filters/)).toBeInTheDocument();
  });
});
