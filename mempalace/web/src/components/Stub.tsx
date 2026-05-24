/**
 * Placeholder body for routes that exist for navigation but aren't
 * implemented yet. The legacy /ui/* page is linked so users can fall
 * back during the rewrite.
 */
export function Stub({ title, legacy }: { title: string; legacy?: string }) {
  return (
    <div className="mx-auto flex max-w-2xl flex-col items-start gap-3">
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="text-[13px] text-[color:var(--color-muted)]">
        Not implemented yet in the new SPA. Coming in a later phase of the rewrite.
      </p>
      {legacy && (
        <a
          href={legacy}
          className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-3 py-1.5 text-[12px] hover:bg-[color:var(--color-surface-2)]"
        >
          open legacy {legacy} →
        </a>
      )}
    </div>
  );
}
