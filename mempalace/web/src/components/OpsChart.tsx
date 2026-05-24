import { useMemo, useState } from "react";

import type { OpSeriesResponse } from "@/api/types";
import { formatInt } from "@/lib/format";

interface Props {
  series: OpSeriesResponse;
  height?: number;
}

/**
 * Stacked-bar chart of WAL operations over time.
 *
 * Hand-rolled SVG rather than a chart library: the dataset is small
 * (≤ 8760 hourly buckets for a full year), the visual is simple, and
 * adding visx would pull ~200KB for one screen. Tooltips use a fixed
 * floating panel rather than per-bar <title> so the multi-line counts
 * render reliably across browsers.
 */
export function OpsChart({ series, height = 240 }: Props) {
  const { buckets, op_types, palette, bucket } = series;
  const [hover, setHover] = useState<number | null>(null);

  const max = useMemo(() => {
    let m = 0;
    for (const b of buckets) {
      const sum = op_types.reduce((s, t) => s + (b.counts[t] ?? 0), 0);
      if (sum > m) m = sum;
    }
    return m || 1;
  }, [buckets, op_types]);

  const padL = 40;
  const padR = 12;
  const padT = 8;
  const padB = 24;
  const innerH = height - padT - padB;
  const minPlotW = 700;
  const barW = Math.max(2, Math.min(12, Math.floor(minPlotW / Math.max(buckets.length, 1))));
  const innerW = Math.max(minPlotW, barW * buckets.length);
  const totalW = innerW + padL + padR;
  const yTick = niceTicks(max, 4);

  const labelEvery = Math.max(1, Math.ceil(buckets.length / 8));
  const hoveredBucket = hover !== null ? buckets[hover] : null;
  const hoveredTotal = hoveredBucket
    ? op_types.reduce((s, t) => s + (hoveredBucket.counts[t] ?? 0), 0)
    : 0;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${totalW} ${height}`}
        className="w-full"
        role="img"
        aria-label={`operations by ${bucket}`}
      >
        {/* axis lines */}
        <line
          x1={padL}
          x2={totalW - padR}
          y1={padT + innerH}
          y2={padT + innerH}
          stroke="var(--color-border)"
        />
        <line
          x1={padL}
          x2={padL}
          y1={padT}
          y2={padT + innerH}
          stroke="var(--color-border)"
        />

        {/* y ticks */}
        {yTick.map((t) => {
          const y = padT + innerH - (t / max) * innerH;
          return (
            <g key={t}>
              <line
                x1={padL}
                x2={totalW - padR}
                y1={y}
                y2={y}
                stroke="var(--color-border)"
                strokeOpacity={0.3}
              />
              <text
                x={padL - 4}
                y={y + 3}
                textAnchor="end"
                fontSize="10"
                fill="var(--color-muted)"
              >
                {formatInt(t)}
              </text>
            </g>
          );
        })}

        {/* bars */}
        {buckets.map((b, i) => {
          let stackY = padT + innerH;
          const x = padL + i * barW;
          return (
            <g key={b.key}>
              {op_types.map((t) => {
                const v = b.counts[t] ?? 0;
                if (!v) return null;
                const h = (v / max) * innerH;
                stackY -= h;
                return (
                  <rect
                    key={t}
                    x={x}
                    y={stackY}
                    width={Math.max(1, barW - 1)}
                    height={h}
                    fill={palette[t] ?? "var(--color-muted)"}
                  />
                );
              })}
              <rect
                x={x}
                y={padT}
                width={barW}
                height={innerH}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
              />
            </g>
          );
        })}

        {/* x labels (first, last, evenly sampled middle) */}
        {buckets.map((b, i) =>
          i === 0 || i === buckets.length - 1 || i % labelEvery === 0 ? (
            <text
              key={b.key}
              x={padL + i * barW + barW / 2}
              y={padT + innerH + 14}
              textAnchor="middle"
              fontSize="10"
              fill="var(--color-muted)"
            >
              {bucket === "hour" ? b.key.slice(5) : b.key.slice(5)}
            </text>
          ) : null,
        )}
      </svg>

      {hoveredBucket && (
        <div className="pointer-events-none absolute top-2 right-2 rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-3 py-2 text-[11px] shadow-lg">
          <div className="font-semibold text-[color:var(--color-fg)]">{hoveredBucket.key}</div>
          <div className="mb-1 text-[color:var(--color-muted)]">
            total {formatInt(hoveredTotal)}
          </div>
          {op_types
            .map((t) => [t, hoveredBucket.counts[t] ?? 0] as const)
            .filter(([, v]) => v > 0)
            .map(([t, v]) => (
              <div key={t} className="flex items-center gap-1.5">
                <span
                  aria-hidden
                  className="inline-block h-2 w-2 rounded-sm"
                  style={{ background: palette[t] }}
                />
                <span className="text-[color:var(--color-muted)]">{t}</span>
                <span className="ml-auto tabular-nums">{formatInt(v)}</span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0];
  const step = Math.max(1, Math.ceil(max / count));
  const out: number[] = [];
  for (let v = 0; v <= max; v += step) out.push(v);
  if (out[out.length - 1] < max) out.push(max);
  return out;
}
