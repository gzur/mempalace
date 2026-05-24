import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/api/client";
import { Breadcrumbs } from "@/components/Breadcrumbs";
import { cn } from "@/lib/cn";
import { formatInt, relativeTime } from "@/lib/format";

interface DiaryEntry {
  ts: string;
  content: string;
  topic?: string;
  wing?: string;
  room?: string;
}

interface DiaryResponse {
  entries: DiaryEntry[];
  total?: number;
  error?: string;
}

interface Search {
  agent?: string;
}

export const Route = createFileRoute("/diary")({
  component: DiaryPage,
  validateSearch: (s: Record<string, unknown>): Search => ({
    agent: typeof s.agent === "string" && s.agent ? s.agent : undefined,
  }),
});

function DiaryPage() {
  const { agent } = Route.useSearch();
  const navigate = Route.useNavigate();
  const [draft, setDraft] = useState(agent ?? "");

  const q = useQuery({
    queryKey: ["diary", agent ?? ""],
    queryFn: () =>
      api<DiaryResponse>("/api/v1/diary", {
        params: { agent: agent ?? "", last_n: 30 },
      }),
    enabled: !!agent,
  });

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <Breadcrumbs items={[{ label: "palace", to: "/" }, { label: "diary" }]} />
      <header>
        <h1 className="text-xl font-semibold">diary</h1>
        <p className="mt-1 text-[12px] text-[color:var(--color-muted)]">
          per-agent journal. each entry is a verbatim drawer under the agent's wing.
        </p>
      </header>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          navigate({ search: { agent: draft.trim() || undefined } });
        }}
        className="flex items-center gap-2 rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-3"
      >
        <span className="text-[12px] text-[color:var(--color-muted)]">agent</span>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="gzur, session-hook, …"
          className="flex-1 rounded border border-[color:var(--color-border)] bg-[color:var(--color-bg)] px-2 py-1 text-[13px] outline-none focus:border-[color:var(--color-accent)]"
        />
        <button
          type="submit"
          className="rounded border border-[color:var(--color-accent)] bg-[color:var(--color-accent)] px-3 py-1 text-[12px] text-[color:var(--color-accent-fg)]"
        >
          load
        </button>
      </form>

      {!agent && (
        <p className="text-[12px] text-[color:var(--color-muted)]">
          enter an agent name to load their last 30 entries.
        </p>
      )}

      {agent && q.isError && (
        <p className="text-[color:var(--color-danger)]">{(q.error as Error).message}</p>
      )}

      {agent && q.data?.error && (
        <p className="text-[color:var(--color-warn)]">{q.data.error}</p>
      )}

      {agent && q.data && !q.data.error && (
        <p className="text-[12px] text-[color:var(--color-muted)]">
          {formatInt(q.data.entries.length)} entries
        </p>
      )}

      <ol className="flex flex-col gap-2">
        {(q.data?.entries ?? []).map((e, i) => (
          <li
            key={`${e.ts}-${i}`}
            className={cn(
              "rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-3 py-2",
            )}
          >
            <div className="mb-1 flex items-baseline justify-between gap-2 text-[11px] text-[color:var(--color-muted)]">
              <span>{relativeTime(e.ts)}</span>
              {e.topic && <code>{e.topic}</code>}
            </div>
            <pre className="text-[12px] whitespace-pre-wrap">{e.content}</pre>
          </li>
        ))}
      </ol>
    </div>
  );
}
