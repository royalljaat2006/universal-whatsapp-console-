"use client";

import { PlusIcon, XIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { LoadingButton } from "@/components/ui/spinner";
import { useToast } from "@/components/ui/toast";
import { toApiError } from "@/lib/format";
import { cn } from "@/lib/utils";
import { templateStudioService } from "@/services/templates";
import { useUiMode } from "@/stores/ui-mode";
import type {
  BusinessAccount,
  StudioButton,
  StudioChecks,
  StudioContent,
  StudioIssue,
  StudioOptions,
  TemplateDraft,
} from "@/types/api";

import { GuidancePanel } from "./guidance-panel";
import {
  emptyContent,
  fieldIssues,
  newButton,
  nextVariableNumber,
  pruneExamples,
  variableKeys,
} from "./model";
import { TemplatePreview } from "./template-preview";

const STEPS = ["Purpose", "Name and language", "Write it", "Check it", "Review and send"] as const;
const VALIDATE_DELAY_MS = 350;

function Issues({ issues, id }: { issues: StudioIssue[]; id: string }) {
  if (!issues.length) return null;
  return (
    <ul id={id} className="text-destructive mt-1 flex flex-col gap-0.5 text-sm">
      {issues.map((i) => (
        <li key={`${i.code}-${i.field}`}>{i.message}</li>
      ))}
    </ul>
  );
}

function Counter({ used, limit, label }: { used: number; limit: number; label: string }) {
  return (
    <span className={cn("text-caption", used > limit && "text-destructive font-medium")}>
      {used} / {limit} characters{" "}<span className="sr-only">in the {label}</span>
    </span>
  );
}

/** Read-only view for a draft that has already been sent to Meta: only Meta's reported status matters now. */
function SentView({
  draft,
  onChanged,
}: {
  draft: TemplateDraft;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [checking, setChecking] = useState(false);
  async function check() {
    setChecking(true);
    try {
      const result = await templateStudioService.check(draft.id);
      toast.info("Asking Meta", result.note);
      setTimeout(onChanged, 3000);
    } catch (cause) {
      toast.error("Couldn't ask Meta", toApiError(cause).message);
    } finally {
      setChecking(false);
    }
  }
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm">
        <span className="font-medium">{draft.name}</span> ({draft.language})
      </p>
      <GuidancePanel
        guidance={draft.guidance}
        action={
          <LoadingButton size="sm" variant="outline" loading={checking} onClick={() => void check()} className="w-fit">
            Check with Meta
          </LoadingButton>
        }
      />
      {draft.last_error ? <p className="text-supporting">{draft.last_error}</p> : null}
    </div>
  );
}

export function TemplateStudio({
  accounts,
  options,
  initialDraft,
  onClose,
  onChanged,
}: {
  accounts: BusinessAccount[];
  options: StudioOptions;
  initialDraft?: TemplateDraft;
  onClose: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { mode } = useUiMode();
  const advanced = mode === "advanced";

  const [step, setStep] = useState(initialDraft ? 2 : 0);
  const [draft, setDraft] = useState<TemplateDraft | undefined>(initialDraft);
  const [accountId, setAccountId] = useState(initialDraft?.business_account_id ?? accounts[0]?.id ?? "");
  const [name, setName] = useState(initialDraft?.name ?? "");
  const [language, setLanguage] = useState(initialDraft?.language ?? "en_US");
  const [category, setCategory] = useState(initialDraft?.category ?? "");
  const [format, setFormat] = useState<"POSITIONAL" | "NAMED">(initialDraft?.parameter_format ?? "POSITIONAL");
  const [content, setContent] = useState<StudioContent>(initialDraft?.content ?? emptyContent());
  const [checks, setChecks] = useState<StudioChecks | undefined>(initialDraft?.checks);
  const [components, setComponents] = useState<Record<string, unknown>[] | undefined>();
  const [confirmed, setConfirmed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  // Live checks: the SERVER applies Meta's documented rules; nothing is saved or sent by this call.
  useEffect(() => {
    if (!accountId) return;
    const mine = ++seq.current;
    const timer = setTimeout(() => {
      templateStudioService
        .validate({
          business_account_id: accountId,
          name,
          language,
          category,
          parameter_format: format,
          content: pruneExamples(content),
        })
        .then((result) => {
          if (mine !== seq.current) return;
          setChecks(result);
          setComponents(result.components);
        })
        .catch(() => {
          if (mine === seq.current) setChecks(undefined);
        });
    }, VALIDATE_DELAY_MS);
    return () => clearTimeout(timer);
  }, [accountId, name, language, category, format, content]);

  const errors = checks?.errors ?? [];
  const warnings = checks?.warnings ?? [];
  const bodyKeys = useMemo(() => variableKeys(content.body), [content.body]);
  const headerKeys = useMemo(() => variableKeys(content.header_text), [content.header_text]);
  const exampleKeys = [...headerKeys, ...bodyKeys.filter((k) => !headerKeys.includes(k))];

  if (draft && draft.status !== "draft") {
    return <SentView draft={draft} onChanged={() => {
      onChanged();
      templateStudioService.draft(draft.id).then(setDraft).catch(() => undefined);
    }} />;
  }

  function patchContent(changes: Partial<StudioContent>) {
    setConfirmed(false);
    setContent((c) => ({ ...c, ...changes }));
  }

  function setButton(index: number, changes: Partial<StudioButton>) {
    patchContent({ buttons: content.buttons.map((b, i) => (i === index ? { ...b, ...changes } : b)) });
  }

  async function save(): Promise<TemplateDraft | undefined> {
    const body = {
      name,
      language,
      category,
      parameter_format: format,
      content: pruneExamples(content),
    };
    const saved = draft
      ? await templateStudioService.updateDraft(draft.id, body)
      : await templateStudioService.saveDraft({ business_account_id: accountId, ...body });
    setDraft(saved);
    onChanged();
    return saved;
  }

  async function saveOnly() {
    setSaving(true);
    setError(null);
    try {
      await save();
      toast.success("Draft saved", "Nothing was sent to Meta.");
    } catch (cause) {
      setError(toApiError(cause).message);
    } finally {
      setSaving(false);
    }
  }

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      const saved = await save();
      if (!saved) return;
      const sent = await templateStudioService.submit(saved.id, saved.content_hash);
      setDraft(sent);
      toast.info("Sent to Meta for review", sent.note ?? "This is not an approval.");
      onChanged();
    } catch (cause) {
      setError(toApiError(cause).message);
      if (draft) templateStudioService.draft(draft.id).then(setDraft).catch(() => undefined);
    } finally {
      setSubmitting(false);
    }
  }

  const stepErrorsBlock =
    step === 0
      ? !category || errors.some((e) => e.field === "category")
      : step === 1
        ? !name || !language || errors.some((e) => ["name", "language"].includes(e.field))
        : step === 2
          ? !content.body.trim()
          : step === 3
            ? errors.length > 0 || !checks
            : false;

  return (
    <div className="flex flex-col gap-5">
      <nav aria-label="Template steps">
        <ol className="flex flex-wrap gap-x-4 gap-y-1">
          {STEPS.map((label, i) => (
            <li key={label} aria-current={i === step ? "step" : undefined}>
              <span className={cn("text-sm", i === step ? "font-semibold" : "text-muted-foreground")}>
                {i + 1}. {label}
              </span>
            </li>
          ))}
        </ol>
      </nav>

      {error ? (
        <p role="alert" className="border-destructive/25 bg-danger-soft rounded-md border px-3 py-2 text-sm">
          {error}
        </p>
      ) : null}

      {step === 0 ? (
        <fieldset className="flex flex-col gap-3">
          <legend className="text-section-title mb-1">What is this message for?</legend>
          {options.categories.map((c) => (
            <label
              key={c.value}
              className={cn(
                "flex items-start gap-3 rounded-md border p-3 text-sm",
                !c.supported && "opacity-70",
                category === c.value && "border-primary bg-brand-soft/30",
              )}
            >
              <input
                type="radio"
                name="category"
                className="mt-1"
                disabled={!c.supported}
                checked={category === c.value}
                onChange={() => {
                  setCategory(c.value);
                  setConfirmed(false);
                }}
              />
              <span>
                <span className="block font-medium">{c.label}</span>
                <span className="text-supporting block">{c.when}</span>
                {!c.supported && c.note ? <span className="text-caption block">{c.note}</span> : null}
              </span>
            </label>
          ))}
          <div>
            <p className="text-sm font-medium">Or start from an example</p>
            <p className="text-caption">
              Examples are only a starting point. You can change everything, and Meta still reviews it.
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              {options.starters.map((s) => (
                <Button
                  key={s.id}
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setCategory(s.category);
                    setContent(s.content);
                    setFormat("POSITIONAL");
                    if (!name) setName(s.id);
                    setConfirmed(false);
                  }}
                >
                  {s.title}
                </Button>
              ))}
            </div>
          </div>
        </fieldset>
      ) : null}

      {step === 1 ? (
        <div className="flex flex-col gap-4">
          {accounts.length > 1 ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="studio-account">WhatsApp account</Label>
              <NativeSelect id="studio-account" value={accountId} onChange={(e) => setAccountId(e.target.value)} disabled={Boolean(draft)}>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name || a.external_id}
                  </option>
                ))}
              </NativeSelect>
            </div>
          ) : null}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="studio-name">Template name</Label>
            <Input
              id="studio-name"
              value={name}
              placeholder="order_shipped"
              aria-describedby="studio-name-help studio-name-errors"
              aria-invalid={fieldIssues(errors, "name").length > 0}
              onChange={(e) => {
                setName(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"));
                setConfirmed(false);
              }}
            />
            <p id="studio-name-help" className="text-caption">
              Only lowercase letters, numbers and underscores. Customers never see this name.
            </p>
            <Issues id="studio-name-errors" issues={fieldIssues(errors, "name")} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="studio-language">Language the message is written in</Label>
            <NativeSelect id="studio-language" value={language} onChange={(e) => setLanguage(e.target.value)}>
              {options.languages.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </NativeSelect>
            <p className="text-caption">Meta does not translate your text. Write it in this language.</p>
            <Issues id="studio-language-errors" issues={fieldIssues(errors, "language")} />
          </div>
        </div>
      ) : null}

      {step === 2 ? (
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="studio-header">Header (optional)</Label>
            <Input
              id="studio-header"
              value={content.header_text}
              aria-describedby="studio-header-errors"
              onChange={(e) => patchContent({ header_text: e.target.value })}
            />
            <Counter used={content.header_text.length} limit={options.limits.header} label="header" />
            <Issues id="studio-header-errors" issues={fieldIssues(errors, "header")} />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="studio-body">Your message</Label>
            <textarea
              id="studio-body"
              rows={6}
              value={content.body}
              aria-describedby="studio-body-help studio-body-errors"
              aria-invalid={fieldIssues(errors, "body").length > 0}
              placeholder="Hi {{1}}, your order {{2}} has shipped."
              onChange={(e) => patchContent({ body: e.target.value })}
              className="border-input focus-visible:ring-ring/50 min-h-28 w-full rounded-lg border bg-transparent px-2.5 py-1.5 text-sm outline-none focus-visible:ring-3"
            />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Counter used={content.body.length} limit={options.limits.body} label="message" />
              {format === "POSITIONAL" ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    patchContent({ body: `${content.body}{{${nextVariableNumber(content.body)}}}` })
                  }
                >
                  <PlusIcon aria-hidden /> Add a variable
                </Button>
              ) : null}
            </div>
            <p id="studio-body-help" className="text-caption">
              A variable is a part that changes for each person, written {"{{1}}"}, {"{{2}}"}… Do not start or end the
              message with one.
            </p>
            <Issues id="studio-body-errors" issues={[...fieldIssues(errors, "body").filter((e) => !e.field.includes("."))]} />
          </div>

          {exampleKeys.length ? (
            <fieldset className="flex flex-col gap-2 rounded-md border p-3">
              <legend className="px-1 text-sm font-medium">Example values</legend>
              <p className="text-caption">
                Meta needs an example for every variable so its reviewers can see what the message looks like.
              </p>
              {exampleKeys.map((key) => (
                <div key={key} className="flex flex-col gap-1">
                  <Label htmlFor={`example-${key}`}>Example for {`{{${key}}}`}</Label>
                  <Input
                    id={`example-${key}`}
                    value={content.examples[key] ?? ""}
                    onChange={(e) => patchContent({ examples: { ...content.examples, [key]: e.target.value } })}
                  />
                  <Issues
                    id={`example-${key}-errors`}
                    issues={errors.filter((i) => i.code === "example_missing" && i.field.endsWith(`.${key}`))}
                  />
                </div>
              ))}
            </fieldset>
          ) : null}

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="studio-footer">Footer (optional)</Label>
            <Input
              id="studio-footer"
              value={content.footer}
              aria-describedby="studio-footer-errors"
              onChange={(e) => patchContent({ footer: e.target.value })}
            />
            <Counter used={content.footer.length} limit={options.limits.footer} label="footer" />
            <Issues id="studio-footer-errors" issues={fieldIssues(errors, "footer")} />
          </div>

          <fieldset className="flex flex-col gap-2">
            <legend className="text-sm font-medium">Buttons (optional)</legend>
            {content.buttons.map((b, i) => (
              <div key={i} className="flex flex-col gap-2 rounded-md border p-3">
                <div className="flex items-end gap-2">
                  <div className="flex flex-1 flex-col gap-1">
                    <Label htmlFor={`button-type-${i}`}>Button {i + 1} type</Label>
                    <NativeSelect
                      id={`button-type-${i}`}
                      value={b.type}
                      onChange={(e) => setButton(i, { ...newButton(e.target.value as StudioButton["type"]), text: b.text })}
                    >
                      {options.button_types.map((t) => (
                        <option key={t.value} value={t.value}>
                          {t.label}
                        </option>
                      ))}
                    </NativeSelect>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label={`Remove button ${i + 1}`}
                    onClick={() => patchContent({ buttons: content.buttons.filter((_, idx) => idx !== i) })}
                  >
                    <XIcon aria-hidden />
                  </Button>
                </div>
                <div className="flex flex-col gap-1">
                  <Label htmlFor={`button-text-${i}`}>Button {i + 1} text</Label>
                  <Input id={`button-text-${i}`} value={b.text} onChange={(e) => setButton(i, { text: e.target.value })} />
                  <Counter used={b.text.length} limit={options.limits.button_text} label={`button ${i + 1}`} />
                </div>
                {b.type === "URL" ? (
                  <>
                    <div className="flex flex-col gap-1">
                      <Label htmlFor={`button-url-${i}`}>Web address</Label>
                      <Input id={`button-url-${i}`} value={b.url ?? ""} onChange={(e) => setButton(i, { url: e.target.value })} />
                    </div>
                    {(b.url ?? "").endsWith("{{1}}") ? (
                      <div className="flex flex-col gap-1">
                        <Label htmlFor={`button-url-example-${i}`}>Example for the changing part</Label>
                        <Input
                          id={`button-url-example-${i}`}
                          value={b.url_example ?? ""}
                          onChange={(e) => setButton(i, { url_example: e.target.value })}
                        />
                      </div>
                    ) : null}
                  </>
                ) : null}
                {b.type === "PHONE_NUMBER" ? (
                  <div className="flex flex-col gap-1">
                    <Label htmlFor={`button-phone-${i}`}>Phone number (with country code)</Label>
                    <Input
                      id={`button-phone-${i}`}
                      value={b.phone_number ?? ""}
                      inputMode="tel"
                      onChange={(e) => setButton(i, { phone_number: e.target.value })}
                    />
                  </div>
                ) : null}
                <Issues id={`button-${i}-errors`} issues={errors.filter((e) => e.field === `buttons.${i}`)} />
              </div>
            ))}
            {content.buttons.length < options.limits.buttons ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-fit"
                onClick={() => patchContent({ buttons: [...content.buttons, newButton("QUICK_REPLY")] })}
              >
                <PlusIcon aria-hidden /> Add a button
              </Button>
            ) : null}
            <Issues id="studio-buttons-errors" issues={errors.filter((e) => e.field === "buttons")} />
          </fieldset>
          <TemplatePreview preview={checks?.preview} />
        </div>
      ) : null}

      {step === 3 ? (
        <div className="flex flex-col gap-4">
          <div aria-live="polite">
            {errors.length ? (
              <section aria-label="Things to fix" className="border-destructive/25 bg-danger-soft/60 rounded-md border px-3 py-2">
                <p className="text-sm font-medium">
                  {errors.length} thing{errors.length === 1 ? "" : "s"} to fix before this can go to Meta
                </p>
                <ul className="mt-1 list-disc pl-5 text-sm">
                  {errors.map((e) => (
                    <li key={`${e.code}-${e.field}`}>{e.message}</li>
                  ))}
                </ul>
              </section>
            ) : checks ? (
              <p className="text-success text-sm font-medium">No problems found with Meta&apos;s documented rules.</p>
            ) : (
              <p className="text-supporting">Checking…</p>
            )}
          </div>
          {warnings.length ? (
            <section aria-label="Good to know">
              <p className="text-sm font-medium">Good to know</p>
              <ul className="text-supporting list-disc pl-5">
                {warnings.map((w) => (
                  <li key={`${w.code}-${w.field}`}>{w.message}</li>
                ))}
              </ul>
            </section>
          ) : null}
          <TemplatePreview preview={checks?.preview} />
          {advanced ? (
            <>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="studio-format">Variable style</Label>
                <NativeSelect id="studio-format" value={format} onChange={(e) => setFormat(e.target.value as "POSITIONAL" | "NAMED")}>
                  <option value="POSITIONAL">Numbered: {"{{1}}"}</option>
                  <option value="NAMED">Named: {"{{first_name}}"}</option>
                </NativeSelect>
              </div>
              <details>
                <summary className="text-supporting cursor-pointer text-sm">What would be sent to Meta</summary>
                <pre className="bg-muted mt-2 overflow-x-auto rounded-md p-3 text-xs">
                  {JSON.stringify(
                    { name, language, category, parameter_format: format, components },
                    null,
                    2,
                  )}
                </pre>
              </details>
            </>
          ) : null}
        </div>
      ) : null}

      {step === 4 ? (
        <div className="flex flex-col gap-4">
          <TemplatePreview preview={checks?.preview} />
          <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 text-sm">
            <dt className="text-muted-foreground">Name</dt>
            <dd>{name}</dd>
            <dt className="text-muted-foreground">Language</dt>
            <dd>{options.languages.find((l) => l.value === language)?.label ?? language}</dd>
            <dt className="text-muted-foreground">Category</dt>
            <dd>{options.categories.find((c) => c.value === category)?.label ?? category}</dd>
          </dl>
          <section aria-label="What happens next" className="rounded-md border px-3 py-3 text-sm">
            <p className="font-medium">What happens next</p>
            <ul className="text-supporting mt-1 list-disc pl-5">
              <li>The template is sent to Meta for review. Meta usually decides within {options.limits.review_hours} hours.</li>
              <li>It is NOT approved when you send it. You can only use it to send messages after Meta approves it.</li>
              <li>Meta decides whether it is allowed. This console cannot predict or change the result.</li>
              <li>After it is sent you cannot edit it here. If it is rejected, you can create a corrected copy.</li>
            </ul>
          </section>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={confirmed}
              disabled={errors.length > 0}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            <span>I understand this sends the template to Meta for review, and that it is not approved yet.</span>
          </label>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 border-t pt-4">
        {step > 0 ? (
          <Button type="button" variant="outline" onClick={() => setStep(step - 1)}>
            Back
          </Button>
        ) : null}
        {step < 4 ? (
          <Button type="button" disabled={stepErrorsBlock} onClick={() => setStep(step + 1)}>
            Next
          </Button>
        ) : (
          <LoadingButton
            loading={submitting}
            disabled={!confirmed || errors.length > 0 || !checks}
            onClick={() => void submit()}
          >
            Send to Meta for review
          </LoadingButton>
        )}
        {name && category ? (
          <LoadingButton type="button" variant="ghost" loading={saving} onClick={() => void saveOnly()}>
            Save draft
          </LoadingButton>
        ) : null}
        <Button type="button" variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
    </div>
  );
}
