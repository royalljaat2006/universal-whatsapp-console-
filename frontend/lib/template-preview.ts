import type { TemplateParameterSpec } from "@/types/api";

/**
 * Local preview of a template body with the values typed so far. This is only
 * a preview — the backend builds and validates the real payload
 * (apps/templates/parameters.py) and is the source of truth.
 */
export function renderPreview(text: string, values: Record<string, string>): string {
  return text.replace(/\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g, (match, key: string) => {
    const value = values[key];
    return value ? value : match;
  });
}

/** Parameter field id used for form state: component + key (+ button index). */
export function parameterField(spec: TemplateParameterSpec): string {
  return spec.component === "button" ? `button:${spec.button_index}` : `${spec.component}:${spec.key}`;
}

/** Path the backend uses for this parameter in TEMPLATE_PARAMETERS_INVALID details.fields. */
export function serverPath(spec: TemplateParameterSpec): string {
  if (spec.component === "header") return spec.kind === "text" ? "header.text" : "header.media_asset_id";
  if (spec.component === "body") return `body.${spec.key}`;
  return `buttons.${spec.key}`;
}

export interface TemplateFormValues {
  [field: string]: string;
}

/** Convert form values into the backend's parameter shape. */
export function toParameters(specs: TemplateParameterSpec[], values: TemplateFormValues) {
  const out: {
    header?: { text?: string; media_asset_id?: string };
    body?: Record<string, string>;
    buttons?: Record<string, string>;
  } = {};
  for (const spec of specs) {
    const value = values[parameterField(spec)] ?? "";
    if (spec.component === "header") {
      out.header = spec.kind === "text" ? { text: value } : { media_asset_id: value };
    } else if (spec.component === "body") {
      out.body = { ...out.body, [spec.key]: value };
    } else {
      out.buttons = { ...out.buttons, [String(spec.button_index)]: value };
    }
  }
  return out;
}

export function missingParameters(specs: TemplateParameterSpec[], values: TemplateFormValues): string[] {
  return specs.filter((spec) => !(values[parameterField(spec)] ?? "").trim()).map(parameterField);
}
