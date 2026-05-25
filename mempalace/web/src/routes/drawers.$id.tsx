import { createFileRoute } from "@tanstack/react-router";

import { useDrawer } from "@/api/queries";
import { Breadcrumbs } from "@/components/Breadcrumbs";
import { relativeTime } from "@/lib/format";

export const Route = createFileRoute("/drawers/$id")({
  component: DrawerPage,
});

type Provenance = Record<string, string | number | boolean>;

const PROVENANCE_FIELDS: Array<{ key: keyof Provenance | string; label: string }> = [
  { key: "client", label: "client" },
  { key: "added_by", label: "added by" },
  { key: "filed_at", label: "filed" },
  { key: "source_file", label: "source" },
];

function DrawerProvenance({ metadata }: { metadata: Provenance }) {
  const present = PROVENANCE_FIELDS.map(({ key, label }) => {
    const v = metadata[key];
    if (v === undefined || v === null || v === "") return null;
    let display = String(v);
    if (key === "filed_at") {
      display = `${display} · ${relativeTime(display)}`;
    }
    return { label, value: display };
  }).filter((x): x is { label: string; value: string } => x !== null);

  if (present.length === 0) return null;
  return (
    <section className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-3">
      <h2 className="mb-2 text-[11px] uppercase tracking-wide text-[color:var(--color-muted)]">
        provenance
      </h2>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-[12px]">
        {present.map(({ label, value }) => (
          <div key={label} className="contents">
            <dt className="text-[color:var(--color-muted)]">{label}</dt>
            <dd className="break-all">
              <code>{value}</code>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

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
          <DrawerProvenance metadata={d.metadata} />

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
              all metadata
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
