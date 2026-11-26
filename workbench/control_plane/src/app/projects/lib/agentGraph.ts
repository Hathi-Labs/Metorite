/**
 * The Agents view · the Graph lens (WS-MCP AMR-1, `agent_coordination.md` §5.12).
 *
 * A fixed layered layout with four columns: person, orchestrator, session, and
 * what the session waits on. One pure function places the nodes, so a vitest
 * proves it (§3.12.4 rule 6). No layout library.
 *
 * The lines:
 *  - `tree`  neutral: a person holds a session that nothing dispatched.
 *  - `dispatch` neutral: one session dispatched another.
 *  - `you`   warning: the session waits on a person (the state "Needs you").
 *  - `wait`  primary: the session waits on something that a structured field
 *    of the run names. With no field there is no line. No line is invented.
 */
import type { AgentWaitingOn } from "./agentRuns";
import {
  type LensFilters,
  type LensModel,
  type LensSession,
  WAIT_NODE_WORD,
  matchesFind,
  membersOf,
  personSessions,
  splitSessions,
} from "./agentLenses";

export type BlockKey = "you" | AgentWaitingOn;

export type GraphNode = { id: string; x: number; y: number; w: number; h: number } & (
  | { kind: "person"; person: string; sessions: LensSession[] }
  | { kind: "orch"; session: LensSession; members: LensSession[]; folded: boolean; groupKey: string }
  | { kind: "session"; session: LensSession }
  | { kind: "fold"; groupKey: string; folded: LensSession[] }
  | { kind: "block"; block: BlockKey; sessions: LensSession[] }
);

export type GraphEdgeKind = "tree" | "dispatch" | "you" | "wait";

export interface GraphEdge {
  id: string;
  from: string;
  to: string;
  kind: GraphEdgeKind;
  /** Both ends sit in the session column, so the line bends out to the right. */
  side: boolean;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface GraphOptions {
  filters: LensFilters;
  /** The ids of the orchestrators whose sessions are folded away. */
  collapsed: ReadonlySet<string>;
  /** The keys of the groups whose fold line is open. */
  opened: ReadonlySet<string>;
  /** Draw the fourth column. */
  waits: boolean;
  /** The root font size over 16, so density scales the graph. */
  unit: number;
  nameOf: (email: string) => string;
}

export const GRAPH_X = { person: 0, orch: 210, session: 490, block: 900 } as const;
export const GRAPH_W = { person: 176, orch: 244, session: 300, block: 220 } as const;
export const GRAPH_H = { person: 48, orch: 76, session: 60, fold: 40, block: 56 } as const;
const GAP = 10;
const GROUP_GAP = 24;
const PERSON_GAP = 28;

export const sessionNodeId = (id: string): string => `s:${id}`;
export const personNodeId = (person: string): string => `p:${person}`;
export const blockNodeId = (block: BlockKey): string => `b:${block}`;

export function blockWord(block: BlockKey): string {
  return block === "you" ? "You" : WAIT_NODE_WORD[block];
}

export function blockSentence(block: BlockKey, n: number): string {
  if (block === "you") return n === 1 ? "1 session waits on you" : `${n} sessions wait on you`;
  return n === 1 ? "1 session waits" : `${n} sessions wait`;
}

/** The "waits on" target of a session, or `null` when no fact names one. */
export function blockOf(s: LensSession): BlockKey | null {
  if (s.state === "you") return "you";
  return s.state === "waiting" && s.wait ? s.wait.on : null;
}

export function layoutGraph(model: LensModel, options: GraphOptions): Graph {
  const { filters, collapsed, opened, waits, unit: u, nameOf } = options;
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  /** Session id to the id of the node that draws it. A folded session has none. */
  const drawn = new Map<string, string>();
  /** Session node id to the id of the node that holds it when nothing else does. */
  const parent = new Map<string, string>();
  const box = (column: keyof typeof GRAPH_X, kind: keyof typeof GRAPH_H, y: number) => ({
    x: GRAPH_X[column] * u,
    y,
    w: GRAPH_W[column] * u,
    h: GRAPH_H[kind] * u,
  });
  const find = filters.find.trim();
  let y = 0;

  for (const p of model.people) {
    if (filters.person && p.person !== filters.person) continue;
    const pid = personNodeId(p.person);
    const centers: number[] = [];
    for (const group of p.groups) {
      const orch = group.orch;
      const orchHit = orch !== null && (!find || matchesFind(orch, find, nameOf(orch.person)));
      const folded = orch !== null && collapsed.has(orch.id) && !find;
      const split = folded ? { shown: [], folded: [] } : splitSessions(group.sessions, filters, opened.has(group.key), nameOf);
      if (find && !orchHit && split.shown.length === 0) continue;
      const kids: number[] = [];
      const holder = orch ? sessionNodeId(orch.id) : pid;
      for (const s of split.shown) {
        const id = sessionNodeId(s.id);
        nodes.push({ id, kind: "session", session: s, ...box("session", "session", y) });
        drawn.set(s.id, id);
        parent.set(id, holder);
        kids.push(y + (GRAPH_H.session * u) / 2);
        y += (GRAPH_H.session + GAP) * u;
      }
      if (split.folded.length > 0) {
        const id = `f:${group.key}`;
        nodes.push({ id, kind: "fold", groupKey: group.key, folded: split.folded, ...box("session", "fold", y) });
        edges.push({ id: `${holder}>${id}`, from: holder, to: id, kind: "tree", side: false });
        kids.push(y + (GRAPH_H.fold * u) / 2);
        y += (GRAPH_H.fold + GAP) * u;
      }
      if (orch) {
        const h = GRAPH_H.orch * u;
        let top: number;
        if (kids.length > 0) {
          top = Math.max((kids[0] + kids[kids.length - 1]) / 2 - h / 2, 0);
          // A short group must not let the tall orchestrator overlap the group below.
          y = Math.max(y, top + h + GAP * u);
        } else {
          top = y;
          y += h + GAP * u;
        }
        const id = sessionNodeId(orch.id);
        nodes.push({
          id,
          kind: "orch",
          session: orch,
          members: membersOf(group),
          folded,
          groupKey: group.key,
          ...box("orch", "orch", top),
        });
        drawn.set(orch.id, id);
        edges.push({ id: `${pid}>${id}`, from: pid, to: id, kind: "tree", side: false });
        centers.push(top + h / 2);
      } else if (kids.length > 0) {
        centers.push((kids[0] + kids[kids.length - 1]) / 2);
      }
      y += (GROUP_GAP - GAP) * u;
    }
    if (centers.length === 0) continue;
    const h = GRAPH_H.person * u;
    const middle = (centers[0] + centers[centers.length - 1]) / 2;
    const previous = nodes.filter((n) => n.kind === "person").at(-1);
    const top = Math.max(middle - h / 2, previous ? previous.y + previous.h + GAP * u : 0);
    nodes.push({ id: pid, kind: "person", person: p.person, sessions: personSessions(p), ...box("person", "person", top) });
    y += (PERSON_GAP - GROUP_GAP + GAP) * u;
  }

  // The lines into each session: from its dispatchers, else from its holder.
  for (const [sessionId, id] of drawn) {
    const s = model.byId.get(sessionId);
    if (!s || !parent.has(id)) continue;
    const from = s.dispatchers.map((d) => drawn.get(d)).filter((d): d is string => d !== undefined);
    if (from.length === 0) {
      const holder = parent.get(id)!;
      edges.push({ id: `${holder}>${id}`, from: holder, to: id, kind: "tree", side: false });
      continue;
    }
    for (const source of from) {
      // A source that is a session (not an orchestrator) sits in the same column.
      edges.push({ id: `${source}~${id}`, from: source, to: id, kind: "dispatch", side: parent.has(source) });
    }
  }

  if (waits) {
    const targets = new Map<BlockKey, Array<{ session: LensSession; node: GraphNode }>>();
    for (const node of nodes) {
      if (node.kind !== "session" && node.kind !== "orch") continue;
      const key = blockOf(node.session);
      if (!key) continue;
      targets.set(key, [...(targets.get(key) ?? []), { session: node.session, node }]);
    }
    const h = GRAPH_H.block * u;
    let floor = 0;
    const placed = [...targets.entries()]
      .map(([block, list]) => ({
        block,
        list,
        at: list.reduce((sum, t) => sum + t.node.y + t.node.h / 2, 0) / list.length - h / 2,
      }))
      .sort((a, b) => a.at - b.at || a.block.localeCompare(b.block));
    for (const { block, list, at } of placed) {
      const top = Math.max(at, floor);
      floor = top + h + GROUP_GAP * u;
      const id = blockNodeId(block);
      nodes.push({ id, kind: "block", block, sessions: list.map((t) => t.session), ...box("block", "block", top) });
      for (const t of list) {
        edges.push({ id: `${t.node.id}>${id}`, from: t.node.id, to: id, kind: block === "you" ? "you" : "wait", side: false });
      }
    }
  }
  return { nodes, edges };
}

/**
 * The chain that a hover lights: the node, everything above it, everything it
 * dispatched, and what those wait on. A "waits on" node lights its sessions
 * and the chain above each.
 */
export function relatedNodes(graph: Graph, id: string): Set<string> {
  const lit = new Set<string>([id]);
  const down = (edge: GraphEdge) => edge.kind === "tree" || edge.kind === "dispatch";
  const walk = (at: string, direction: "up" | "down") => {
    for (const edge of graph.edges) {
      if (!down(edge)) continue;
      const [near, far] = direction === "up" ? [edge.to, edge.from] : [edge.from, edge.to];
      if (near === at && !lit.has(far)) {
        lit.add(far);
        walk(far, direction);
      }
    }
  };
  const node = graph.nodes.find((n) => n.id === id);
  walk(id, "up");
  if (node?.kind === "block") {
    for (const edge of graph.edges) {
      if (edge.to !== id) continue;
      lit.add(edge.from);
      walk(edge.from, "up");
    }
    return lit;
  }
  const above = new Set(lit);
  walk(id, "down");
  for (const edge of graph.edges) {
    // Only the lines of this node and of what it dispatched, never of its parents.
    if (!down(edge) && lit.has(edge.from) && (edge.from === id || !above.has(edge.from))) lit.add(edge.to);
  }
  return lit;
}
