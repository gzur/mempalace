import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { useKGEntity, useKGGraph, useKGMerge } from "@/api/queries";
import { Breadcrumbs } from "@/components/Breadcrumbs";
import { KGGraph } from "@/components/KGGraph";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/kg/$entity")({
  component: EntityPage,
});

function MergeControl({
  entity,
  otherEntityNames,
  onMerged,
}: {
  entity: string;
  otherEntityNames: string[];
  onMerged: (into: string) => void;
}) {
  const merge = useKGMerge();
  const [target, setTarget] = useState("");
  const datalistId = useMemo(
    () => `merge-targets-${entity.replace(/[^a-zA-Z0-9]/g, "")}`,
    [entity],
  );
  const trimmed = target.trim();
  const canSubmit =
    trimmed.length > 0 && trimmed !== entity && !merge.isPending;

  return (
    <section className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-3">
      <h2 className="mb-2 text-[11px] uppercase tracking-wide text-[color:var(--color-muted)]">
        merge into…
      </h2>
      <p className="mb-2 text-[11px] text-[color:var(--color-muted)]">
        Fold this entity into another (e.g. an alias). Every triple
        referencing <code>{entity}</code> will be rewritten to point at
        the target, then this entity is deleted.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!canSubmit) return;
          merge.mutate(
            { from: entity, into: trimmed },
            {
              onSuccess: (data) => {
                if (data.success) {
                  onMerged(data.into ?? trimmed);
                }
              },
            },
          );
        }}
        className="flex flex-wrap items-center gap-2 text-[12px]"
      >
        <input
          list={datalistId}
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder="canonical entity name"
          className="flex-1 rounded border border-[color:var(--color-border)] bg-[color:var(--color-bg)] px-2 py-1 outline-none focus:border-[color:var(--color-accent)]"
        />
        <datalist id={datalistId}>
          {otherEntityNames.slice(0, 500).map((n) => (
            <option key={n} value={n} />
          ))}
        </datalist>
        <button
          type="submit"
          disabled={!canSubmit}
          className={cn(
            "rounded border px-3 py-1",
            canSubmit
              ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent)] text-[color:var(--color-accent-fg)]"
              : "border-[color:var(--color-border)] text-[color:var(--color-muted)]",
          )}
        >
          {merge.isPending ? "merging…" : "merge"}
        </button>
      </form>
      {merge.data && (
        <p
          className={cn(
            "mt-2 text-[12px]",
            merge.data.success
              ? "text-[color:var(--color-success)]"
              : "text-[color:var(--color-danger)]",
          )}
        >
          {merge.data.success
            ? `rewrote ${merge.data.triples_rewritten ?? 0} triple(s) from ${merge.data.from} → ${merge.data.into}`
            : (merge.data.error ?? "merge failed")}
        </p>
      )}
      {merge.isError && (
        <p className="mt-2 text-[12px] text-[color:var(--color-danger)]">
          {(merge.error as Error).message}
        </p>
      )}
    </section>
  );
}

function EntityPage() {
  const { entity } = Route.useParams();
  const navigate = useNavigate();
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

      <MergeControl
        entity={entity}
        otherEntityNames={
          fullGraph.data?.nodes
            .map((n) => n.name)
            .filter((n) => n !== entity) ?? []
        }
        onMerged={(into) => {
          navigate({ to: "/kg/$entity", params: { entity: into } });
        }}
      />

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
