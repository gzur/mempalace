import { createFileRoute, Link } from "@tanstack/react-router";

import { usePalaceRooms } from "@/api/queries";
import { Breadcrumbs } from "@/components/Breadcrumbs";
import { formatInt } from "@/lib/format";

export const Route = createFileRoute("/wings/$wing")({
  component: WingPage,
});

function WingPage() {
  const { wing } = Route.useParams();
  const rooms = usePalaceRooms(wing);

  const entries = rooms.data
    ? Object.entries(rooms.data.rooms).sort((a, b) => b[1] - a[1])
    : [];

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <Breadcrumbs
        items={[
          { label: "palace", to: "/" },
          { label: "wings", to: "/wings" },
          { label: wing },
        ]}
      />

      <header>
        <h1 className="text-xl font-semibold">wing · {wing}</h1>
        <p className="mt-1 text-[12px] text-[color:var(--color-muted)]">
          {rooms.data
            ? `${entries.length} rooms · ${formatInt(
                entries.reduce((s, [, n]) => s + n, 0),
              )} drawers in this wing`
            : "loading rooms…"}
        </p>
      </header>

      <ul className="grid gap-2 sm:grid-cols-2">
        {entries.map(([room, count]) => (
          <li key={room}>
            <Link
              to="/wings/$wing/$room"
              params={{ wing, room }}
              className="flex items-center justify-between rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-3 py-2 transition-colors hover:border-[color:var(--color-accent)]"
            >
              <span className="truncate font-medium">{room}</span>
              <span className="text-[color:var(--color-muted)] tabular-nums">
                {formatInt(count)}
              </span>
            </Link>
          </li>
        ))}
      </ul>

      {!rooms.isPending && entries.length === 0 && (
        <p className="text-[12px] text-[color:var(--color-muted)]">no rooms in this wing.</p>
      )}
    </div>
  );
}
