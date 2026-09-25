"use client";

import { useId, useState } from "react";

export interface Series {
  name: string;
  color: string;
  values: number[];
}

/**
 * Minimal responsive SVG line chart for backend time series. It draws exactly
 * the values it is given (no smoothing, no interpolation of missing points)
 * and exposes the same numbers as a table for screen readers.
 */
export function LineChart({
  labels,
  series,
  height = 180,
  ariaLabel,
}: {
  labels: string[];
  series: Series[];
  height?: number;
  ariaLabel: string;
}) {
  const id = useId();
  const [hover, setHover] = useState<number | null>(null);
  const width = 600;
  const pad = { top: 10, right: 10, bottom: 22, left: 36 };
  const max = Math.max(1, ...series.flatMap((s) => s.values));
  const n = labels.length;
  const x = (i: number) => pad.left + (n <= 1 ? 0 : (i * (width - pad.left - pad.right)) / (n - 1));
  const y = (v: number) => pad.top + (1 - v / max) * (height - pad.top - pad.bottom);
  const ticks = [0, Math.round(max / 2), max];
  const step = Math.max(1, Math.ceil(n / 8));

  return (
    <figure className="flex flex-col gap-2">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-auto w-full"
        role="img"
        aria-labelledby={`${id}-title`}
        onMouseLeave={() => setHover(null)}
      >
        <title id={`${id}-title`}>{ariaLabel}</title>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} className="stroke-border" />
            <text
              x={pad.left - 4}
              y={y(t) + 3}
              textAnchor="end"
              className="fill-muted-foreground text-[10px]"
            >
              {t}
            </text>
          </g>
        ))}
        {labels.map((label, i) =>
          i % step === 0 ? (
            <text
              key={label}
              x={x(i)}
              y={height - 6}
              textAnchor="middle"
              className="fill-muted-foreground text-[10px]"
            >
              {label}
            </text>
          ) : null,
        )}
        {series.map((s) => (
          <polyline
            key={s.name}
            fill="none"
            stroke={s.color}
            strokeWidth={2}
            points={s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ")}
          />
        ))}
        {labels.map((label, i) => (
          <rect
            key={`hit-${label}`}
            x={x(i) - width / Math.max(n, 1) / 2}
            y={0}
            width={width / Math.max(n, 1)}
            height={height}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
          />
        ))}
        {hover !== null ? (
          <line
            x1={x(hover)}
            x2={x(hover)}
            y1={pad.top}
            y2={height - pad.bottom}
            className="stroke-muted-foreground"
            strokeDasharray="3 3"
          />
        ) : null}
      </svg>
      <figcaption className="flex flex-wrap items-center gap-3 text-xs">
        {series.map((s) => (
          <span key={s.name} className="flex items-center gap-1">
            <span className="inline-block size-2 rounded-full" style={{ background: s.color }} />
            {s.name}
            {hover !== null ? <strong className="ml-1">{s.values[hover]}</strong> : null}
          </span>
        ))}
        {hover !== null ? <span className="text-muted-foreground">{labels[hover]}</span> : null}
      </figcaption>
      <table className="sr-only">
        <caption>{ariaLabel}</caption>
        <thead>
          <tr>
            <th>Bucket</th>
            {series.map((s) => (
              <th key={s.name}>{s.name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {labels.map((label, i) => (
            <tr key={label}>
              <td>{label}</td>
              {series.map((s) => (
                <td key={s.name}>{s.values[i]}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
