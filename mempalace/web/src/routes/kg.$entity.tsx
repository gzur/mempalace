import { createFileRoute, Link } from "@tanstack/react-router";

import { useKGEntity, useKGGraph } from "@/api/queries";
import { Breadcrumbs } from "@/components/Breadcrumbs";
import { KGGraph } from "@/components/KGGraph";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/kg/$entity")({
  component: EntityPage,
});

function EntityPage() {
  const { entity } = Route.useParams();
  const facts = useKGEntity(entity, "both");
  // Reuse the same full-graph dump and let the SPA narrow client-side
  // to the entity's neighbors. Cheap because the dump is cached by
  // TanStack Query.
  const fullGraph = useKGGraph(false);

  const neighborhood = (() => {
    const data = fullGraph.data;
    if (!data) return null;
    const entityLower = entity.toLowerCase();
    const focusId = data.nodes.find(
      (n) => n.name.toLowerCase() === entityLower,
    )?.id;
    if (!focusId) return { nodes: [], edges: [], truncated: false };
    const neighbors = new Set<string>([focusId]);
    for (const e of data.edges) {
      if (e.s === focusId) neighbors.add(e.o);
      if (e.o === focusId) neighbors.add(e.s);
    }
    const nodes = data.nodes.filter((n) => neighbors.has(n.id));
    const edges = data.edges.filter(
      (e) => neighbors.has(e.s) && neighbors.has(e.o),
    );
    return { nodes, edges, truncated: false };
  })();

  return (
    <div className="flex flex-col gap-4">
      <Breadcrumbs
        items={[
          { label: "palace", to: "/" },
          { label: "knowledge graph", to: "/kg" },
          { label: entity },
        ]}
      />
      <header>
        <h1 className="text-xl font-semibold">{entity}</h1>
        <p className="mt-1 text-[12px] text-[color:var(--color-muted)]">
          {facts.data
            ? `${facts.data.count} fact${facts.data.count === 1 ? "" : "s"}`
            : "loading…"}
        </p>
      </header>

      {neighborhood && neighborhood.nodes.length > 1 && (
        <KGGraph
          graph={neighborhood}
          highlightName={entity}
          height={420}
          onNodeClick={(_id, name) => {
            if (name === entity) return;
            window.location.assign(`/app/kg/${encodeURIComponent(name)}`);
          }}
        />
      )}

      {facts.data?.error && (
        <p className="text-[color:var(--color-warn)]">{facts.data.error}</p>
      )}

      <section>
        <h2 className="mb-2 text-[11px] uppercase tracking-wide text-[color:var(--color-muted)]">
          facts
        </h2>
        <ul className="flex flex-col gap-1">
          {(facts.data?.facts ?? []).map((f, i) => (
            <li
              key={`${f.predicate}-${f.subject}-${f.object}-${i}`}
              className={cn(
                "flex flex-wrap items-baseline gap-2 rounded border px-3 py-1.5 text-[12px]",
                f.current === false
                  ? "border-[color:var(--color-border)] bg-[color:var(--color-surface)] text-[color:var(--color-muted)]"
                  : "border-[color:var(--color-border)] bg-[color:var(--color-surface)]",
              )}
            >
              <Link
                to="/kg/$entity"
                params={{ entity: f.subject }}
                className="font-semibold hover:underline"
              >
                {f.subject}
              </Link>
              <code className="text-[color:var(--color-accent)]">{f.predicate}</code>
              <Link
                to="/kg/$entity"
                params={{ entity: f.object }}
                className="font-semibold hover:underline"
              >
                {f.object}
              </Link>
              <span className="ml-auto text-[color:var(--color-muted)]">
                {f.valid_from ?? "?"} → {f.valid_to ?? "now"}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
