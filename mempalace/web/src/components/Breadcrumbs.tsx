import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";

import type { ReactNode } from "react";

export type Crumb =
  | { label: string; to: string; params?: Record<string, string> }
  | { label: string };

export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav
      aria-label="breadcrumb"
      className="flex flex-wrap items-center gap-1 text-[12px] text-[color:var(--color-muted)]"
    >
      {items.map((c, i) => {
        const last = i === items.length - 1;
        let node: ReactNode;
        if ("to" in c && !last) {
          node = (
            <Link
              to={c.to}
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              params={c.params as any}
              className="hover:text-[color:var(--color-accent)] hover:underline"
            >
              {c.label}
            </Link>
          );
        } else {
          node = (
            <span className={last ? "text-[color:var(--color-fg)]" : undefined}>
              {c.label}
            </span>
          );
        }
        return (
          <span key={i} className="flex items-center gap-1">
            {node}
            {!last && <ChevronRight size={12} aria-hidden="true" />}
          </span>
        );
      })}
    </nav>
  );
}
