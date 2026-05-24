import { createFileRoute } from "@tanstack/react-router";

import { useDrawer } from "@/api/queries";
import { Breadcrumbs } from "@/components/Breadcrumbs";

export const Route = createFileRoute("/drawers/$id")({
  component: DrawerPage,
});

function DrawerPage() {
  const { id } = Route.useParams();
  const q = useDrawer(id);
  const d = q.data;

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <Breadcrumbs
        items={
          d && !d.error
            ? [
                { label: "palace", to: "/" },
                { label: "wings", to: "/wings" },
                ...(d.wing
                  ? [{ label: d.wing, to: "/wings/$wing", params: { wing: d.wing } } as const]
                  : []),
                ...(d.wing && d.room
                  ? [
                      {
                        label: d.room,
                        to: "/wings/$wing/$room" as const,
                        params: { wing: d.wing, room: d.room },
                      },
                    ]
                  : []),
                { label: id.slice(0, 32) + (id.length > 32 ? "…" : "") },
              ]
            : [{ label: "palace", to: "/" }, { label: "drawer" }]
        }
      />

      <header>
        <h1 className="text-xl font-semibold">drawer</h1>
        <p className="mt-1 break-all text-[11px] text-[color:var(--color-muted)]">
          <code>{id}</code>
        </p>
      </header>

      {q.isError && (
        <p className="text-[color:var(--color-danger)]">
          failed to load: {(q.error as Error).message}
        </p>
      )}

      {d?.error && (
        <p className="text-[color:var(--color-warn)]">{d.error}</p>
      )}

      {d && !d.error && (
        <>
          <section>
            <h2 className="mb-2 text-[11px] uppercase tracking-wide text-[color:var(--color-muted)]">
              content
            </h2>
            <pre className="overflow-auto rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-4 text-[13px] whitespace-pre-wrap">
              {d.content}
            </pre>
          </section>

          <section>
            <h2 className="mb-2 text-[11px] uppercase tracking-wide text-[color:var(--color-muted)]">
              metadata
            </h2>
            <table className="w-full border-collapse text-[12px]">
              <tbody>
                {Object.entries(d.metadata)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([k, v]) => (
                    <tr key={k} className="border-b border-[color:var(--color-border)]">
                      <th className="w-1/3 py-1.5 pr-3 text-left font-medium text-[color:var(--color-muted)]">
                        {k}
                      </th>
                      <td className="break-all py-1.5">{String(v)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  );
}
