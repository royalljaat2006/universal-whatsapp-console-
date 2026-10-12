import type { StudioChecks } from "@/types/api";

/** WhatsApp-style preview built from the server's own rendering of the template (examples filled in). */
export function TemplatePreview({ preview }: { preview: StudioChecks["preview"] | undefined }) {
  if (!preview) return null;
  const empty = !preview.body && !preview.header && !preview.footer;
  return (
    <section
      aria-label="Preview"
      className="rounded-lg bg-[oklch(0.95_0.02_150)] p-4"
    >
      <p className="text-caption mb-2">Preview with your example values</p>
      <div className="bg-surface max-w-sm rounded-xl rounded-tl-sm p-3 text-sm shadow-xs">
        {empty ? (
          <p className="text-muted-foreground">Your message will appear here as you write it.</p>
        ) : null}
        {preview.header ? <p className="mb-1 font-semibold">{preview.header}</p> : null}
        {preview.body ? <p className="leading-5 whitespace-pre-wrap">{preview.body}</p> : null}
        {preview.footer ? <p className="text-caption mt-2">{preview.footer}</p> : null}
      </div>
      {preview.buttons.length ? (
        <div className="mt-1.5 flex max-w-sm flex-col gap-1">
          {preview.buttons.map((b, i) => (
            <span
              key={`${b.type}-${i}`}
              className="bg-surface text-info rounded-lg px-3 py-1.5 text-center text-sm font-medium shadow-xs"
            >
              {b.text}
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
}
