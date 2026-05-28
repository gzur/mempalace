import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMemo } from "react";

import { useKGGraph, useKGStats } from "@/api/queries";
import { Breadcrumbs } from "@/components/Breadcrumbs";
import { KGGraph } from "@/components/KGGraph";
import { StatCard } from "@/components/StatCard";
import { cn } from "@/lib/cn";
import { formatInt } from "@/lib/format";

type LabelsMode = "auto" | "all" | "off";

interface Search {
  q?: string;
  includeExpired?: boolean;
  maxNodes?: number;
  labels?: LabelsMode;
}

function parseLabels(v: unknown): LabelsMode {
  if (v === "all" || v === "off" || v === "auto") return v;
  return "auto";
}

export const Route = createFileRoute("/kg/")({
  component: KGPage,
  validateSearch: (s: Record<string, unknown>): Search => ({
    q: typeof s.q === "string" && s.q ? s.q : undefined,
    includeExpired:
      s.includeExpired === true ||
      s.includeExpired === "1" ||
      s.includeExpired === "true",
    maxNodes:
      typeof s.maxNodes === "number"
        ? s.maxNodes
        : typeof s.maxNodes === "string"
          ? parseInt(s.maxNodes, 10) || undefined
          : undefined,
    labels: parseLabels(s.labels),
  }),
});

const TYPE_PALETTE: Record<string, string> = {
  person: "#3fb950",
  org: "#58a6ff",
  project: "#bc8cff",
  activity: "#d29922",
  topic: "#56d4dd",
  place: "#ff7b72",
  unknown: "#8b949e",
};

function KGPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: "/kg/" });
  const includeExpired = search.includeExpired ?? false;
  const maxNodes = search.maxNodes;
  const labels: LabelsMode = search.labels ?? "auto";

  const stats = useKGStats();
  const graph = useKGGraph(includeExpired, maxNodes);

  const typeCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const n of graph.data?.nodes ?? []) {
      m.set(n.type, (m.get(n.type) ?? 0) + 1);
    }
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
  }, [graph.data]);

  return (
    <div className="flex flex-col gap-4">
      <Breadcrumbs items={[{ label: "palace", to: "/" }, { label: "knowledge graph" }]} />

      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">knowledge graph</h1>
          <p className="mt-1 text-[12px] text-[color:var(--color-muted)]">
            entities and the temporal facts that connect them. click a node to drill in.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          <button
            type="button"
            onClick={() =>
              navigate({ search: { ...search, includeExpired: !includeExpired } })
            }
            className={cn(
              "rounded border px-2 py-0.5",
              includeExpired
                ? "border-[color:var(--color-accent)] text-[color:var(--color-accent)]"
                : "border-[color:var(--color-border)] text-[color:var(--color-muted)] hover:border-[color:var(--color-fg)]",
            )}
          >
            {includeExpired ? "expired included" : "current only"}
          </button>
          {[500, 2000, undefined].map((n) => (
            <button
              key={String(n)}
              type="button"
              onClick={() => navigate({ search: { ...search, maxNodes: n } })}
              className={cn(
                "rounded border px-2 py-0.5",
                n === maxNodes
                  ? "border-[color:var(--color-accent)] text-[color:var(--color-accent)]"
                  : "border-[color:var(--color-border)] text-[color:var(--color-muted)] hover:border-[color:var(--color-fg)]",
              )}
            >
              {n ? `top ${n}` : "full"}
            </button>
          ))}
          <span className="ml-2 text-[color:var(--color-muted)]">labels</span>
          {(["auto", "all", "off"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => navigate({ search: { ...search, labels: m } })}
              className={cn(
                "rounded border px-2 py-0.5",
                m === labels
                  ? "border-[color:var(--color-accent)] text-[color:var(--color-accent)]"
                  : "border-[color:var(--color-border)] text-[color:var(--color-muted)] hover:border-[color:var(--color-fg)]",
              )}
            >
              {m}
            </button>
          ))}
        </div>
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="entities" value={formatInt(stats.data?.entities ?? 0)} />
        <StatCard label="triples" value={formatInt(stats.data?.triples ?? 0)} />
        <StatCard
          label="current"
          value={formatInt(stats.data?.current_facts ?? 0)}
          tone="success"
        />
        <StatCard
          label="expired"
          value={formatInt(stats.data?.expired_facts ?? 0)}
          tone="warn"
        />
      </section>

      {graph.data?.truncated && (
        <div className="rounded border border-[color:var(--color-warn)]/40 bg-[color:var(--color-warn)]/10 px-3 py-2 text-[12px]">
          truncated to top {formatInt(graph.data.nodes.length)} nodes by degree.{" "}
          <button
            type="button"
            onClick={() => navigate({ search: { ...search, maxNodes: undefined } })}
            className="underline"
          >
            load full graph
          </button>
        </div>
      )}

      <section>
        {graph.isPending ? (
          <div className="grid h-[600px] place-items-center text-[12px] text-[color:var(--color-muted)]">
            loading graph…
          </div>
        ) : graph.isError ? (
          <p className="text-[color:var(--color-danger)]">
            failed to load graph: {(graph.error as Error).message}
          </p>
        ) : graph.data ? (
          <KGGraph
            graph={graph.data}
            highlightName={search.q}
            typePalette={TYPE_PALETTE}
            labelsMode={labels}
            onNodeClick={(_id, name) =>
              navigate({
                to: "/kg/$entity",
                params: { entity: name },
              })
            }
          />
        ) : null}
      </section>

      {typeCounts.length > 0 && (
        <section>
          <h2 className="mb-2 text-[11px] uppercase tracking-wide text-[color:var(--color-muted)]">
            entity types
          </h2>
          <div className="flex flex-wrap gap-2 text-[12px]">
            {typeCounts.map(([type, count]) => (
              <span
                key={type}
                className="flex items-center gap-1.5 rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-2 py-0.5"
              >
                <span
                  aria-hidden
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ background: TYPE_PALETTE[type] ?? "var(--color-muted)" }}
                />
                <span className="text-[color:var(--color-muted)]">{type}</span>
                <span className="tabular-nums">{formatInt(count)}</span>
              </span>
            ))}
          </div>
        </section>
      )}

      <p className="text-[11px] text-[color:var(--color-muted)]">
        Want a specific entity?{" "}
        <Link to="/" className="hover:underline">
          press ⌘K
        </Link>{" "}
        and search.
      </p>
    </div>
  );
}
