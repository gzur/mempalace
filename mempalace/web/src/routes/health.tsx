import { createFileRoute } from "@tanstack/react-router";
import { CircleAlert, CircleCheck, CircleX } from "lucide-react";

import { useHealth } from "@/api/queries";
import { Breadcrumbs } from "@/components/Breadcrumbs";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/health")({
  component: HealthPage,
});

function HealthPage() {
  const q = useHealth();
  const overall = q.data?.overall;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <Breadcrumbs items={[{ label: "palace", to: "/" }, { label: "health" }]} />

      <header className="flex items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">health</h1>
          <p className="mt-1 text-[12px] text-[color:var(--color-muted)]">
            auto-refreshes every 30s · same checks as <code>mempalace doctor</code>
          </p>
        </div>
        {overall && (
          <span
            className={cn(
              "rounded px-3 py-1 text-[12px]",
              overall === "fail" && "bg-[color:var(--color-danger)]/15 text-[color:var(--color-danger)]",
              overall === "warn" && "bg-[color:var(--color-warn)]/15 text-[color:var(--color-warn)]",
              overall === "ok" && "bg-[color:var(--color-success)]/15 text-[color:var(--color-success)]",
            )}
          >
            overall: {overall}
          </span>
        )}
      </header>

      {q.isError && (
        <p className="text-[color:var(--color-danger)]">{(q.error as Error).message}</p>
      )}

      <ul className="flex flex-col gap-2">
        {(q.data?.checks ?? []).map((c) => (
          <li
            key={c.name}
            className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-3 py-2"
          >
            <div className="flex items-center gap-2">
              <StatusIcon status={c.status} />
              <code className="text-[12px] font-semibold">{c.name}</code>
              <span
                className={cn(
                  "ml-1 rounded px-1.5 text-[10px] uppercase tracking-wide",
                  c.status === "fail" && "bg-[color:var(--color-danger)]/15 text-[color:var(--color-danger)]",
                  c.status === "warn" && "bg-[color:var(--color-warn)]/15 text-[color:var(--color-warn)]",
                  c.status === "ok" && "bg-[color:var(--color-success)]/15 text-[color:var(--color-success)]",
                )}
              >
                {c.status}
              </span>
            </div>
            <p className="mt-1 text-[12px] text-[color:var(--color-fg)]">{c.detail}</p>
            {c.value !== undefined && c.value !== null && (
              <p className="mt-0.5 text-[11px] text-[color:var(--color-muted)]">
                value: <code>{JSON.stringify(c.value)}</code>
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function StatusIcon({ status }: { status: string }) {
  if (status === "fail") {
    return <CircleX size={14} className="text-[color:var(--color-danger)]" aria-hidden="true" />;
  }
  if (status === "warn") {
    return <CircleAlert size={14} className="text-[color:var(--color-warn)]" aria-hidden="true" />;
  }
  return <CircleCheck size={14} className="text-[color:var(--color-success)]" aria-hidden="true" />;
}
