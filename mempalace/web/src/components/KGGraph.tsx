import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods } from "react-force-graph-2d";

import type { KGGraphResponse } from "@/api/types";

/**
 * Force-directed canvas view of the knowledge graph.
 *
 * Wraps ``react-force-graph-2d`` (which wraps d3-force + canvas) so the
 * page-level code only deals with our own typed Node/Link shapes.
 *
 * Performance budget: tested up to ~2,000 nodes interactively. Above
 * that the page should pass ``maxNodes`` to the API and show a truncate
 * banner.
 */

interface Node {
  id: string;
  name: string;
  type: string;
  degree: number;
  isFocus?: boolean;
  isHighlighted?: boolean;
}

interface Link {
  source: string | { id: string };
  target: string | { id: string };
  predicate: string;
}

/** ``auto`` = density-based heuristic; ``all`` = always show; ``off`` = only
 *  the hovered node's label. Applies to both node and edge labels. */
export type LabelsMode = "auto" | "all" | "off";

interface Props {
  graph: KGGraphResponse;
  onNodeClick?: (id: string, name: string) => void;
  highlightName?: string;
  /** CSS palette per entity type. Falls back to a deterministic colour. */
  typePalette?: Record<string, string>;
  /** Fixed pixel height, or ``"fill"`` to stretch to the parent
   *  container's height (uses a ResizeObserver on the wrapper). */
  height?: number | "fill";
  labelsMode?: LabelsMode;
}

const FALLBACK_PALETTE = [
  "#58a6ff",
  "#3fb950",
  "#d29922",
  "#bc8cff",
  "#f85149",
  "#56d4dd",
  "#ff7b72",
  "#a5d6ff",
];

export function KGGraph({
  graph,
  onNodeClick,
  highlightName,
  typePalette,
  height = 600,
  labelsMode = "auto",
}: Props) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fgRef = useRef<ForceGraphMethods<any, any> | undefined>(undefined);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [measuredHeight, setMeasuredHeight] = useState<number>(600);
  const fillMode = height === "fill";

  // When asked to fill, track the container's height via ResizeObserver
  // and feed that number to ForceGraph2D (the canvas needs a pixel value).
  useEffect(() => {
    if (!fillMode) return;
    const el = containerRef.current;
    if (!el) return;
    const apply = () => {
      const next = el.clientHeight;
      if (next > 0) setMeasuredHeight(next);
    };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, [fillMode]);

  const [hovered, setHovered] = useState<Node | null>(null);
  const [hoveredLink, setHoveredLink] = useState<Link | null>(null);
  const canvasHeight =
    fillMode || typeof height !== "number" ? measuredHeight : height;

  const data = useMemo(() => {
    const lowerHighlight = highlightName?.toLowerCase().trim();
    const nodes: Node[] = graph.nodes.map((n) => ({
      id: n.id,
      name: n.name,
      type: n.type,
      degree: n.degree,
      isHighlighted: lowerHighlight
        ? n.name.toLowerCase().includes(lowerHighlight)
        : false,
    }));
    const links: Link[] = graph.edges.map((e) => ({
      source: e.s,
      target: e.o,
      predicate: e.p,
    }));
    return { nodes, links };
  }, [graph, highlightName]);

  const colorFor = useMemo(() => {
    const palette = typePalette ?? {};
    const cache: Record<string, string> = {};
    return (type: string) => {
      if (palette[type]) return palette[type];
      if (cache[type]) return cache[type];
      const idx = Object.keys(cache).length % FALLBACK_PALETTE.length;
      cache[type] = FALLBACK_PALETTE[idx];
      return cache[type];
    };
  }, [typePalette]);

  // Re-centre when the dataset changes.
  useEffect(() => {
    const id = setTimeout(() => {
      fgRef.current?.zoomToFit(400, 60);
    }, 300);
    return () => clearTimeout(id);
  }, [graph]);

  const shouldShowNodeLabel = (node: Node, scale: number) => {
    if (labelsMode === "off") return false;
    if (labelsMode === "all") return true;
    return scale > 1.6 || node.degree > 6 || !!node.isHighlighted;
  };

  const shouldShowEdgeLabel = (scale: number) => {
    if (labelsMode === "off") return false;
    if (labelsMode === "all") return true;
    // ``auto`` for edges: only at high zoom where they won't blanket the
    // whole canvas with overlapping predicate text.
    return scale > 2.4;
  };

  return (
    <div
      ref={containerRef}
      style={fillMode ? { height: "100%" } : { height }}
      className="relative overflow-hidden rounded border border-[color:var(--color-border)] bg-[color:var(--color-bg)]"
    >
      <ForceGraph2D
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ref={fgRef as any}
        graphData={data}
        height={canvasHeight}
        backgroundColor="rgba(0,0,0,0)"
        cooldownTicks={120}
        nodeRelSize={4}
        nodeVal={(n) => Math.max(1, Math.log2((n as Node).degree + 1))}
        linkColor={() => "rgba(139, 148, 158, 0.25)"}
        linkWidth={0.6}
        linkDirectionalArrowLength={3}
        linkDirectionalArrowRelPos={1}
        nodeCanvasObject={(rawNode, ctx, scale) => {
          const node = rawNode as Node & { x?: number; y?: number };
          if (node.x === undefined || node.y === undefined) return;
          const r = Math.max(2, Math.log2(node.degree + 1) * 1.5);
          ctx.beginPath();
          ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
          ctx.fillStyle = node.isHighlighted ? "#ffd60a" : colorFor(node.type);
          ctx.fill();
          if (node.isHighlighted) {
            ctx.lineWidth = 2 / scale;
            ctx.strokeStyle = "#fff";
            ctx.stroke();
          }
          if (shouldShowNodeLabel(node, scale)) {
            // Font size in world units = 10 / scale → constant ~10px on
            // screen at any zoom. Clamping (Math.max) here would peg the
            // value in world units and make labels GROW with zoom.
            const fontSize = 10 / scale;
            ctx.fillStyle = "#c9d1d9";
            ctx.font = `${fontSize}px ui-monospace, SFMono-Regular, Menlo, monospace`;
            ctx.textAlign = "center";
            ctx.textBaseline = "top";
            ctx.fillText(node.name, node.x, node.y + r + 1 / scale);
          }
        }}
        linkCanvasObjectMode={() => "after"}
        linkCanvasObject={(rawLink, ctx, scale) => {
          if (!shouldShowEdgeLabel(scale)) return;
          const link = rawLink as Link & {
            source: { x?: number; y?: number };
            target: { x?: number; y?: number };
          };
          const sx = link.source.x;
          const sy = link.source.y;
          const tx = link.target.x;
          const ty = link.target.y;
          if (sx === undefined || sy === undefined || tx === undefined || ty === undefined) {
            return;
          }
          const mx = (sx + tx) / 2;
          const my = (sy + ty) / 2;
          // Constant ~8px screen size regardless of zoom (no Math.max clamp,
          // which would force world-unit size and grow with zoom).
          const fontSize = 8 / scale;
          ctx.font = `${fontSize}px ui-monospace, SFMono-Regular, Menlo, monospace`;
          const label = link.predicate;
          const padding = 2 / scale;
          const w = ctx.measureText(label).width + padding * 2;
          const h = fontSize + padding * 2;
          ctx.fillStyle = "rgba(13, 17, 23, 0.85)";
          ctx.fillRect(mx - w / 2, my - h / 2, w, h);
          ctx.fillStyle = "#8b949e";
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(label, mx, my);
        }}
        onNodeHover={(n) => setHovered((n as Node | null) ?? null)}
        onLinkHover={(l) => setHoveredLink((l as Link | null) ?? null)}
        onNodeClick={(n) => {
          const node = n as Node;
          onNodeClick?.(node.id, node.name);
        }}
        enableNodeDrag={false}
      />
      {(hovered || hoveredLink) && (
        <div className="pointer-events-none absolute top-2 right-2 max-w-[60%] rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-3 py-2 text-[11px] shadow-lg">
          {hovered && (
            <div className="flex flex-col gap-0.5">
              <div className="font-semibold text-[color:var(--color-fg)]">
                {hovered.name}
              </div>
              <div className="text-[color:var(--color-muted)]">
                <span className="text-[color:var(--color-accent)]">
                  {hovered.type || "unknown"}
                </span>
                <span> · deg {hovered.degree}</span>
              </div>
              <div className="text-[10px] text-[color:var(--color-muted)]">
                click to open
              </div>
            </div>
          )}
          {!hovered && hoveredLink && (
            <div className="flex flex-col gap-0.5">
              <div className="text-[color:var(--color-muted)]">edge</div>
              <code className="text-[color:var(--color-accent)]">
                {hoveredLink.predicate}
              </code>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
