import { Link, useRouterState } from "@tanstack/react-router";
import {
  Activity,
  BookOpen,
  Building2,
  HeartPulse,
  Home,
  Network,
  Search,
} from "lucide-react";

import { cn } from "@/lib/cn";

type NavItem = {
  to: string;
  label: string;
  Icon: typeof Home;
};

const items: NavItem[] = [
  { to: "/", label: "dashboard", Icon: Home },
  { to: "/wings", label: "wings", Icon: Building2 },
  { to: "/search", label: "search", Icon: Search },
  { to: "/ops", label: "operations", Icon: Activity },
  { to: "/kg", label: "knowledge graph", Icon: Network },
  { to: "/diary", label: "diary", Icon: BookOpen },
  { to: "/health", label: "health", Icon: HeartPulse },
];

export function Sidebar() {
  const router = useRouterState({ select: (s) => s.location.pathname });
  return (
    <nav className="flex h-full w-56 flex-col border-r border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-3">
      <div className="mb-4 px-2">
        <div className="text-sm font-semibold tracking-wide">mempalace</div>
        <div className="text-[11px] text-[color:var(--color-muted)]">local memory</div>
      </div>
      <ul className="flex flex-1 flex-col gap-0.5">
        {items.map(({ to, label, Icon }) => {
          const active =
            to === "/"
              ? router === "/" || router === ""
              : router === to || router.startsWith(to + "/");
          return (
            <li key={to}>
              <Link
                to={to}
                className={cn(
                  "flex items-center gap-2 rounded px-2 py-1.5 text-sm transition-colors",
                  active
                    ? "bg-[color:var(--color-surface-2)] text-[color:var(--color-fg)]"
                    : "text-[color:var(--color-muted)] hover:bg-[color:var(--color-surface-2)] hover:text-[color:var(--color-fg)]",
                )}
              >
                <Icon size={14} aria-hidden="true" />
                <span>{label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
      <div className="border-t border-[color:var(--color-border)] pt-3 text-[10px] text-[color:var(--color-muted)]">
        <a href="/ui/" className="hover:underline">
          legacy /ui →
        </a>
      </div>
    </nav>
  );
}
