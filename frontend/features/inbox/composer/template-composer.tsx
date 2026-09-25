"use client";

import { useState } from "react";

import { ApiErrorNotice } from "@/components/status/api-error-notice";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { useApiQuery } from "@/hooks/use-api-query";
import type { ApiError } from "@/lib/api/errors";
import { formatBytes, toApiError } from "@/lib/format";
import {
  missingParameters,
  parameterField,
  renderPreview,
  serverPath,
  type TemplateFormValues,
  toParameters,
} from "@/lib/template-preview";
import { inboxService } from "@/services/inbox";
import { mediaService } from "@/services/media";
import { templateService } from "@/services/templates";
import type { InboxConversation, InboxMessage, MediaAsset, TemplateParameterSpec } from "@/types/api";

import { useSend } from "./use-send";

const MEDIA_ACCEPT: Record<string, string> = {
  image: "image/jpeg,image/png",
  video: "video/mp4,video/3gpp",
  document: "application/pdf,text/plain,.doc,.docx,.xls,.xlsx,.ppt,.pptx",
};

/**
 * Template send: only APPROVED templates synced from the number's WABA are
 * offered (the backend enforces the same rule). Parameters are sent as plain
 * values; the backend builds the Meta components and validates every field.
 */
export function TemplateComposer({
  conversation,
  businessAccountId,
  onQueued,
}: {
  conversation: InboxConversation;
  businessAccountId: string | undefined;
  onQueued: (message: InboxMessage) => void;
}) {
  const [templateId, setTemplateId] = useState("");
  const [values, setValues] = useState<TemplateFormValues>({});
  const [headerAsset, setHeaderAsset] = useState<MediaAsset | null>(null);
  const [uploadError, setUploadError] = useState<ApiError | null>(null);
  const [uploading, setUploading] = useState(false);
  const { send, sending, error } = useSend(onQueued);

  const templates = useApiQuery(
    () => templateService.list({ sendable: "1", business_account: businessAccountId }),
    [businessAccountId],
    Boolean(businessAccountId),
  );
  const detail = useApiQuery(() => templateService.get(templateId), [templateId], Boolean(templateId));
  const structure = detail.data?.id === templateId ? detail.data.structure : undefined;
  const specs: TemplateParameterSpec[] = structure?.supported ? structure.parameters : [];

  const fieldErrors =
    (error?.code === "TEMPLATE_PARAMETERS_INVALID"
      ? (error.details.fields as Record<string, string> | undefined)
      : undefined) ?? {};

  function choose(id: string) {
    setTemplateId(id);
    setValues({});
    setHeaderAsset(null);
  }

  async function uploadHeader(spec: TemplateParameterSpec, file: File | undefined) {
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      const asset = await mediaService.upload(file);
      setHeaderAsset(asset);
      setValues((v) => ({ ...v, [parameterField(spec)]: asset.id }));
    } catch (cause) {
      setUploadError(toApiError(cause));
    } finally {
      setUploading(false);
    }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!templateId || !structure?.supported) return;
    const ok = await send((key) =>
      inboxService.sendTemplate(conversation.id, templateId, toParameters(specs, values), key),
    );
    if (ok) choose("");
  }

  if (!businessAccountId) {
    return (
      <p className="text-muted-foreground text-xs">Loading the number&apos;s WhatsApp Business Account…</p>
    );
  }
  if (templates.error) return <ApiErrorNotice error={templates.error} />;
  if (templates.data && templates.data.results.length === 0) {
    return (
      <p className="text-muted-foreground text-xs">
        No approved templates are synced for this number&apos;s WABA. Sync templates on the Templates page
        after they are approved in WhatsApp Manager.
      </p>
    );
  }

  const bodyValues: Record<string, string> = {};
  const headerValues: Record<string, string> = {};
  for (const spec of specs) {
    if (spec.component === "body") bodyValues[spec.key] = values[parameterField(spec)] ?? "";
    if (spec.component === "header" && spec.kind === "text") {
      headerValues[spec.key] = values[parameterField(spec)] ?? "";
    }
  }
  const missing = missingParameters(specs, values);

  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      {error && error.code !== "TEMPLATE_PARAMETERS_INVALID" ? <ApiErrorNotice error={error} /> : null}
      {uploadError ? <ApiErrorNotice error={uploadError} /> : null}
      <NativeSelect aria-label="Template" value={templateId} onChange={(e) => choose(e.target.value)}>
        <option value="">Choose an approved template…</option>
        {templates.data?.results.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name} · {t.language} · {t.category}
          </option>
        ))}
      </NativeSelect>

      {templateId && detail.loading ? (
        <p className="text-muted-foreground text-xs">Loading template…</p>
      ) : null}
      {structure && !structure.supported ? (
        <p className="text-destructive text-xs">
          This template cannot be sent from the console: {structure.reason}
        </p>
      ) : null}

      {structure?.supported ? (
        <div className="grid gap-3 md:grid-cols-2">
          <div className="flex flex-col gap-2">
            {specs.length === 0 ? (
              <p className="text-muted-foreground text-xs">This template has no variables.</p>
            ) : null}
            {specs.map((spec) => {
              const field = parameterField(spec);
              const serverError = fieldErrors[serverPath(spec)];
              const label =
                spec.component === "button"
                  ? `Button ${Number(spec.button_index) + 1} URL suffix`
                  : spec.component === "header"
                    ? `Header ${spec.kind === "text" ? `{{${spec.key}}}` : spec.kind}`
                    : `Body {{${spec.key}}}`;
              return (
                <label key={field} className="flex flex-col gap-1 text-xs">
                  <span className="font-medium">{label}</span>
                  {spec.kind === "text" ? (
                    <Input
                      value={values[field] ?? ""}
                      maxLength={1024}
                      placeholder={spec.example ? `e.g. ${spec.example}` : ""}
                      aria-invalid={Boolean(serverError)}
                      onChange={(e) => setValues((v) => ({ ...v, [field]: e.target.value }))}
                    />
                  ) : (
                    <span className="flex flex-wrap items-center gap-2">
                      <input
                        type="file"
                        accept={MEDIA_ACCEPT[spec.kind]}
                        aria-label={`Header ${spec.kind}`}
                        onChange={(e) => void uploadHeader(spec, e.target.files?.[0])}
                        disabled={uploading}
                      />
                      {uploading ? <span>Uploading…</span> : null}
                      {headerAsset ? (
                        <span>
                          {headerAsset.original_filename} · {formatBytes(headerAsset.file_size)}
                        </span>
                      ) : null}
                    </span>
                  )}
                  {serverError ? <span className="text-destructive">{serverError}</span> : null}
                </label>
              );
            })}
          </div>
          <div className="bg-muted flex flex-col gap-1 rounded-md p-3 text-xs">
            <span className="text-muted-foreground font-medium">Preview</span>
            {structure.header_format === "TEXT" && structure.header_text ? (
              <p className="font-semibold">{renderPreview(structure.header_text, headerValues)}</p>
            ) : structure.header_format ? (
              <p className="italic">[{structure.header_format.toLowerCase()} header]</p>
            ) : null}
            <p className="whitespace-pre-wrap">{renderPreview(structure.body_text, bodyValues)}</p>
            {structure.footer_text ? <p className="text-muted-foreground">{structure.footer_text}</p> : null}
            {structure.buttons.map((b, i) => (
              <p key={i} className="rounded border px-2 py-0.5 text-center">
                {b.text}
              </p>
            ))}
          </div>
        </div>
      ) : null}

      <div className="flex justify-end">
        <Button
          type="submit"
          disabled={!structure?.supported || sending || uploading || missing.length > 0}
          title={missing.length ? "Fill every variable first" : undefined}
        >
          {sending ? "Sending…" : "Send template"}
        </Button>
      </div>
    </form>
  );
}
