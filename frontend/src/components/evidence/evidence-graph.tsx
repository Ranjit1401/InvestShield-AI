import { useMemo, useState } from "react";
import { GitFork } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/common/state-blocks";
import { humanizeEnum } from "@/lib/format";
import type {
  Claim,
  Entity,
  EvidenceRelation,
  EvidenceResponse,
} from "@/types/api";

/** English fallback: the backend has no section key for the graph itself. */
const FALLBACK_TITLE = "Claim, evidence and source graph";

type NodeKind = "claim" | "entity" | "evidence" | "source";

interface GraphNode {
  id: string;
  kind: NodeKind;
  label: string;
  detail: string;
  url?: string;
}

type EdgeKind = "claim-entity" | "claim-evidence" | "evidence-source";

interface GraphEdge {
  from: string;
  to: string;
  kind: EdgeKind;
  relation?: EvidenceRelation;
}

/** The stable key a node is identified by in `data-*` attributes. */
const KIND_LABEL: Record<NodeKind, string> = {
  claim: "CLAIM",
  entity: "ENTITY",
  evidence: "EVIDENCE",
  source: "SOURCE",
};

/** Border colour per node kind, as design-token references. */
const KIND_COLOR: Record<NodeKind, string> = {
  claim: "var(--color-accent)",
  entity: "var(--color-tone-info)",
  evidence: "var(--color-tone-warning)",
  source: "var(--color-ink-muted)",
};

/* Layout constants for the hand-rolled SVG. Three columns:
 * claims on the left, entities and evidence in the middle
 * (both hang directly off claims), sources on the right. */
const VIEWBOX_WIDTH = 920;
const NODE_WIDTH = 232;
const NODE_HEIGHT = 56;
const COLUMN_X: Record<"left" | "middle" | "right", number> = {
  left: 20,
  middle: 344,
  right: 668,
};
const VERTICAL_GAP = 18;
const PADDING_Y = 24;
const LABEL_MAX = 38;
const DETAIL_MAX = 44;

/** Collapse whitespace and hard-truncate; SVG text cannot wrap. */
function truncate(text: string, max: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/**
 * The claim → evidence → source graph.
 *
 * Every node and every edge is derived from the investigation
 * response itself: `Claim.entity_ids` gives claim → entity edges,
 * each `EvidenceResponse` group gives claim → evidence edges, and
 * each item's `source_id` gives evidence → source edges. Nothing
 * here is example data, and nothing is computed that the backend
 * did not already record.
 *
 * Rendering is a hand-rolled layered SVG rather than a graph
 * library: the relationship set is exactly three edge types over
 * four node types, which a fixed three-column layout expresses
 * without a dependency. Layout is deterministic — the same payload
 * always produces the same picture.
 *
 * Absence is reported as absence. When the investigation recorded
 * no relationships at all, the panel says so plainly; it never
 * presents that absence as a contradiction.
 */
export function EvidenceGraph({
  claims,
  entities,
  evidence,
  title = FALLBACK_TITLE,
}: {
  claims: Claim[];
  entities: Entity[];
  evidence: EvidenceResponse[];
  /** Presentation title; the backend defines no section key for the graph. */
  title?: string;
}) {
  const [activeId, setActiveId] = useState<string | null>(null);

  const { nodes, edges } = useMemo(() => {
    const nodeList: GraphNode[] = [];
    const edgeList: GraphEdge[] = [];

    const claimNodes: GraphNode[] = claims.map((claim) => ({
      id: claim.id,
      kind: "claim",
      label: truncate(claim.text, LABEL_MAX),
      detail: truncate(
        `${claim.id} · ${humanizeEnum(claim.claim_type)}`,
        DETAIL_MAX,
      ),
    }));

    const entityNodes: GraphNode[] = entities.map((entity) => ({
      id: entity.id,
      kind: "entity",
      label: truncate(entity.name, LABEL_MAX),
      detail: truncate(
        `${humanizeEnum(entity.entity_type)} · ${entity.id}`,
        DETAIL_MAX,
      ),
    }));

    const evidenceNodes: GraphNode[] = [];
    const sourcesById = new Map<string, GraphNode>();

    for (const group of evidence) {
      for (const source of group.sources ?? []) {
        if (!sourcesById.has(source.source_id)) {
          sourcesById.set(source.source_id, {
            id: source.source_id,
            kind: "source",
            label: truncate(source.title || source.domain, LABEL_MAX),
            detail: truncate(
              `${source.domain} · ${humanizeEnum(source.source_tier)}`,
              DETAIL_MAX,
            ),
            url: source.canonical_url || source.url,
          });
        }
      }

      for (const item of group.evidence) {
        evidenceNodes.push({
          id: item.id,
          kind: "evidence",
          label: truncate(item.excerpt, LABEL_MAX),
          detail: truncate(
            `${relationWord(item.relationship)} · ${item.id}`,
            DETAIL_MAX,
          ),
          url: item.source.canonical_url || item.source.url,
        });

        // A source the group did not list is still a real source the
        // backend returned; record it rather than drawing to nothing.
        if (!sourcesById.has(item.source_id)) {
          sourcesById.set(item.source_id, {
            id: item.source_id,
            kind: "source",
            label: truncate(item.source.title || item.source.domain, LABEL_MAX),
            detail: truncate(
              `${item.source.domain} · ${humanizeEnum(item.source.source_tier)}`,
              DETAIL_MAX,
            ),
            url: item.source.canonical_url || item.source.url,
          });
        }

        edgeList.push({
          from: group.claim_id,
          to: item.id,
          kind: "claim-evidence",
          relation: item.relationship,
        });
        edgeList.push({ from: item.id, to: item.source_id, kind: "evidence-source" });
      }
    }

    for (const claim of claims) {
      for (const entityId of claim.entity_ids) {
        if (entities.some((entity) => entity.id === entityId)) {
          edgeList.push({ from: claim.id, to: entityId, kind: "claim-entity" });
        }
      }
    }

    nodeList.push(...claimNodes, ...entityNodes, ...evidenceNodes, ...sourcesById.values());

    const byId = new Map(nodeList.map((node) => [node.id, node]));
    // Only edges whose both ends exist are drawable; a dangling
    // reference is dropped rather than drawn to an invented point.
    const drawable = edgeList.filter(
      (edge) => byId.has(edge.from) && byId.has(edge.to),
    );

    return { nodes: nodeList, edges: drawable };
  }, [claims, entities, evidence]);

  /** Which nodes touch which, for the focus-on-hover highlight. */
  const adjacency = useMemo(() => {
    const map = new Map<string, Set<string>>();
    const link = (a: string, b: string) => {
      map.set(a, new Set([...(map.get(a) ?? []), b]));
      map.set(b, new Set([...(map.get(b) ?? []), a]));
    };
    for (const edge of edges) link(edge.from, edge.to);
    return map;
  }, [edges]);

  const columnBuckets = useMemo((): [GraphNode[], GraphNode[], GraphNode[]] => {
    const claims = nodes.filter((node) => node.kind === "claim");
    const middle = nodes.filter(
      (node) => node.kind === "entity" || node.kind === "evidence",
    );
    const sources = nodes.filter((node) => node.kind === "source");
    return [claims, middle, sources];
  }, [nodes]);

  /** Node positions, computed once; columns are centred vertically. */
  const positions = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    const tallest = Math.max(
      1,
      ...columnBuckets.map((bucket) => bucket.length),
    );
    const height =
      tallest * NODE_HEIGHT +
      (tallest - 1) * VERTICAL_GAP +
      2 * PADDING_Y;

    columnBuckets.forEach((bucket, columnIndex) => {
      const columnKey =
        columnIndex === 0 ? "left" : columnIndex === 1 ? "middle" : "right";
      const x = COLUMN_X[columnKey];
      const bucketHeight =
        bucket.length * NODE_HEIGHT +
        Math.max(0, bucket.length - 1) * VERTICAL_GAP;
      const offsetY = PADDING_Y + (height - PADDING_Y * 2 - bucketHeight) / 2;
      bucket.forEach((node, index) => {
        map.set(node.id, { x, y: offsetY + index * (NODE_HEIGHT + VERTICAL_GAP) });
      });
    });

    return { map, height };
  }, [columnBuckets]);

  const isDimmedNode = (node: GraphNode) =>
    activeId !== null &&
    node.id !== activeId &&
    !adjacency.get(activeId)?.has(node.id);

  const edgeState = (edge: GraphEdge) => {
    if (activeId === null) return { dimmed: false, highlighted: false };
    const touches = edge.from === activeId || edge.to === activeId;
    return { dimmed: !touches, highlighted: touches };
  };

  if (edges.length === 0) {
    return (
      <section aria-labelledby="evidence-graph-heading" className="space-y-3">
        <h2 id="evidence-graph-heading" className="text-base font-semibold text-ink">
          {title}
        </h2>
        <EmptyState
          icon={<GitFork aria-hidden="true" className="size-7" />}
          title="No evidence relationships were recorded"
          body="This investigation recorded no claim, entity, evidence or source relationships to draw. An empty graph is a statement about what was recorded, not a contradiction of any claim."
        />
      </section>
    );
  }

  const edgeColor = (edge: GraphEdge): string => {
    if (edge.kind === "claim-evidence") {
      switch (edge.relation) {
        case "SUPPORTS":
          return "var(--color-tone-success)";
        case "CONTRADICTS":
          return "var(--color-tone-danger)";
        default:
          return "var(--color-tone-info)";
      }
    }
    return "var(--color-ink-faint)";
  };

  const markerFor = (edge: GraphEdge): string => {
    if (edge.kind === "claim-evidence") {
      switch (edge.relation) {
        case "SUPPORTS":
          return "arrow-success";
        case "CONTRADICTS":
          return "arrow-danger";
        default:
          return "arrow-info";
      }
    }
    return "arrow-neutral";
  };

  const counts = {
    claims: columnBuckets[0].length,
    middle: columnBuckets[1].length,
    sources: columnBuckets[2].length,
  };

  return (
    <section aria-labelledby="evidence-graph-heading" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id="evidence-graph-heading" className="text-base font-semibold text-ink">
          {title}
        </h2>
        <p className="text-xs text-ink-faint">
          {counts.claims} claims · {counts.middle} entities &amp; evidence ·{" "}
          {counts.sources} sources · {edges.length} relationships
        </p>
      </div>

      <Card>
        <CardContent className="p-4 sm:p-5">
          <svg
            role="img"
            viewBox={`0 0 ${VIEWBOX_WIDTH} ${positions.height}`}
            className="h-auto w-full"
            aria-label={`Relationship graph of the investigation: ${counts.claims} claims, ${counts.middle} entities and evidence items, ${counts.sources} sources, connected by ${edges.length} relationships. Claims connect to the entities and evidence they reference, and evidence connects to its source.`}
          >
            <defs>
              <marker
                id="arrow-neutral"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="6.5"
                markerHeight="6.5"
                orient="auto-start-reverse"
              >
                <path d="M 0 1 L 9 5 L 0 9 z" fill="var(--color-ink-faint)" />
              </marker>
              <marker
                id="arrow-info"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="6.5"
                markerHeight="6.5"
                orient="auto-start-reverse"
              >
                <path d="M 0 1 L 9 5 L 0 9 z" fill="var(--color-tone-info)" />
              </marker>
              <marker
                id="arrow-success"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="6.5"
                markerHeight="6.5"
                orient="auto-start-reverse"
              >
                <path d="M 0 1 L 9 5 L 0 9 z" fill="var(--color-tone-success)" />
              </marker>
              <marker
                id="arrow-danger"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="6.5"
                markerHeight="6.5"
                orient="auto-start-reverse"
              >
                <path d="M 0 1 L 9 5 L 0 9 z" fill="var(--color-tone-danger)" />
              </marker>
            </defs>

            {edges.map((edge) => {
              const from = positions.map.get(edge.from);
              const to = positions.map.get(edge.to);
              if (!from || !to) return null;
              const state = edgeState(edge);
              const x0 = from.x + NODE_WIDTH;
              const y0 = from.y + NODE_HEIGHT / 2;
              const x1 = to.x;
              const y1 = to.y + NODE_HEIGHT / 2;
              const midX = (x0 + x1) / 2;

              return (
                <path
                  key={`${edge.from}->${edge.to}`}
                  data-edge-kind={edge.kind}
                  data-edge-relation={edge.relation ?? ""}
                  d={`M ${x0} ${y0} C ${midX} ${y0}, ${midX} ${y1}, ${x1} ${y1}`}
                  fill="none"
                  style={{
                    stroke: edgeColor(edge),
                    strokeWidth: state.highlighted ? 2.25 : 1.25,
                    opacity: state.dimmed ? 0.12 : state.highlighted ? 0.95 : 0.6,
                  }}
                  markerEnd={`url(#${markerFor(edge)})`}
                />
              );
            })}

            {nodes.map((node) => {
              const position = positions.map.get(node.id);
              if (!position) return null;
              const dimmed = isDimmedNode(node);
              const active = activeId === node.id;

              return (
                <g
                  key={node.id}
                  data-node-id={node.id}
                  data-node-kind={node.kind}
                  opacity={dimmed ? 0.25 : 1}
                  onMouseEnter={() => setActiveId(node.id)}
                  onMouseLeave={() => setActiveId(null)}
                  onClick={() =>
                    setActiveId((current) => (current === node.id ? null : node.id))
                  }
                  className="cursor-pointer"
                  style={{ transition: "opacity 120ms ease-in-out" }}
                >
                  <title>
                    {`${KIND_LABEL[node.kind]}: ${node.label}`}
                  </title>
                  <rect
                    x={position.x}
                    y={position.y}
                    width={NODE_WIDTH}
                    height={NODE_HEIGHT}
                    rx={8}
                    style={{
                      fill: "var(--color-surface-raised)",
                      stroke: KIND_COLOR[node.kind],
                      strokeWidth: active ? 2.25 : 1.5,
                    }}
                  />
                  <text
                    x={position.x + 12}
                    y={position.y + 21}
                    style={{
                      fill: "var(--color-ink)",
                      fontSize: 12,
                      fontWeight: 600,
                    }}
                  >
                    {node.label}
                  </text>
                  <text
                    x={position.x + 12}
                    y={position.y + 39}
                    style={{
                      fill: "var(--color-ink-faint)",
                      fontSize: 10,
                    }}
                  >
                    {node.detail}
                  </text>
                  <text
                    x={position.x + NODE_WIDTH - 10}
                    y={position.y + 15}
                    textAnchor="end"
                    style={{
                      fill: KIND_COLOR[node.kind],
                      fontSize: 9,
                      fontWeight: 700,
                      letterSpacing: "0.08em",
                    }}
                  >
                    {KIND_LABEL[node.kind]}
                  </text>
                </g>
              );
            })}
          </svg>

          <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-hairline pt-3">
            <p className="text-xs text-ink-faint">
              Hover or tap a node to trace what it connects to.
            </p>
            <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-ink-faint">
              {(Object.keys(KIND_LABEL) as NodeKind[]).map((kind) => (
                <li key={kind} className="flex items-center gap-1.5">
                  <span
                    aria-hidden="true"
                    className="inline-block size-2.5 rounded-sm border"
                    style={{
                      borderColor: KIND_COLOR[kind],
                      backgroundColor: "var(--color-surface-raised)",
                    }}
                  />
                  {KIND_LABEL[kind]}
                </li>
              ))}
            </ul>
            <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-ink-faint">
              <li className="flex items-center gap-1.5">
                <span
                  aria-hidden="true"
                  className="inline-block h-0.5 w-5"
                  style={{ backgroundColor: "var(--color-tone-success)" }}
                />
                supports
              </li>
              <li className="flex items-center gap-1.5">
                <span
                  aria-hidden="true"
                  className="inline-block h-0.5 w-5"
                  style={{ backgroundColor: "var(--color-tone-danger)" }}
                />
                contradicts
              </li>
              <li className="flex items-center gap-1.5">
                <span
                  aria-hidden="true"
                  className="inline-block h-0.5 w-5"
                  style={{ backgroundColor: "var(--color-ink-faint)" }}
                />
                other recorded relationship
              </li>
            </ul>
          </div>
        </CardContent>
      </Card>

      {columnBuckets[1].filter((node) => node.kind === "evidence").length ===
        0 ? (
        <p className="text-sm leading-relaxed text-ink-muted">
          No evidence relationships were recorded for this investigation.
        </p>
      ) : null}
    </section>
  );
}

/** The display word for an evidence relationship, used in node details. */
function relationWord(relation: EvidenceRelation): string {
  switch (relation) {
    case "SUPPORTS":
      return "Supports";
    case "CONTRADICTS":
      return "Contradicts";
    case "IDENTITY_REFERENCE":
      return "Identifies";
    case "CONTEXT":
      return "Context";
    case "MENTIONS":
      return "Mentions";
    default:
      return humanizeEnum(relation);
  }
}
