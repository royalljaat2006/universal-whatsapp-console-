import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";
import { UiModeProvider, resetModeForTests, writeStoredMode } from "@/stores/ui-mode";
import type { StudioOptions, TemplateDraft, TemplateGuidance } from "@/types/api";

import { DraftsPanel } from "./drafts-panel";
import { GuidancePanel } from "./guidance-panel";
import { TemplateStudio } from "./template-studio";

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

const validate = vi.fn();
const saveDraft = vi.fn();
const updateDraft = vi.fn();
const submit = vi.fn();
const check = vi.fn();
const getDraft = vi.fn();
vi.mock("@/services/templates", () => ({
  templateStudioService: {
    validate: (...a: unknown[]) => validate(...a),
    saveDraft: (...a: unknown[]) => saveDraft(...a),
    updateDraft: (...a: unknown[]) => updateDraft(...a),
    submit: (...a: unknown[]) => submit(...a),
    check: (...a: unknown[]) => check(...a),
    draft: (...a: unknown[]) => getDraft(...a),
  },
}));

const OPTIONS: StudioOptions = {
  categories: [
    { value: "UTILITY", label: "Update about something the customer did", when: "Order updates.", supported: true },
    { value: "MARKETING", label: "Promotion or offer", when: "Sales.", supported: true },
    {
      value: "AUTHENTICATION",
      label: "One-time code",
      when: "Login codes.",
      supported: false,
      note: "Authentication templates are not supported in the Studio yet. Create them in WhatsApp Manager.",
    },
  ],
  languages: [
    { value: "en_US", label: "English (US)" },
    { value: "hi", label: "Hindi" },
  ],
  parameter_formats: ["POSITIONAL", "NAMED"],
  button_types: [
    { value: "QUICK_REPLY", label: "Quick reply", max: 10 },
    { value: "URL", label: "Website", max: 2 },
    { value: "PHONE_NUMBER", label: "Call", max: 1 },
  ],
  limits: { header: 60, body: 1024, footer: 60, buttons: 10, button_text: 25, review_hours: 24 },
  unsupported: ["Authentication templates"],
  starters: [
    {
      id: "order_shipped",
      title: "Order has shipped",
      category: "UTILITY",
      content: {
        header_text: "",
        body: "Hi {{1}}, your order {{2}} has shipped.",
        examples: { "1": "Asha", "2": "ORD-1" },
        footer: "",
        buttons: [],
      },
    },
  ],
  source: "test",
};

const ACCOUNT = { id: "a1", name: "Kiosk WABA", external_id: "123" } as never;

const checks = (over: Record<string, unknown> = {}) => ({
  ok: true,
  errors: [],
  warnings: [{ code: "meta_review", field: "review", message: "Meta reviews every template, usually within 24 hours." }],
  counts: { body: { used: 40, limit: 1024 } },
  preview: { header: "", body: "Hi Asha, your order ORD-1 has shipped.", footer: "", buttons: [], variables: ["1", "2"] },
  components: [{ type: "BODY", text: "Hi {{1}}, your order {{2}} has shipped." }],
  ...over,
});

const guidance = (over: Partial<TemplateGuidance> = {}): TemplateGuidance => ({
  state: "draft",
  meta_status: "",
  verified_by_meta: false,
  explanation: "This is a draft. Meta has not seen it.",
  next_step: "Finish it.",
  reason: null,
  can_send: false,
  can_fix_as_new: false,
  links: [],
  review_note: "",
  ...over,
});

const draftOf = (over: Partial<TemplateDraft> = {}): TemplateDraft => ({
  id: "d1",
  business_account_id: "a1",
  name: "order_shipped",
  language: "en_US",
  category: "UTILITY",
  parameter_format: "POSITIONAL",
  content: { header_text: "", body: "Hi {{1}}, your order {{2}} has shipped.", examples: { "1": "Asha", "2": "ORD-1" }, footer: "", buttons: [] },
  status: "draft",
  status_label: "Draft",
  template_id: null,
  attempts: 0,
  submitted_at: null,
  last_error: "",
  last_error_code: "",
  updated_at: "2026-10-12T10:00:00Z",
  checks: { ok: true, errors: [], warnings: [], counts: {}, preview: { header: "", body: "", footer: "", buttons: [], variables: [] } },
  content_hash: "hash-1",
  can_submit: true,
  guidance: guidance(),
  ...over,
});

function renderStudio(props: Partial<React.ComponentProps<typeof TemplateStudio>> = {}) {
  return render(
    <UiModeProvider>
      <TemplateStudio
        accounts={[ACCOUNT]}
        options={OPTIONS}
        onClose={vi.fn()}
        onChanged={vi.fn()}
        {...props}
      />
    </UiModeProvider>,
  );
}

const next = () => fireEvent.click(screen.getByRole("button", { name: "Next" }));

beforeEach(() => {
  vi.clearAllMocks();
  resetModeForTests();
  window.localStorage.clear();
  validate.mockResolvedValue(checks());
});

describe("TemplateStudio: guided steps", () => {
  it("starts with plain-language purposes, and says unsupported ones are unsupported", () => {
    renderStudio();
    expect(screen.getByRole("navigation", { name: "Template steps" })).toBeInTheDocument();
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(3);
    expect(screen.getByRole("radio", { name: /One-time code/ })).toBeDisabled();
    expect(screen.getByText(/not supported in the Studio yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled(); // no purpose chosen yet
  });

  it("an example starter fills in a purpose and the message, but is only a starting point", () => {
    renderStudio();
    expect(screen.getByText(/Meta still reviews it/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Order has shipped" }));
    expect(screen.getByRole("radio", { name: /Update about something/ })).toBeChecked();
    expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
  });

  async function toWriteStep() {
    renderStudio();
    fireEvent.click(screen.getByRole("radio", { name: /Update about something/ }));
    next();
    fireEvent.change(screen.getByLabelText("Template name"), { target: { value: "Order Shipped!" } });
    next();
    expect(await screen.findByLabelText("Your message")).toBeInTheDocument();
  }

  it("normalises the name as the person types and shows the server's rule messages", async () => {
    validate.mockResolvedValue(
      checks({ ok: false, errors: [{ code: "name_taken", field: "name", message: "This account already has a template with this name." }] }),
    );
    renderStudio();
    fireEvent.click(screen.getByRole("radio", { name: /Update about something/ }));
    next();
    const name = screen.getByLabelText("Template name");
    fireEvent.change(name, { target: { value: "Order Shipped!" } });
    expect(name).toHaveValue("order_shipped_");
    expect(await screen.findByText(/already has a template with this name/)).toBeInTheDocument();
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("'Add a variable' adds the next number with no gaps and asks for an example", async () => {
    await toWriteStep();
    const body = screen.getByLabelText("Your message");
    fireEvent.change(body, { target: { value: "Hi " } });
    fireEvent.click(screen.getByRole("button", { name: /Add a variable/ }));
    expect(body).toHaveValue("Hi {{1}}");
    fireEvent.change(body, { target: { value: "Hi {{1}}, your order is on its way " } });
    fireEvent.click(screen.getByRole("button", { name: /Add a variable/ }));
    expect(body).toHaveValue("Hi {{1}}, your order is on its way {{2}}");
    expect(screen.getByLabelText("Example for {{1}}")).toBeInTheDocument();
    expect(screen.getByLabelText("Example for {{2}}")).toBeInTheDocument();
  });

  it("shows live character counters and sends the examples with the checks", async () => {
    await toWriteStep();
    fireEvent.change(screen.getByLabelText("Your message"), { target: { value: "Hello there {{1}} welcome" } });
    fireEvent.change(screen.getByLabelText("Example for {{1}}"), { target: { value: "Asha" } });
    expect(screen.getByText(/25 \/ 1024 characters/)).toBeInTheDocument();
    await waitFor(() =>
      expect(validate).toHaveBeenLastCalledWith(
        expect.objectContaining({
          business_account_id: "a1",
          name: "order_shipped_",
          content: expect.objectContaining({ examples: { "1": "Asha" } }),
        }),
      ),
    );
    expect(await screen.findByRole("region", { name: "Preview" })).toHaveTextContent("Hi Asha, your order ORD-1 has shipped.");
  });

  it("stale example values for removed variables are not sent", async () => {
    await toWriteStep();
    const body = screen.getByLabelText("Your message");
    fireEvent.change(body, { target: { value: "Hi {{1}} welcome" } });
    fireEvent.change(screen.getByLabelText("Example for {{1}}"), { target: { value: "Asha" } });
    fireEvent.change(body, { target: { value: "Hi there welcome" } });
    await waitFor(() =>
      expect(validate).toHaveBeenLastCalledWith(expect.objectContaining({ content: expect.objectContaining({ examples: {} }) })),
    );
  });

  it("the check step lists problems and blocks going on until they are fixed", async () => {
    validate.mockResolvedValue(
      checks({
        ok: false,
        errors: [{ code: "variable_at_edge", field: "body", message: "A message cannot start or end with a variable." }],
      }),
    );
    await toWriteStep();
    fireEvent.change(screen.getByLabelText("Your message"), { target: { value: "{{1}} hi" } });
    await screen.findByText(/cannot start or end with a variable/);
    next();
    const section = await screen.findByRole("region", { name: "Things to fix" });
    expect(within(section).getByText(/cannot start or end with a variable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });
});

describe("TemplateStudio: explicit confirmation before anything goes to Meta", () => {
  async function toReview() {
    renderStudio({ initialDraft: draftOf() });
    next(); // write -> check
    await screen.findByText(/No problems found/);
    next(); // check -> review
    return screen.findByRole("region", { name: "What happens next" });
  }

  it("the review step says it is not approved and needs a ticked confirmation", async () => {
    const section = await toReview();
    expect(within(section).getByText(/NOT approved when you send it/)).toBeInTheDocument();
    expect(within(section).getByText(/usually decides within 24 hours/)).toBeInTheDocument();
    const send = screen.getByRole("button", { name: "Send to Meta for review" });
    expect(send).toBeDisabled();
    fireEvent.click(send);
    expect(submit).not.toHaveBeenCalled();
    expect(saveDraft).not.toHaveBeenCalled();
  });

  it("saving a draft never submits", async () => {
    updateDraft.mockResolvedValue(draftOf());
    await toReview();
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(updateDraft).toHaveBeenCalledTimes(1));
    expect(toast.success).toHaveBeenCalledWith("Draft saved", "Nothing was sent to Meta.");
    expect(submit).not.toHaveBeenCalled();
  });

  it("after confirming it saves, then submits with the hash of exactly what was reviewed, and says it is not an approval", async () => {
    updateDraft.mockResolvedValue(draftOf({ content_hash: "reviewed-hash" }));
    submit.mockResolvedValue(
      draftOf({
        status: "submitted",
        content_hash: "reviewed-hash",
        note: "Sent to Meta. This is NOT an approval.",
        guidance: guidance({ state: "pending", meta_status: "PENDING", explanation: "Meta is reviewing this template." }),
      }),
    );
    await toReview();
    fireEvent.click(screen.getByLabelText(/I understand this sends the template to Meta/));
    fireEvent.click(screen.getByRole("button", { name: "Send to Meta for review" }));

    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit).toHaveBeenCalledWith("d1", "reviewed-hash");
    expect(updateDraft.mock.invocationCallOrder[0]).toBeLessThan(submit.mock.invocationCallOrder[0]);
    expect(toast.info).toHaveBeenCalledWith("Sent to Meta for review", expect.stringContaining("NOT an approval"));
    expect(toast.success).not.toHaveBeenCalled();
    // The studio now shows only what Meta reported: pending, not yet confirmed.
    expect(await screen.findByText("In review at Meta")).toBeInTheDocument();
    expect(screen.getByText("Not yet confirmed by Meta")).toBeInTheDocument();
    expect(screen.queryByText("Approved")).not.toBeInTheDocument();
  });

  it("a refusal from the server (Meta rejected the request) is shown and nothing is claimed", async () => {
    updateDraft.mockResolvedValue(draftOf());
    submit.mockRejectedValue(new ApiError(502, "TEMPLATE_SUBMIT_FAILED", "Meta did not accept the template: Invalid parameter"));
    getDraft.mockResolvedValue(draftOf({ last_error: "Invalid parameter" }));
    await toReview();
    fireEvent.click(screen.getByLabelText(/I understand this sends the template to Meta/));
    fireEvent.click(screen.getByRole("button", { name: "Send to Meta for review" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid parameter");
    expect(toast.info).not.toHaveBeenCalled();
    expect(screen.queryByText("Sent to Meta")).not.toBeInTheDocument();
  });

  it("a stale-content refusal (409) is shown so the person can review again", async () => {
    updateDraft.mockResolvedValue(draftOf());
    submit.mockRejectedValue(new ApiError(409, "DRAFT_CONFLICT", "This template changed since you reviewed it."));
    getDraft.mockResolvedValue(draftOf());
    await toReview();
    fireEvent.click(screen.getByLabelText(/I understand this sends the template to Meta/));
    fireEvent.click(screen.getByRole("button", { name: "Send to Meta for review" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("changed since you reviewed it");
  });
});

describe("Simple and Advanced", () => {
  it("Simple hides the technical view; Advanced shows the variable style and what would be sent", async () => {
    const { unmount } = renderStudio({ initialDraft: draftOf() });
    next();
    await screen.findByText(/No problems found/);
    expect(screen.queryByLabelText("Variable style")).not.toBeInTheDocument();
    expect(screen.queryByText("What would be sent to Meta")).not.toBeInTheDocument();
    unmount();

    writeStoredMode("advanced");
    renderStudio({ initialDraft: draftOf() });
    next();
    expect(await screen.findByLabelText("Variable style")).toBeInTheDocument();
    expect(screen.getByText("What would be sent to Meta")).toBeInTheDocument();
  });
});

describe("a draft that was already sent", () => {
  it("is read-only and shows only Meta's reported status, with a way to ask Meta again", async () => {
    check.mockResolvedValue({ status: "queued", note: "Asking Meta for the latest status." });
    renderStudio({
      initialDraft: draftOf({
        status: "submitted",
        guidance: guidance({ state: "submitted", explanation: "The console sent this to Meta, but Meta has not reported a status yet." }),
      }),
    });
    expect(screen.getByText("Sent to Meta")).toBeInTheDocument();
    expect(screen.getByText("Not yet confirmed by Meta")).toBeInTheDocument();
    expect(screen.queryByLabelText("Your message")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Send to Meta for review/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Check with Meta" }));
    await waitFor(() => expect(check).toHaveBeenCalledWith("d1"));
    expect(toast.info).toHaveBeenCalledWith("Asking Meta", expect.any(String));
  });

  it("an unconfirmed draft explains that it cannot be sent again yet", () => {
    renderStudio({
      initialDraft: draftOf({
        status: "unconfirmed",
        guidance: guidance({
          state: "submitted",
          explanation: "The console is not sure Meta received this template: the request did not finish.",
          next_step: "Use \"Check with Meta\". It cannot be submitted again until that is settled.",
        }),
      }),
    });
    expect(screen.getByText(/not sure Meta received this template/)).toBeInTheDocument();
    expect(screen.getByText(/cannot be submitted again until that is settled/)).toBeInTheDocument();
  });
});

describe("GuidancePanel", () => {
  it("shows Meta's reason verbatim with plain help for a rejection, and an official link in a new tab", () => {
    render(
      <GuidancePanel
        guidance={guidance({
          state: "rejected",
          meta_status: "REJECTED",
          verified_by_meta: true,
          explanation: "Meta rejected this template, so it cannot be sent.",
          reason: { code: "INVALID_FORMAT", meaning: "Meta says the template is formatted wrongly.", how_to_fix: "Check every {{variable}}." },
          can_fix_as_new: true,
          links: [{ label: "Open WhatsApp Manager (Meta)", url: "https://business.facebook.com/latest/whatsapp_manager" }],
        })}
        action={<button>Create a corrected copy</button>}
      />,
    );
    expect(screen.getByText("Rejected")).toBeInTheDocument();
    expect(screen.getByText("Confirmed by Meta")).toBeInTheDocument();
    expect(screen.getByText("INVALID_FORMAT")).toBeInTheDocument();
    expect(screen.getByText(/Check every \{\{variable\}\}/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Open WhatsApp Manager/ });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(screen.getByRole("button", { name: "Create a corrected copy" })).toBeInTheDocument();
  });

  it("an approved status that Meta has not confirmed yet is labelled as such", () => {
    render(
      <GuidancePanel
        guidance={guidance({ state: "approved", meta_status: "APPROVED", verified_by_meta: false, explanation: "Meta approved this template." })}
      />,
    );
    expect(screen.getByText("Not yet confirmed by Meta")).toBeInTheDocument();
  });

  it("an unrecognised status is shown verbatim", () => {
    render(
      <GuidancePanel
        guidance={guidance({ state: "unknown", meta_status: "SOMETHING_NEW", explanation: "Meta reported a status this console does not recognise." })}
      />,
    );
    expect(screen.getByText("Status unclear")).toBeInTheDocument();
    expect(screen.getByText("Meta says: SOMETHING_NEW")).toBeInTheDocument();
  });
});

describe("DraftsPanel", () => {
  it("lists drafts with their real state and only offers Continue/Delete for unsent drafts", () => {
    const onOpen = vi.fn();
    const onDelete = vi.fn();
    render(
      <DraftsPanel
        canManage
        onOpen={onOpen}
        onDelete={onDelete}
        drafts={[
          draftOf({ id: "d1", name: "first_one" }),
          draftOf({ id: "d2", name: "second_one", status: "submitted", guidance: guidance({ state: "pending" }) }),
        ]}
      />,
    );
    expect(screen.getByRole("button", { name: /Continue first_one/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Delete first_one/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /View second_one/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete second_one/ })).not.toBeInTheDocument();
    expect(screen.getByText("In review at Meta")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Delete first_one/ }));
    expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ id: "d1" }));
  });

  it("without manage permission drafts can only be viewed", () => {
    render(<DraftsPanel canManage={false} onOpen={vi.fn()} onDelete={vi.fn()} drafts={[draftOf()]} />);
    expect(screen.getByRole("button", { name: /View order_shipped/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
  });

  it("renders nothing when there are no drafts", () => {
    const { container } = render(<DraftsPanel canManage onOpen={vi.fn()} onDelete={vi.fn()} drafts={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

// keep act referenced for fake-timer based future cases
void act;
