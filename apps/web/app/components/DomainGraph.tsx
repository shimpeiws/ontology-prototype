/**
 * Compact visual model of the draft domain: entity cards with their typed
 * properties, event cards bound to subjects, projection cards, and dashed
 * ghost cards for referenced-but-undeclared entities. Edges are drawn as
 * SVG beziers between the rendered cards.
 *
 * The graph is one rendering of the same parsed draft that drives the list
 * and the forms (INV-003) — the inspectable list beside it carries the
 * identical information in text form (INV-008). Cards are keyboard-operable
 * buttons that select the concept for editing.
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type { DomainDraftView } from "../lib/domainEdit";

export interface GraphSelection {
  kind: "entity" | "event" | "projection";
  name: string;
}

interface Link {
  /** Node id of the edge source, e.g. `entity:Job`. */
  from: string;
  to: string;
  label: string;
  style: "ref" | "subject";
}

interface Segment {
  path: string;
  /** Label anchor point. */
  lx: number;
  ly: number;
  label: string;
  style: Link["style"];
}

function links(view: DomainDraftView): Link[] {
  const out: Link[] = [];
  const entityNames = new Set(view.entities.map((e) => e.name));
  const nodeFor = (name: string) =>
    entityNames.has(name) ? `entity:${name}` : `missing:${name}`;
  for (const entity of view.entities) {
    for (const prop of entity.properties) {
      if (prop.type === "ref" && prop.ref) {
        out.push({
          from: `entity:${entity.name}`,
          to: nodeFor(prop.ref),
          label: prop.name,
          style: "ref",
        });
      }
    }
  }
  for (const event of view.events) {
    for (const prop of event.properties) {
      if (prop.type === "ref" && prop.ref) {
        out.push({
          from: `event:${event.name}`,
          to: nodeFor(prop.ref),
          label: prop.name,
          style: "ref",
        });
      }
    }
    if (event.subject) {
      out.push({
        from: `event:${event.name}`,
        to: nodeFor(event.subject),
        label: "subject",
        style: "subject",
      });
    }
  }
  for (const proj of view.projections) {
    if (proj.subject) {
      out.push({
        from: `projection:${proj.name}`,
        to: nodeFor(proj.subject),
        label: "subject",
        style: "subject",
      });
    }
  }
  return out;
}

/** Referenced entity names with no declaration — rendered as ghost cards. */
function missingTargets(view: DomainDraftView): string[] {
  const declared = new Set(view.entities.map((e) => e.name));
  const missing = new Set<string>();
  for (const entity of view.entities) {
    for (const p of entity.properties)
      if (p.type === "ref" && p.ref && !declared.has(p.ref)) missing.add(p.ref);
  }
  for (const event of view.events) {
    if (event.subject && !declared.has(event.subject)) missing.add(event.subject);
    for (const p of event.properties)
      if (p.type === "ref" && p.ref && !declared.has(p.ref)) missing.add(p.ref);
  }
  for (const proj of view.projections) {
    if (proj.subject && !declared.has(proj.subject)) missing.add(proj.subject);
  }
  return [...missing].sort();
}

const CURVE = 44;

function segmentBetween(
  s: DOMRect,
  t: DOMRect,
  container: DOMRect,
): { path: string; lx: number; ly: number } {
  const sx = s.left - container.left;
  const sy = s.top - container.top;
  const tx = t.left - container.left;
  const ty = t.top - container.top;
  const sCx = sx + s.width / 2;
  const sCy = sy + s.height / 2;
  const tCx = tx + t.width / 2;
  const tCy = ty + t.height / 2;

  if (s.left === t.left && s.top === t.top) {
    // Self-reference: a loop off the card's right edge.
    const x = sx + s.width;
    const y1 = sy + s.height * 0.3;
    const y2 = sy + s.height * 0.7;
    return {
      path: `M ${x} ${y1} C ${x + CURVE} ${y1}, ${x + CURVE} ${y2}, ${x} ${y2}`,
      lx: x + CURVE * 0.8,
      ly: (y1 + y2) / 2,
    };
  }

  const dx = tCx - sCx;
  const dy = tCy - sCy;
  let x1: number, y1: number, x2: number, y2: number;
  let o1x = 0, o1y = 0, o2x = 0, o2y = 0;
  if (Math.abs(dx) >= Math.abs(dy)) {
    const dir = dx > 0 ? 1 : -1;
    x1 = dir > 0 ? sx + s.width : sx;
    y1 = sCy;
    x2 = dir > 0 ? tx : tx + t.width;
    y2 = tCy;
    o1x = CURVE * dir;
    o2x = CURVE * dir;
  } else {
    const dir = dy > 0 ? 1 : -1;
    x1 = sCx;
    y1 = dir > 0 ? sy + s.height : sy;
    x2 = tCx;
    y2 = dir > 0 ? ty : ty + t.height;
    o1y = CURVE * dir;
    o2y = CURVE * dir;
  }
  return {
    path: `M ${x1} ${y1} C ${x1 + o1x} ${y1 + o1y}, ${x2 + o2x} ${y2 + o2y}, ${x2} ${y2}`,
    lx: (x1 + x2) / 2 + o1x * 0.15,
    ly: (y1 + y2) / 2 + o1y * 0.15,
  };
}

function cardKeyDown(
  onSelect: (sel: GraphSelection) => void,
  sel: GraphSelection,
) {
  return (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelect(sel);
    }
  };
}

export function DomainGraph({
  view,
  selected,
  onSelect,
}: {
  view: DomainDraftView;
  selected: GraphSelection | null;
  onSelect: (sel: GraphSelection) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const nodeEls = useRef(new Map<string, HTMLElement>());
  const [segments, setSegments] = useState<Segment[]>([]);
  const [canvasSize, setCanvasSize] = useState({ w: 0, h: 0 });

  const missing = missingTargets(view);
  // Memoized: `measure` depends on this and setSegments re-renders — a fresh
  // array each render would loop forever.
  const edgeLinks = useMemo(() => links(view), [view]);

  const setNode = useCallback(
    (id: string) => (el: HTMLElement | null) => {
      if (el) nodeEls.current.set(id, el);
      else nodeEls.current.delete(id);
    },
    [],
  );

  const measure = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const cr = container.getBoundingClientRect();
    setCanvasSize({ w: cr.width, h: cr.height });
    const segs: Segment[] = [];
    for (const link of edgeLinks) {
      const from = nodeEls.current.get(link.from);
      const to = nodeEls.current.get(link.to);
      if (!from || !to) continue;
      const seg = segmentBetween(
        from.getBoundingClientRect(),
        to.getBoundingClientRect(),
        cr,
      );
      segs.push({ ...seg, label: link.label, style: link.style });
    }
    setSegments(segs);
  }, [edgeLinks]);

  useLayoutEffect(measure, [measure]);
  useEffect(() => {
    window.addEventListener("resize", measure);
    const ro =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(measure)
        : null;
    if (ro && containerRef.current) ro.observe(containerRef.current);
    return () => {
      window.removeEventListener("resize", measure);
      ro?.disconnect();
    };
  }, [measure]);

  const cardClass = (kind: GraphSelection["kind"], name: string) =>
    `graph-card graph-card-${kind}` +
    (selected?.kind === kind && selected.name === name ? " selected" : "");

  return (
    <div className="domain-graph">
      <div className="graph-canvas" ref={containerRef}>
        <svg
          className="graph-edges"
          aria-hidden="true"
          width={canvasSize.w}
          height={canvasSize.h}
        >
          <defs>
            <marker
              id="graph-arrow"
              viewBox="0 0 8 8"
              refX="7"
              refY="4"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 8 4 L 0 8 z" className="graph-arrowhead" />
            </marker>
          </defs>
          {segments.map((s, i) => (
            <g key={i}>
              <path
                d={s.path}
                className={`graph-edge graph-edge-${s.style}`}
                markerEnd="url(#graph-arrow)"
                fill="none"
              />
              <text x={s.lx} y={s.ly} className="graph-edge-label" textAnchor="middle">
                {s.label}
              </text>
            </g>
          ))}
        </svg>

        <div className="graph-row" aria-hidden="true">
          <span className="graph-row-label">Entities</span>
        </div>
        <div className="graph-row">
          {view.entities.map((e) => (
            <div
              key={e.name}
              ref={setNode(`entity:${e.name}`)}
              className={cardClass("entity", e.name)}
              role="button"
              tabIndex={0}
              aria-label={`Entity ${e.name}`}
              aria-pressed={selected?.kind === "entity" && selected.name === e.name}
              onClick={() => onSelect({ kind: "entity", name: e.name })}
              onKeyDown={cardKeyDown(onSelect, { kind: "entity", name: e.name })}
            >
              <strong className="graph-card-title">{e.name}</strong>
              {e.properties.map((p) => (
                <span key={p.name} className="graph-prop">
                  {p.name}: {p.type ?? "?"}
                  {p.ref ? ` → ${p.ref}` : ""}
                  {p.required ? "*" : ""}
                </span>
              ))}
            </div>
          ))}
          {missing.map((name) => (
            <div
              key={name}
              ref={setNode(`missing:${name}`)}
              className="graph-card graph-card-missing"
              title={`${name} is referenced but not declared as an entity`}
            >
              <strong className="graph-card-title">{name}</strong>
              <span className="graph-prop">(not declared)</span>
            </div>
          ))}
        </div>

        {view.events.length > 0 ? (
          <>
            <div className="graph-row" aria-hidden="true">
              <span className="graph-row-label">Events</span>
            </div>
            <div className="graph-row">
              {view.events.map((e) => (
                <div
                  key={e.name}
                  ref={setNode(`event:${e.name}`)}
                  className={cardClass("event", e.name)}
                  role="button"
                  tabIndex={0}
                  aria-label={`Event ${e.name}`}
                  aria-pressed={selected?.kind === "event" && selected.name === e.name}
                  onClick={() => onSelect({ kind: "event", name: e.name })}
                  onKeyDown={cardKeyDown(onSelect, { kind: "event", name: e.name })}
                >
                  <strong className="graph-card-title">{e.name}</strong>
                  <span className="graph-prop">
                    subject: {e.subject ?? "(none)"}
                  </span>
                </div>
              ))}
            </div>
          </>
        ) : null}

        {view.projections.length > 0 ? (
          <>
            <div className="graph-row" aria-hidden="true">
              <span className="graph-row-label">Projections</span>
            </div>
            <div className="graph-row">
              {view.projections.map((p) => (
                <div
                  key={p.name}
                  ref={setNode(`projection:${p.name}`)}
                  className={cardClass("projection", p.name)}
                  role="button"
                  tabIndex={0}
                  aria-label={`Projection ${p.name}`}
                  aria-pressed={
                    selected?.kind === "projection" && selected.name === p.name
                  }
                  onClick={() => onSelect({ kind: "projection", name: p.name })}
                  onKeyDown={cardKeyDown(onSelect, {
                    kind: "projection",
                    name: p.name,
                  })}
                >
                  <strong className="graph-card-title">{p.name}</strong>
                  <span className="graph-prop">
                    subject: {p.subject ?? "(none)"}
                  </span>
                </div>
              ))}
            </div>
          </>
        ) : null}
      </div>
      <p className="hint">
        Solid arrows are <code>ref</code> properties, dashed arrows are event /
        projection subjects. Select a card to edit it.
      </p>
    </div>
  );
}
