import { createFileRoute, Link } from "@tanstack/react-router";

import { useDrawers } from "@/api/queries";
import { Breadcrumbs } from "@/components/Breadcrumbs";
import { formatInt, truncate } from "@/lib/format";

const PAGE_SIZE = 50;

interface Search {
  offset?: number;
}

export const Route = createFileRoute("/wings/$wing/$room")({
  component: RoomPage,
  validateSearch: (search: Record<string, unknown>): Search => ({
    offset:
      typeof search.offset === "number"
        ? search.offset
        : typeof search.offset === "string"
          ? parseInt(search.offset, 10) || 0
          : 0,
  }),
});

function RoomPage() {
  const { wing, room } = Route.useParams();
  const { offset = 0 } = Route.useSearch();
  const drawers = useDrawers({ wing, room, offset, limit: PAGE_SIZE });

  const list = drawers.data?.drawers ?? [];
  const total = drawers.data?.total;
  const showing = `${offset + 1}–${Math.min(offset + list.length, offset + PAGE_SIZE)}`;
  const hasPrev = offset > 0;
  const hasNext = list.length === PAGE_SIZE;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4">
      <Breadcrumbs
        items={[
          { label: "palace", to: "/" },
          { label: "wings", to: "/wings" },
          { label: wing, to: "/wings/$wing", params: { wing } },
          { label: room },
        ]}
      />

      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">
            {wing} / {room}
          </h1>
          <p className="mt-1 text-[12px] text-[color:var(--color-muted)]">
            {drawers.isPending
              ? "loading…"
              : `showing ${showing}${total !== undefined ? ` of ${formatInt(total)}` : ""}`}
          </p>
        </div>
        <div className="flex gap-1 text-[12px]">
          {hasPrev && (
            <Link
              to="/wings/$wing/$room"
              params={{ wing, room }}
              search={{ offset: Math.max(0, offset - PAGE_SIZE) }}
              className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-3 py-1 hover:border-[color:var(--color-accent)]"
            >
              ← prev
            </Link>
          )}
          {hasNext && (
            <Link
              to="/wings/$wing/$room"
              params={{ wing, room }}
              search={{ offset: offset + PAGE_SIZE }}
              className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-3 py-1 hover:border-[color:var(--color-accent)]"
            >
              next →
            </Link>
          )}
        </div>
      </header>

      <ul className="flex flex-col gap-1">
        {list.map((d) => (
          <li key={d.drawer_id}>
            <Link
              to="/drawers/$id"
              params={{ id: d.drawer_id }}
              className="flex flex-col gap-1 rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-3 py-2 transition-colors hover:border-[color:var(--color-accent)]"
            >
              <div className="flex items-center justify-between gap-2">
                <code className="truncate text-[11px] text-[color:var(--color-muted)]">
                  {d.drawer_id}
                </code>
              </div>
              <p className="text-[12px] text-[color:var(--color-fg)]">
                {truncate(d.content_preview, 200)}
              </p>
            </Link>
          </li>
        ))}
      </ul>

      {!drawers.isPending && list.length === 0 && (
        <p className="text-[12px] text-[color:var(--color-muted)]">no drawers in this room.</p>
      )}
    </div>
  );
}
