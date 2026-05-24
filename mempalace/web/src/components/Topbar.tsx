import { Link } from "@tanstack/react-router";
import { CircleCheck, CircleAlert, CircleX, Loader2 } from "lucide-react";

import { useHealth } from "@/api/queries";
import { cn } from "@/lib/cn";

export function Topbar() {
  const health = useHealth();
  const overall = health.data?.overall;
  return (
    <div className="flex h-10 items-center justify-between border-b border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-4 text-[12px]">
      <div className="text-[color:var(--color-muted)]">
        local-loopback only · <code className="text-[color:var(--color-fg)]">127.0.0.1:9876</code>
      </div>
      <Link
        to="/health"
        className={cn(
          "flex items-center gap-1.5 rounded px-2 py-1 hover:bg-[color:var(--color-surface-2)]",
          overall === "fail" && "text-[color:var(--color-danger)]",
          overall === "warn" && "text-[color:var(--color-warn)]",
          overall === "ok" && "text-[color:var(--color-success)]",
        )}
        aria-label={`palace health: ${overall ?? "loading"}`}
      >
        {health.isPending ? (
          <Loader2 size={12} className="animate-spin" />
        ) : overall === "fail" ? (
          <CircleX size={12} />
        ) : overall === "warn" ? (
          <CircleAlert size={12} />
        ) : (
          <CircleCheck size={12} />
        )}
        <span>{overall ?? "…"}</span>
      </Link>
    </div>
  );
}
