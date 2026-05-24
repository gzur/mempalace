import { createFileRoute, Link } from "@tanstack/react-router";

import { usePalaceWings } from "@/api/queries";
import { formatInt } from "@/lib/format";

export const Route = createFileRoute("/wings")({
  component: WingsList,
});

function WingsList() {
  const wings = usePalaceWings();

  const entries = wings.data
    ? Object.entries(wings.data.wings).sort((a, b) => b[1] - a[1])
    : [];

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <header>
        <h1 className="text-xl font-semibold">wings</h1>
        <p className="mt-1 text-[12px] text-[color:var(--color-muted)]">
          {wings.data
            ? `${entries.length} wings · ${formatInt(
                entries.reduce((s, [, n]) => s + n, 0),
              )} drawers total`
            : "loading…"}
        </p>
      </header>

      {wings.isError && (
        <p className="text-[color:var(--color-danger)]">
          failed to load wings: {(wings.error as Error).message}
        </p>
      )}

      <ul className="grid gap-2 sm:grid-cols-2">
        {entries.map(([name, count]) => (
          <li key={name}>
            <Link
              to="/wings/$wing"
              params={{ wing: name }}
              className="flex items-center justify-between rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-3 py-2 transition-colors hover:border-[color:var(--color-accent)]"
            >
              <span className="truncate font-medium">{name}</span>
              <span className="text-[color:var(--color-muted)] tabular-nums">
                {formatInt(count)}
              </span>
            </Link>
          </li>
        ))}
      </ul>
      {!wings.isPending && entries.length === 0 && (
        <p className="text-[12px] text-[color:var(--color-muted)]">
          no wings yet — add a drawer with <code>mempalace_add_drawer</code> to create one.
        </p>
      )}
    </div>
  );
}
