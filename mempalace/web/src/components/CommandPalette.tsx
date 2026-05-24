import { Command } from "cmdk";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import {
  Activity,
  BookOpen,
  Building2,
  HeartPulse,
  Home,
  Network,
  Search as SearchIcon,
} from "lucide-react";

import { useKGGraph, usePalaceSummary } from "@/api/queries";

/**
 * ⌘K palette: jump to any wing, any KG entity, or any top-level page.
 *
 * Uses cmdk for the headless keyboard / focus / fuzzy-match logic and our
 * own Tailwind chrome.
 */
export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();

  const summary = usePalaceSummary();
  const graph = useKGGraph(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!open) return null;

  const pages = [
    { to: "/", label: "dashboard", Icon: Home },
    { to: "/wings", label: "wings", Icon: Building2 },
    { to: "/search", label: "search", Icon: SearchIcon },
    { to: "/ops", label: "operations", Icon: Activity },
    { to: "/kg", label: "knowledge graph", Icon: Network },
    { to: "/diary", label: "diary", Icon: BookOpen },
    { to: "/health", label: "health", Icon: HeartPulse },
  ] as const;

  const wings = summary.data
    ? Object.entries(summary.data.wings).sort((a, b) => b[1] - a[1]).slice(0, 200)
    : [];
  const entities = graph.data?.nodes.slice(0, 300) ?? [];

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 pt-[18vh]"
      onClick={() => setOpen(false)}
    >
      <Command
        label="command menu"
        className="w-[640px] max-w-[92vw] overflow-hidden rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface)] shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <Command.Input
          autoFocus
          placeholder="jump to…"
          className="w-full border-b border-[color:var(--color-border)] bg-transparent px-4 py-3 text-[14px] outline-none placeholder:text-[color:var(--color-muted)]"
        />
        <Command.List className="max-h-[60vh] overflow-auto p-2">
          <Command.Empty className="px-3 py-6 text-center text-[12px] text-[color:var(--color-muted)]">
            no matches.
          </Command.Empty>

          <Command.Group
            heading="pages"
            className="text-[10px] uppercase tracking-wide text-[color:var(--color-muted)]"
          >
            {pages.map(({ to, label, Icon }) => (
              <Command.Item
                key={to}
                value={`page ${label}`}
                onSelect={() => {
                  setOpen(false);
                  navigate({ to });
                }}
                className="flex cursor-pointer items-center gap-2 rounded px-3 py-2 text-[13px] aria-selected:bg-[color:var(--color-surface-2)]"
              >
                <Icon size={14} aria-hidden="true" />
                <span>{label}</span>
              </Command.Item>
            ))}
          </Command.Group>

          {wings.length > 0 && (
            <Command.Group
              heading="wings"
              className="mt-2 text-[10px] uppercase tracking-wide text-[color:var(--color-muted)]"
            >
              {wings.map(([w, count]) => (
                <Command.Item
                  key={`wing-${w}`}
                  value={`wing ${w}`}
                  onSelect={() => {
                    setOpen(false);
                    navigate({ to: "/wings/$wing", params: { wing: w } });
                  }}
                  className="flex cursor-pointer items-center gap-2 rounded px-3 py-1.5 text-[13px] aria-selected:bg-[color:var(--color-surface-2)]"
                >
                  <Building2 size={12} aria-hidden="true" />
                  <span>{w}</span>
                  <span className="ml-auto text-[11px] text-[color:var(--color-muted)] tabular-nums">
                    {count.toLocaleString()}
                  </span>
                </Command.Item>
              ))}
            </Command.Group>
          )}

          {entities.length > 0 && (
            <Command.Group
              heading="entities"
              className="mt-2 text-[10px] uppercase tracking-wide text-[color:var(--color-muted)]"
            >
              {entities.map((n) => (
                <Command.Item
                  key={`ent-${n.id}`}
                  value={`entity ${n.name} ${n.type}`}
                  onSelect={() => {
                    setOpen(false);
                    navigate({ to: "/kg/$entity", params: { entity: n.name } });
                  }}
                  className="flex cursor-pointer items-center gap-2 rounded px-3 py-1.5 text-[13px] aria-selected:bg-[color:var(--color-surface-2)]"
                >
                  <Network size={12} aria-hidden="true" />
                  <span>{n.name}</span>
                  <span className="ml-auto text-[11px] text-[color:var(--color-muted)]">
                    {n.type}
                  </span>
                </Command.Item>
              ))}
            </Command.Group>
          )}
        </Command.List>
        <div className="border-t border-[color:var(--color-border)] px-3 py-2 text-[10px] text-[color:var(--color-muted)]">
          ⏎ open · esc close · ⌘K toggle
        </div>
      </Command>
    </div>
  );
}
