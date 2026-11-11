/** Force-directed knowledge graph on a <canvas> (d3-force for layout, hand-drawn rendering). */
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, type Simulation } from "d3-force";
import { useEffect, useRef, useState } from "react";
import { hitTest, shapeGraph, type SimLink, type SimNode } from "../lib/graph";
import type { GraphData } from "../lib/types";

interface View {
  x: number;
  y: number;
  k: number;
}

export function GraphCanvas({ data, height = 520 }: { data: GraphData; height?: number }) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const view = useRef<View>({ x: 0, y: 0, k: 1 });
  const nodesRef = useRef<SimNode[]>([]);
  const drawRef = useRef<() => void>(() => {});
  const [width, setWidth] = useState(800);
  const [hover, setHover] = useState<{ n: SimNode; x: number; y: number } | null>(null);

  // Track container width.
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => e && setWidth(Math.max(320, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Build the simulation whenever the data changes.
  useEffect(() => {
    const { nodes, links } = shapeGraph(data);
    nodesRef.current = nodes;
    const sim: Simulation<SimNode, SimLink> = forceSimulation<SimNode, SimLink>(nodes)
      .force("link", forceLink<SimNode, SimLink>(links).id((d) => d.id).distance(36).strength(0.6))
      .force("charge", forceManyBody<SimNode>().strength(-55).distanceMax(320))
      .force("center", forceCenter(0, 0))
      .force("collide", forceCollide<SimNode>((d) => d.r + 2))
      .alphaDecay(0.035);

    const draw = () => {
      const c = canvas.current;
      const ctx = c?.getContext("2d");
      if (!c || !ctx) return;
      const dpr = window.devicePixelRatio || 1;
      const w = c.width / dpr;
      const h = c.height / dpr;
      const v = view.current;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      ctx.translate(w / 2 + v.x, h / 2 + v.y);
      ctx.scale(v.k, v.k);
      ctx.lineWidth = 0.8 / v.k;
      ctx.strokeStyle = "rgba(255,255,255,0.12)";
      ctx.beginPath();
      for (const l of links) {
        const a = l.source as SimNode;
        const b = l.target as SimNode;
        if (a.x == null || b.x == null) continue;
        ctx.moveTo(a.x, a.y!);
        ctx.lineTo(b.x, b.y!);
      }
      ctx.stroke();
      for (const n of nodes) {
        if (n.x == null) continue;
        ctx.beginPath();
        ctx.arc(n.x, n.y!, n.r, 0, Math.PI * 2);
        ctx.fillStyle = n.color;
        ctx.globalAlpha = 0.85;
        ctx.fill();
        ctx.globalAlpha = 1;
      }
      ctx.font = `${11 / v.k}px "Inter Variable", system-ui, sans-serif`;
      ctx.fillStyle = "rgba(255,255,255,0.82)";
      ctx.textAlign = "center";
      for (const n of nodes) {
        if (!n.label || n.x == null) continue;
        ctx.fillText(n.name.length > 24 ? `${n.name.slice(0, 23)}…` : n.name, n.x, n.y! - n.r - 3 / v.k);
      }
    };
    drawRef.current = draw;
    sim.on("tick", draw);
    return () => {
      sim.stop();
      sim.on("tick", null);
    };
  }, [data]);

  // Size the backing store for crisp lines.
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = width * dpr;
    c.height = height * dpr;
    drawRef.current();
  }, [width, height]);

  const toGraph = (clientX: number, clientY: number) => {
    const rect = canvas.current!.getBoundingClientRect();
    const v = view.current;
    return {
      x: (clientX - rect.left - rect.width / 2 - v.x) / v.k,
      y: (clientY - rect.top - rect.height / 2 - v.y) / v.k,
      px: clientX - rect.left,
      py: clientY - rect.top,
    };
  };

  // Pan with drag, zoom with wheel.
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const onPointerDown = (e: React.PointerEvent) => {
    drag.current = { x: e.clientX, y: e.clientY, vx: view.current.x, vy: view.current.y };
    (e.target as Element).setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (drag.current) {
      view.current.x = drag.current.vx + e.clientX - drag.current.x;
      view.current.y = drag.current.vy + e.clientY - drag.current.y;
      drawRef.current();
      setHover(null);
      return;
    }
    const p = toGraph(e.clientX, e.clientY);
    const n = hitTest(nodesRef.current, p.x, p.y);
    setHover(n ? { n, x: p.px, y: p.py } : null);
  };
  const onPointerUp = () => {
    drag.current = null;
  };
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const v = view.current;
      v.k = Math.max(0.25, Math.min(4, v.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
      drawRef.current();
    };
    c.addEventListener("wheel", onWheel, { passive: false });
    return () => c.removeEventListener("wheel", onWheel);
  }, []);

  return (
    <div ref={wrap} className="relative w-full overflow-hidden rounded-lg border border-white/[0.06] bg-black/40">
      <canvas
        ref={canvas}
        role="img"
        aria-label={`Knowledge graph with ${data.nodes.length} entities and ${data.edges.length} relations`}
        style={{ width, height, cursor: drag.current ? "grabbing" : hover ? "pointer" : "grab", touchAction: "none" }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => {
          onPointerUp();
          setHover(null);
        }}
      />
      {hover && (
        <div
          className="pointer-events-none absolute z-10 max-w-64 rounded-md border border-white/15 bg-[#0b0f15]/95 px-2.5 py-1.5 text-[12px] shadow-lg"
          style={{ left: Math.min(hover.x + 12, width - 200), top: hover.y + 12 }}
        >
          <div className="font-medium text-white">{hover.n.name}</div>
          <div className="text-white/50">
            {hover.n.type || "entity"} · {hover.n.mentions} mention{hover.n.mentions === 1 ? "" : "s"}
            {hover.n.community != null && ` · community ${hover.n.community}`}
          </div>
        </div>
      )}
      <div className="pointer-events-none absolute right-2 bottom-2 text-[11px] text-white/35">drag to pan · scroll to zoom</div>
    </div>
  );
}
