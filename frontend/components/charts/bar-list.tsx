/** Horizontal bars for a ranked list of real values (e.g. template usage). */
export function BarList({
  items,
  valueLabel = (v: number) => v.toLocaleString(),
  ariaLabel,
}: {
  items: { key: string; label: string; value: number; hint?: string }[];
  valueLabel?: (value: number) => string;
  ariaLabel: string;
}) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <ul aria-label={ariaLabel} className="flex flex-col gap-1.5">
      {items.map((item) => (
        <li key={item.key} className="flex flex-col gap-0.5 text-xs">
          <span className="flex justify-between gap-2">
            <span className="truncate">{item.label}</span>
            <span className="font-medium tabular-nums">{valueLabel(item.value)}</span>
          </span>
          <span className="bg-muted h-2 overflow-hidden rounded">
            <span
              className="bg-primary block h-full rounded"
              style={{ width: `${(item.value / max) * 100}%` }}
            />
          </span>
          {item.hint ? <span className="text-muted-foreground">{item.hint}</span> : null}
        </li>
      ))}
    </ul>
  );
}
