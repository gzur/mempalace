import { createFileRoute, Link } from "@tanstack/react-router";

import { useOpsSeries, useRecentOps } from "@/api/queries";
import { Breadcrumbs } from "@/components/Breadcrumbs";
import { OpsChart } from "@/components/OpsChart";
import { cn } from "@/lib/cn";
import { formatInt, relativeTime, truncate } from "@/lib/format";

const DAY_OPTIONS = [1, 7, 30, 90, 365] as const;
const BUCKET_OPTIONS = ["auto", "hour", "day"] as const;

interface Search {
  days?: number;
  bucket?: "hour" | "day" | "auto";
}

export const Route = createFileRoute("/ops")({
  component: OpsPage,
  validateSearch: (s: Record<string, unknown>): Search => ({
    days:
      typeof s.days === "number"
        ? s.days
        : typeof s.days === "string"
          ? parseInt(s.days, 10) || 30
          : 30,
    bucket:
      s.bucket === "hour" || s.bucket === "day" || s.bucket === "auto"
        ? s.bucket
        : "auto",
  }),
});

function OpsPage() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const days = search.days ?? 30;
  const bucket = search.bucket ?? "auto";
  const seriesBucket = bucket === "auto" ? undefined : bucket;

  const series = useOpsSeries(days, seriesBucket);
  const recent = useRecentOps(50);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      <Breadcrumbs items={[{ label: "palace", to: "/" }, { label: "operations" }]} />

      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">operations</h1>
          <p className="mt-1 text-[12px] text-[color:var(--color-muted)]">
            {series.data
              ? `${formatInt(series.data.total_ops)} ops · last ${days}d · ${series.data.bucket} buckets`
              : "loading WAL…"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          <span className="text-[color:var(--color-muted)]">range</span>
          {DAY_OPTIONS.map((d) => (
            <button
              key={d}
              type="button"
              onClick={() => navigate({ search: { days: d, bucket } })}
              className={cn(
                "rounded border px-2 py-0.5 transition-colors",
                d === days
                  ? "border-[color:var(--color-accent)] text-[color:var(--color-accent)]"
                  : "border-[color:var(--color-border)] text-[color:var(--color-muted)] hover:border-[color:var(--color-fg)]",
              )}
            >
              {d}d
            </button>
          ))}
          <span className="ml-2 text-[color:var(--color-muted)]">bucket</span>
          {BUCKET_OPTIONS.map((b) => (
            <button
              key={b}
              type="button"
              onClick={() => navigate({ search: { days, bucket: b } })}
              className={cn(
                "rounded border px-2 py-0.5 transition-colors",
                b === bucket
                  ? "border-[color:var(--color-accent)] text-[color:var(--color-accent)]"
                  : "border-[color:var(--color-border)] text-[color:var(--color-muted)] hover:border-[color:var(--color-fg)]",
              )}
            >
              {b}
            </button>
          ))}
        </div>
      </header>

      <section className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-3">
        {series.data ? (
          <OpsChart series={series.data} />
        ) : (
          <div className="h-[240px] grid place-items-center text-[12px] text-[color:var(--color-muted)]">
            {series.isError ? "failed to load series" : "loading…"}
          </div>
        )}
        {series.data && (
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px]">
            {series.data.op_types.map((t) => (
              <span key={t} className="flex items-center gap-1.5">
                <span
                  aria-hidden
                  className="inline-block h-2 w-2 rounded-sm"
                  style={{ background: series.data.palette[t] }}
                />
                <span className="text-[color:var(--color-muted)]">{t}</span>
              </span>
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-[11px] uppercase tracking-wide text-[color:var(--color-muted)]">
          recent ops
        </h2>
        <ul className="flex flex-col">
          {(recent.data?.operations ?? [])
            .slice()
            .reverse()
            .slice(0, 50)
            .map((op, i) => {
              const params = (op.params ?? {}) as Record<string, string>;
              const target = params.drawer_id ?? params.entry_id ?? null;
              const context = [params.wing, params.room].filter(Boolean).join("/");
              return (
                <li
                  key={`${op.timestamp}-${i}`}
                  className="flex items-baseline gap-3 border-b border-[color:var(--color-border)] py-1 text-[12px]"
                >
                  <span className="w-24 shrink-0 text-[color:var(--color-muted)] tabular-nums">
                    {relativeTime(op.timestamp)}
                  </span>
                  <span className="w-40 shrink-0">
                    {target ? (
                      <Link
                        to="/drawers/$id"
                        params={{ id: target }}
                        className="text-[color:var(--color-accent)] hover:underline"
                      >
                        {op.operation}
                      </Link>
                    ) : (
                      <span>{op.operation}</span>
                    )}
                  </span>
                  <span className="truncate text-[color:var(--color-muted)]">
                    {truncate(context, 60)}
                  </span>
                </li>
              );
            })}
        </ul>
      </section>
    </div>
  );
}
