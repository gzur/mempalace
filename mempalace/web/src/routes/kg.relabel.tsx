import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { useKGGraph, useKGSetEntityTypeBulk } from "@/api/queries";
import { Breadcrumbs } from "@/components/Breadcrumbs";
import { cn } from "@/lib/cn";
import { formatInt } from "@/lib/format";

interface Search {
  from?: string;
}

export const Route = createFileRoute("/kg/relabel")({
  component: RelabelPage,
  validateSearch: (s: Record<string, unknown>): Search => ({
    from: typeof s.from === "string" && s.from ? s.from : undefined,
  }),
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

function RelabelPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: "/kg/relabel" });
  const graph = useKGGraph(false, undefined);
  const bulk = useKGSetEntityTypeBulk();

  const nodes = graph.data?.nodes ?? [];

  const allTypes = useMemo(() => {
    const m = new Map<string, number>();
    for (const n of nodes) {
      m.set(n.type, (m.get(n.type) ?? 0) + 1);
    }
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
  }, [nodes]);

  const fromType = search.from ?? (allTypes[0]?.[0] ?? "unknown");

  const visible = useMemo(
    () =>
      nodes
        .filter((n) => n.type === fromType)
        .sort((a, b) => b.degree - a.degree || a.name.localeCompare(b.name)),
    [nodes, fromType],
  );

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [target, setTarget] = useState<string>("person");

  const toggle = (name: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };
  const selectAll = () => setSelected(new Set(visible.map((n) => n.name)));
  const clearAll = () => setSelected(new Set());

  const trimmed = target.trim();
  const canApply =
    selected.size > 0 &&
    trimmed.length > 0 &&
    trimmed !== fromType &&
    !bulk.isPending;

  const apply = () => {
    if (!canApply) return;
    bulk.mutate(
      { entities: Array.from(selected), type: trimmed },
      {
        onSuccess: () => {
          setSelected(new Set());
        },
      },
    );
  };

  return (
    <div className="flex flex-col gap-4">
      <Breadcrumbs
        items={[
          { label: "palace", to: "/" },
          { label: "knowledge graph", to: "/kg" },
          { label: "bulk relabel" },
        ]}
      />

      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">bulk relabel</h1>
          <p className="mt-1 text-[12px] text-[color:var(--color-muted)]">
            Sweep entities of one type into another. Auto-detected entities
            usually land as <code>unknown</code> — pick a real type and
            relabel them in batches.
          </p>
        </div>
        <Link
          to="/kg"
          className="rounded border border-[color:var(--color-border)] px-2 py-0.5 text-[12px] text-[color:var(--color-muted)] hover:border-[color:var(--color-fg)]"
        >
          ← graph
        </Link>
      </header>

      <section className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-3">
        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          <span className="text-[color:var(--color-muted)]">from</span>
          {allTypes.map(([t, c]) => (
            <button
              key={t}
              type="button"
              onClick={() => {
                navigate({ search: { from: t } });
                setSelected(new Set());
              }}
              className={cn(
                "rounded border px-2 py-0.5",
                t === fromType
                  ? "border-[color:var(--color-accent)] text-[color:var(--color-accent)]"
                  : "border-[color:var(--color-border)] text-[color:var(--color-muted)] hover:border-[color:var(--color-fg)]",
              )}
            >
              {t} <span className="tabular-nums">{formatInt(c)}</span>
            </button>
          ))}
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2 text-[12px]">
          <span className="text-[color:var(--color-muted)]">target</span>
          <input
            list="bulk-relabel-types"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="new type"
            className="w-40 rounded border border-[color:var(--color-border)] bg-[color:var(--color-bg)] px-2 py-1 outline-none focus:border-[color:var(--color-accent)]"
          />
          <datalist id="bulk-relabel-types">
            {COMMON_TYPES.map((t) => (
              <option key={t} value={t} />
            ))}
          </datalist>
          <button
            type="button"
            onClick={apply}
            disabled={!canApply}
            className={cn(
              "rounded border px-3 py-1",
              canApply
                ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent)] text-[color:var(--color-accent-fg)]"
                : "border-[color:var(--color-border)] text-[color:var(--color-muted)]",
            )}
          >
            {bulk.isPending
              ? `applying ${selected.size}…`
              : `apply to ${selected.size} selected`}
          </button>
          <span className="ml-auto flex items-center gap-2 text-[color:var(--color-muted)]">
            <button
              type="button"
              onClick={selectAll}
              disabled={visible.length === 0}
              className="rounded border border-[color:var(--color-border)] px-2 py-0.5 hover:border-[color:var(--color-fg)] disabled:opacity-40"
            >
              select all ({visible.length})
            </button>
            <button
              type="button"
              onClick={clearAll}
              disabled={selected.size === 0}
              className="rounded border border-[color:var(--color-border)] px-2 py-0.5 hover:border-[color:var(--color-fg)] disabled:opacity-40"
            >
              clear
            </button>
          </span>
        </div>
      </section>

      {bulk.data && (
        <section className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-3 text-[12px]">
          <h2 className="mb-1 text-[11px] uppercase tracking-wide text-[color:var(--color-muted)]">
            last batch → {bulk.data.type}
          </h2>
          {(() => {
            const ok = bulk.data.results.filter((r) => r.ok).length;
            const fail = bulk.data.results.length - ok;
            return (
              <p className="mb-2">
                <span className="text-[color:var(--color-success)]">
                  {ok} ok
                </span>
                {fail > 0 && (
                  <>
                    {" · "}
                    <span className="text-[color:var(--color-danger)]">
                      {fail} failed
                    </span>
                  </>
                )}
              </p>
            );
          })()}
          {bulk.data.results.some((r) => !r.ok) && (
            <ul className="space-y-0.5">
              {bulk.data.results
                .filter((r) => !r.ok)
                .map((r) => (
                  <li
                    key={r.name}
                    className="text-[color:var(--color-danger)]"
                  >
                    {r.name}: {r.error ?? r.response?.error ?? "failed"}
                  </li>
                ))}
            </ul>
          )}
        </section>
      )}

      <section>
        {graph.isPending ? (
          <p className="text-[12px] text-[color:var(--color-muted)]">
            loading entities…
          </p>
        ) : visible.length === 0 ? (
          <p className="text-[12px] text-[color:var(--color-muted)]">
            no entities of type <code>{fromType}</code>.
          </p>
        ) : (
          <ul className="divide-y divide-[color:var(--color-border)] rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] text-[13px]">
            {visible.map((n) => {
              const checked = selected.has(n.name);
              return (
                <li
                  key={n.id}
                  className={cn(
                    "flex items-center gap-3 px-3 py-1.5",
                    checked && "bg-[color:var(--color-bg)]",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggle(n.name)}
                    className="accent-[color:var(--color-accent)]"
                  />
                  <Link
                    to="/kg/$entity"
                    params={{ entity: n.name }}
                    className="flex-1 truncate hover:underline"
                  >
                    {n.name}
                  </Link>
                  <span className="tabular-nums text-[11px] text-[color:var(--color-muted)]">
                    deg {n.degree}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
