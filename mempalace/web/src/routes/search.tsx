import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";

import { useSearch } from "@/api/queries";
import { Breadcrumbs } from "@/components/Breadcrumbs";
import { cn } from "@/lib/cn";
import { truncate } from "@/lib/format";

interface Search {
  q?: string;
  mode?: "semantic" | "fts";
  wing?: string;
  room?: string;
}

export const Route = createFileRoute("/search")({
  component: SearchPage,
  validateSearch: (s: Record<string, unknown>): Search => ({
    q: typeof s.q === "string" ? s.q : undefined,
    mode: s.mode === "fts" ? "fts" : "semantic",
    wing: typeof s.wing === "string" && s.wing ? s.wing : undefined,
    room: typeof s.room === "string" && s.room ? s.room : undefined,
  }),
});

function SearchPage() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const [draft, setDraft] = useState(search.q ?? "");
  const [wing, setWing] = useState(search.wing ?? "");
  const [room, setRoom] = useState(search.room ?? "");

  const q = search.q ?? "";
  const mode = search.mode ?? "semantic";
  const results = useSearch({
    q,
    mode,
    wing: search.wing,
    room: search.room,
    limit: 20,
  });

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    navigate({
      search: {
        q: draft.trim() || undefined,
        mode,
        wing: wing.trim() || undefined,
        room: room.trim() || undefined,
      },
    });
  };

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <Breadcrumbs items={[{ label: "palace", to: "/" }, { label: "search" }]} />

      <header>
        <h1 className="text-xl font-semibold">search</h1>
        <p className="mt-1 text-[12px] text-[color:var(--color-muted)]">
          {mode === "semantic"
            ? "vector + lexical hybrid via the chromadb collection"
            : "FTS5 trigram substring match — crash-proof fallback"}
        </p>
      </header>

      <form
        onSubmit={onSubmit}
        className="flex flex-col gap-2 rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-3"
      >
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="search drawers…"
          className="w-full rounded border border-[color:var(--color-border)] bg-[color:var(--color-bg)] px-2 py-1.5 text-[13px] outline-none focus:border-[color:var(--color-accent)]"
        />
        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          <span className="text-[color:var(--color-muted)]">mode</span>
          {(["semantic", "fts"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() =>
                navigate({
                  search: {
                    q: q || undefined,
                    mode: m,
                    wing: search.wing,
                    room: search.room,
                  },
                })
              }
              className={cn(
                "rounded border px-2 py-0.5 transition-colors",
                m === mode
                  ? "border-[color:var(--color-accent)] text-[color:var(--color-accent)]"
                  : "border-[color:var(--color-border)] text-[color:var(--color-muted)] hover:border-[color:var(--color-fg)]",
              )}
            >
              {m}
            </button>
          ))}
          <span className="ml-3 text-[color:var(--color-muted)]">wing</span>
          <input
            value={wing}
            onChange={(e) => setWing(e.target.value)}
            placeholder="any"
            className="w-28 rounded border border-[color:var(--color-border)] bg-[color:var(--color-bg)] px-1.5 py-0.5"
          />
          <span className="text-[color:var(--color-muted)]">room</span>
          <input
            value={room}
            onChange={(e) => setRoom(e.target.value)}
            placeholder="any"
            className="w-28 rounded border border-[color:var(--color-border)] bg-[color:var(--color-bg)] px-1.5 py-0.5"
          />
          <button
            type="submit"
            className="ml-auto rounded border border-[color:var(--color-accent)] bg-[color:var(--color-accent)] px-3 py-1 text-[color:var(--color-accent-fg)]"
          >
            search
          </button>
        </div>
      </form>

      {!q && (
        <p className="text-[12px] text-[color:var(--color-muted)]">
          type a query and hit enter.
        </p>
      )}

      {q && results.isPending && (
        <p className="text-[12px] text-[color:var(--color-muted)]">searching…</p>
      )}

      {q && results.isError && (
        <p className="text-[color:var(--color-danger)]">
          search failed: {(results.error as Error).message}
        </p>
      )}

      {q && results.data && (
        <p className="text-[12px] text-[color:var(--color-muted)]">
          {results.data.results.length} result
          {results.data.results.length === 1 ? "" : "s"}
        </p>
      )}

      <ul className="flex flex-col gap-2">
        {results.data?.results.map((hit, i) => {
          const id = hit.drawer_id ?? hit.id ?? `result-${i}`;
          const snippet = hit.snippet ?? hit.content ?? hit.text ?? "";
          return (
            <li key={id}>
              <Link
                to="/drawers/$id"
                params={{ id }}
                className="flex flex-col gap-1 rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-3 py-2 transition-colors hover:border-[color:var(--color-accent)]"
              >
                <div className="flex items-center justify-between gap-2 text-[11px] text-[color:var(--color-muted)]">
                  <code className="truncate">{id}</code>
                  <span>
                    {hit.wing}/{hit.room}
                  </span>
                </div>
                <p className="text-[12px]">{truncate(snippet, 280)}</p>
              </Link>
            </li>
          );
        })}
      </ul>

      {q &&
        results.data?.kg_entities &&
        results.data.kg_entities.length > 0 && (
          <section className="mt-2">
            <h2 className="mb-2 text-[11px] uppercase tracking-wide text-[color:var(--color-muted)]">
              knowledge graph entities ({results.data.kg_entities.length})
            </h2>
            <p className="mb-2 text-[11px] text-[color:var(--color-muted)]">
              No drawers matched, but these entities exist in the KG with a
              matching name.
            </p>
            <ul className="flex flex-col gap-1">
              {results.data.kg_entities.map((e) => (
                <li key={e.id}>
                  <Link
                    to="/kg/$entity"
                    params={{ entity: e.name }}
                    className="flex items-center justify-between gap-2 rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-3 py-2 text-[12px] transition-colors hover:border-[color:var(--color-accent)]"
                  >
                    <span className="truncate font-medium">{e.name}</span>
                    <span className="flex shrink-0 items-center gap-2 text-[color:var(--color-muted)]">
                      <code className="text-[color:var(--color-accent)]">
                        {e.type}
                      </code>
                      <span className="tabular-nums">deg {e.degree}</span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}
    </div>
  );
}
