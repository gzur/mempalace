import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import {
  useKGAdd,
  useKGEntity,
  useKGGraph,
  useKGMerge,
  useKGSetEntityType,
} from "@/api/queries";
import { useKGStats } from "@/api/queries";
import { Breadcrumbs } from "@/components/Breadcrumbs";
import { KGGraph } from "@/components/KGGraph";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/kg/$entity")({
  component: EntityPage,
});

const COMMON_TYPES = [
  "person",
  "org",
  "project",
  "topic",
  "place",
  "activity",
  "unknown",
] as const;

function TypeControl({
  entity,
  currentType,
}: {
  entity: string;
  currentType: string;
}) {
  const setType = useKGSetEntityType();
  const [draft, setDraft] = useState(currentType);
  useEffect(() => {
    setDraft(currentType);
  }, [currentType]);

  const datalistId = useMemo(
    () => `entity-types-${entity.replace(/[^a-zA-Z0-9]/g, "")}`,
    [entity],
  );
  const trimmed = draft.trim();
  const canSubmit =
    trimmed.length > 0 && trimmed !== currentType && !setType.isPending;

  return (
    <section className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-3">
      <h2 className="mb-2 text-[11px] uppercase tracking-wide text-[color:var(--color-muted)]">
        type
      </h2>
      <p className="mb-2 text-[11px] text-[color:var(--color-muted)]">
        Relabel this entity. Most auto-created entities default to
        <code> unknown</code>; pick whatever convention fits (the suggestions
        are a starting point, not a fixed list).
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!canSubmit) return;
          setType.mutate({ name: entity, type: trimmed });
        }}
        className="flex flex-wrap items-center gap-2 text-[12px]"
      >
        <span className="text-[color:var(--color-muted)]">current</span>
        <code className="text-[color:var(--color-accent)]">{currentType}</code>
        <span className="text-[color:var(--color-muted)]">→</span>
        <input
          list={datalistId}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="new type"
          className="flex-1 rounded border border-[color:var(--color-border)] bg-[color:var(--color-bg)] px-2 py-1 outline-none focus:border-[color:var(--color-accent)]"
        />
        <datalist id={datalistId}>
          {COMMON_TYPES.map((t) => (
            <option key={t} value={t} />
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
          {setType.isPending ? "saving…" : "set type"}
        </button>
      </form>
      {setType.data && (
        <p
          className={cn(
            "mt-2 text-[12px]",
            setType.data.success
              ? "text-[color:var(--color-success)]"
              : "text-[color:var(--color-danger)]",
          )}
        >
          {setType.data.success
            ? `was '${setType.data.previous_type}', now '${setType.data.type}'`
            : (setType.data.error ?? "failed")}
        </p>
      )}
    </section>
  );
}

function AddFactControl({
  subject,
  entityNames,
  predicateSuggestions,
  onAdded,
}: {
  subject: string;
  entityNames: string[];
  predicateSuggestions: string[];
  onAdded?: () => void;
}) {
  const add = useKGAdd();
  const [predicate, setPredicate] = useState("");
  const [object, setObject] = useState("");
  const [valueText, setValueText] = useState("");
  const [validFrom, setValidFrom] = useState("");
  const [kind, setKind] = useState<"entity" | "text">("entity");

  const predListId = useMemo(
    () => `pred-suggest-${subject.replace(/[^a-zA-Z0-9]/g, "")}`,
    [subject],
  );
  const entityListId = useMemo(
    () => `entity-suggest-${subject.replace(/[^a-zA-Z0-9]/g, "")}`,
    [subject],
  );

  const pred = predicate.trim();
  const objectInput = kind === "entity" ? object.trim() : valueText.trim();
  const canSubmit = pred.length > 0 && objectInput.length > 0 && !add.isPending;

  return (
    <section className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-3">
      <h2 className="mb-2 text-[11px] uppercase tracking-wide text-[color:var(--color-muted)]">
        add fact
      </h2>
      <p className="mb-2 text-[11px] text-[color:var(--color-muted)]">
        Adds a new outgoing triple from <code>{subject}</code>. Object can
        be another entity (autocompletes against the graph) or inline
        descriptive prose.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!canSubmit) return;
          add.mutate(
            {
              subject,
              predicate: pred,
              // tool_kg_add still requires ``object`` as a non-empty
              // string even when ``value_text`` is set, so pass a
              // short placeholder for text-valued triples — the actual
              // prose travels in ``value_text``.
              object: kind === "entity" ? objectInput : objectInput.slice(0, 40),
              value_text: kind === "text" ? objectInput : undefined,
              valid_from: validFrom.trim() || undefined,
            },
            {
              onSuccess: (data) => {
                if (data.success) {
                  setPredicate("");
                  setObject("");
                  setValueText("");
                  setValidFrom("");
                  onAdded?.();
                }
              },
            },
          );
        }}
        className="flex flex-col gap-2 text-[12px]"
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[color:var(--color-muted)]">subject</span>
          <code className="text-[color:var(--color-accent)]">{subject}</code>
          <span className="text-[color:var(--color-muted)]">→</span>
          <input
            list={predListId}
            value={predicate}
            onChange={(e) => setPredicate(e.target.value)}
            placeholder="predicate (e.g. works_at)"
            className="w-48 rounded border border-[color:var(--color-border)] bg-[color:var(--color-bg)] px-2 py-1 outline-none focus:border-[color:var(--color-accent)]"
          />
          <datalist id={predListId}>
            {predicateSuggestions.map((p) => (
              <option key={p} value={p} />
            ))}
          </datalist>
          <span className="text-[color:var(--color-muted)]">→</span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[color:var(--color-muted)]">object kind</span>
          {(["entity", "text"] as const).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => setKind(k)}
              className={cn(
                "rounded border px-2 py-0.5",
                k === kind
                  ? "border-[color:var(--color-accent)] text-[color:var(--color-accent)]"
                  : "border-[color:var(--color-border)] text-[color:var(--color-muted)] hover:border-[color:var(--color-fg)]",
              )}
            >
              {k}
            </button>
          ))}
          <span className="ml-2 text-[color:var(--color-muted)]">valid from</span>
          <input
            value={validFrom}
            onChange={(e) => setValidFrom(e.target.value)}
            placeholder="YYYY-MM-DD (optional)"
            className="w-40 rounded border border-[color:var(--color-border)] bg-[color:var(--color-bg)] px-2 py-1 outline-none focus:border-[color:var(--color-accent)]"
          />
        </div>

        {kind === "entity" ? (
          <div className="flex flex-wrap items-center gap-2">
            <input
              list={entityListId}
              value={object}
              onChange={(e) => setObject(e.target.value)}
              placeholder="object entity"
              className="flex-1 rounded border border-[color:var(--color-border)] bg-[color:var(--color-bg)] px-2 py-1 outline-none focus:border-[color:var(--color-accent)]"
            />
            <datalist id={entityListId}>
              {entityNames.slice(0, 500).map((n) => (
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
              {add.isPending ? "adding…" : "add fact"}
            </button>
          </div>
        ) : (
          <div className="flex items-start gap-2">
            <textarea
              value={valueText}
              onChange={(e) => setValueText(e.target.value)}
              placeholder="descriptive value (a clause, a status, a quote)"
              rows={2}
              className="flex-1 rounded border border-[color:var(--color-border)] bg-[color:var(--color-bg)] px-2 py-1 outline-none focus:border-[color:var(--color-accent)]"
            />
            <button
              type="submit"
              disabled={!canSubmit}
              className={cn(
                "self-start rounded border px-3 py-1",
                canSubmit
                  ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent)] text-[color:var(--color-accent-fg)]"
                  : "border-[color:var(--color-border)] text-[color:var(--color-muted)]",
              )}
            >
              {add.isPending ? "adding…" : "add fact"}
            </button>
          </div>
        )}
      </form>
      {add.data && (
        <p
          className={cn(
            "mt-2 text-[12px]",
            add.data.success
              ? "text-[color:var(--color-success)]"
              : "text-[color:var(--color-danger)]",
          )}
        >
          {add.data.success
            ? `added: ${add.data.fact}`
            : (add.data.error ?? "failed")}
        </p>
      )}
    </section>
  );
}

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
  const stats = useKGStats();

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
          // Neighborhoods are small by construction — labelling all of
          // them is the most useful default here, even when the main
          // /app/kg view is on ``auto``.
          labelsMode="all"
          onNodeClick={(_id, name) => {
            if (name === entity) return;
            window.location.assign(`/app/kg/${encodeURIComponent(name)}`);
          }}
        />
      )}

      {(() => {
        const currentType =
          fullGraph.data?.nodes.find((n) => n.name === entity)?.type ?? "unknown";
        return <TypeControl entity={entity} currentType={currentType} />;
      })()}

      <AddFactControl
        subject={entity}
        entityNames={
          fullGraph.data?.nodes
            .map((n) => n.name)
            .filter((n) => n !== entity) ?? []
        }
        predicateSuggestions={stats.data?.relationship_types ?? []}
      />

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

      {(() => {
        const all = facts.data?.facts ?? [];
        const entityFacts = all.filter((f) => f.object_kind !== "text");
        const textFacts = all.filter((f) => f.object_kind === "text");
        return (
          <>
            <section>
              <h2 className="mb-2 text-[11px] uppercase tracking-wide text-[color:var(--color-muted)]">
                facts ({entityFacts.length})
              </h2>
              <ul className="flex flex-col gap-1">
                {entityFacts.map((f, i) => (
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
                    <code className="text-[color:var(--color-accent)]">
                      {f.predicate}
                    </code>
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

            {textFacts.length > 0 && (
              <section>
                <h2 className="mb-2 text-[11px] uppercase tracking-wide text-[color:var(--color-muted)]">
                  properties ({textFacts.length})
                </h2>
                <p className="mb-2 text-[11px] text-[color:var(--color-muted)]">
                  Inline descriptive values — these don't appear in the
                  graph view since they're properties of this entity
                  rather than edges to other entities.
                </p>
                <ul className="flex flex-col gap-1">
                  {textFacts.map((f, i) => (
                    <li
                      key={`text-${f.predicate}-${i}`}
                      className={cn(
                        "rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-3 py-1.5 text-[12px]",
                        f.current === false &&
                          "text-[color:var(--color-muted)]",
                      )}
                    >
                      <div className="flex items-baseline justify-between gap-2">
                        <code className="text-[color:var(--color-accent)]">
                          {f.predicate}
                        </code>
                        <span className="text-[color:var(--color-muted)]">
                          {f.valid_from ?? "?"} → {f.valid_to ?? "now"}
                        </span>
                      </div>
                      <p className="mt-1 whitespace-pre-wrap break-words">
                        {f.object}
                      </p>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        );
      })()}
    </div>
  );
}
