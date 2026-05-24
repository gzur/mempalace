import { cn } from "@/lib/cn";

export function StatCard({
  label,
  value,
  sublabel,
  tone = "default",
}: {
  label: string;
  value: string | number;
  sublabel?: string;
  tone?: "default" | "accent" | "success" | "warn" | "danger";
}) {
  const toneClass = {
    default: "text-[color:var(--color-fg)]",
    accent: "text-[color:var(--color-accent)]",
    success: "text-[color:var(--color-success)]",
    warn: "text-[color:var(--color-warn)]",
    danger: "text-[color:var(--color-danger)]",
  }[tone];
  return (
    <div className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-4">
      <div className="text-[11px] uppercase tracking-wide text-[color:var(--color-muted)]">
        {label}
      </div>
      <div className={cn("mt-1 text-2xl font-semibold", toneClass)}>{value}</div>
      {sublabel && (
        <div className="mt-0.5 text-[11px] text-[color:var(--color-muted)]">{sublabel}</div>
      )}
    </div>
  );
}
