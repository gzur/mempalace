import { createFileRoute, Link } from "@tanstack/react-router";

import { usePalaceSummary, useRecentOps, useKGStats } from "@/api/queries";
import { StatCard } from "@/components/StatCard";
import { formatInt, relativeTime, truncate } from "@/lib/format";

export const Route = createFileRoute("/")({
  component: Dashboard,
});

function Dashboard() {
  const summary = usePalaceSummary();
  const ops = useRecentOps(20);
  const kg = useKGStats();

  const total = summary.data?.total_drawers ?? 0;
  const wingCount = summary.data ? Object.keys(summary.data.wings).length : 0;
  const roomCount = summary.data ? Object.keys(summary.data.rooms).length : 0;
  const topWings = summary.data
    ? Object.entries(summary.data.wings)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 8)
    : [];

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">palace</h1>
        <p className="mt-1 text-[12px] text-[color:var(--color-muted)]">
          local-first verbatim memory · everything below lives on your machine
        </p>
      </div>

      <section className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard
          label="drawers"
          value={formatInt(total)}
          sublabel="verbatim items"
        />
        <StatCard label="wings" value={formatInt(wingCount)} />
        <StatCard label="rooms" value={formatInt(roomCount)} />
        <StatCard
          label="kg facts"
          value={formatInt(kg.data?.current_facts ?? 0)}
          sublabel={
            kg.data ? `${formatInt(kg.data.entities)} entities` : undefined
          }
          tone="accent"
        />
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <div className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-[color:var(--color-muted)]">
              top wings
            </h2>
            <Link
              to="/wings"
              className="text-[11px] text-[color:var(--color-muted)] hover:text-[color:var(--color-accent)]"
            >
              browse all →
            </Link>
          </div>
          {summary.isPending ? (
            <p className="text-[12px] text-[color:var(--color-muted)]">loading…</p>
          ) : topWings.length === 0 ? (
            <p className="text-[12px] text-[color:var(--color-muted)]">no wings yet</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {topWings.map(([name, count]) => (
                <li
                  key={name}
                  className="flex items-center justify-between text-[13px]"
                >
                  <Link
                    to="/wings/$wing"
                    params={{ wing: name }}
                    className="truncate hover:underline"
                  >
                    {name}
                  </Link>
                  <span className="text-[color:var(--color-muted)] tabular-nums">
                    {formatInt(count)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-[color:var(--color-muted)]">
              recent operations
            </h2>
            <Link
              to="/ops"
              className="text-[11px] text-[color:var(--color-muted)] hover:text-[color:var(--color-accent)]"
            >
              all →
            </Link>
          </div>
          {ops.isPending ? (
            <p className="text-[12px] text-[color:var(--color-muted)]">loading…</p>
          ) : !ops.data?.operations?.length ? (
            <p className="text-[12px] text-[color:var(--color-muted)]">
              no operations recorded yet
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {ops.data.operations
                .slice()
                .reverse()
                .slice(0, 10)
                .map((op, i) => {
                  const params = (op.params ?? {}) as Record<string, string>;
                  const target =
                    params.drawer_id ?? params.entry_id ?? null;
                  const context = [params.wing, params.room]
                    .filter(Boolean)
                    .join("/");
                  return (
                    <li
                      key={i}
                      className="flex items-baseline gap-2 text-[12px]"
                    >
                      <span className="w-20 shrink-0 text-[color:var(--color-muted)] tabular-nums">
                        {relativeTime(op.timestamp)}
                      </span>
                      {target ? (
                        <Link
                          to="/drawers/$id"
                          params={{ id: target }}
                          className="text-[color:var(--color-accent)] hover:underline"
                        >
                          {op.operation}
                        </Link>
                      ) : (
                        <span className="text-[color:var(--color-fg)]">
                          {op.operation}
                        </span>
                      )}
                      {context && (
                        <span className="text-[color:var(--color-muted)]">
                          {truncate(context, 28)}
                        </span>
                      )}
                    </li>
                  );
                })}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}
