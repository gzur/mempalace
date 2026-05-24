import { useEffect, useMemo, useRef } from "react";
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
  source: string;
  target: string;
  predicate: string;
}

interface Props {
  graph: KGGraphResponse;
  onNodeClick?: (id: string, name: string) => void;
  highlightName?: string;
  /** CSS palette per entity type. Falls back to a deterministic colour. */
  typePalette?: Record<string, string>;
  height?: number;
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

export function KGGraph({ graph, onNodeClick, highlightName, typePalette, height = 600 }: Props) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fgRef = useRef<ForceGraphMethods<any, any> | undefined>(undefined);

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

  return (
    <div style={{ height }} className="overflow-hidden rounded border border-[color:var(--color-border)] bg-[color:var(--color-bg)]">
      <ForceGraph2D
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ref={fgRef as any}
        graphData={data}
        height={height}
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
          ctx.fillStyle = node.isHighlighted
            ? "#ffd60a"
            : colorFor(node.type);
          ctx.fill();
          if (node.isHighlighted) {
            ctx.lineWidth = 2 / scale;
            ctx.strokeStyle = "#fff";
            ctx.stroke();
          }
          if (scale > 1.6 || node.degree > 6 || node.isHighlighted) {
            ctx.fillStyle = "#c9d1d9";
            ctx.font = `${10 / scale}px ui-monospace, SFMono-Regular, Menlo, monospace`;
            ctx.textAlign = "center";
            ctx.textBaseline = "top";
            ctx.fillText(node.name, node.x, node.y + r + 1 / scale);
          }
        }}
        nodeLabel={(n) =>
          `${(n as Node).name} · ${(n as Node).type} · deg ${(n as Node).degree}`
        }
        linkLabel={(l) => (l as Link).predicate}
        onNodeClick={(n) => {
          const node = n as Node;
          onNodeClick?.(node.id, node.name);
        }}
        enableNodeDrag={false}
      />
    </div>
  );
}
