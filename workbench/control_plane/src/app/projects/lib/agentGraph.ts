/**
 * The Agents view · the map (WS-MCP AMR-5, `agent_coordination.md` §3.12.8).
 *
 * A fixed layered layout, left to right: the scope, the person, the
 * orchestrator and the agent. The sub-agents of one open agent add a column.
 * One pure function places the nodes, so a vitest proves it (§3.12.4 rule 6).
 * No layout library.
 *
 * AMR-4 (§3.12.9): an agent draws its sub-agents as a count. The one agent that
 * is open adds a column of at most `SUB_ROWS_MAX` rows. `planMap` bounds the
 * agent cards.
 *
 * AMR-5b (§3.12.8, G8): an orchestrator whose run is outside the project draws
 * as a context card in the orchestrator column, only while a lane that it
 * dispatched draws. It has no chevron, and it counts in no total.
 *
 * The lines (decision G1: no wait line, because a wait is a fact of the card):
 *  - `tree`  neutral: a node holds the node.
 *  - `dispatch` neutral, with an arrow: one agent dispatched another.
 */
import {
  type LensContext,
  type LensModel,
  type LensSession,
  type LensState,
  type SubRow,
  matchesFind,
  membersOf,
} from "./agentLenses";
import {
  type PlanOptions,
  SUB_ROWS_MAX,
  contextNodeId,
  isActive,
  orderGroups,
  personNodeId,
  planMap,
  sessionNodeId,
} from "./agentScale";

export type GraphNode = { id: string; x: number; y: number; w: number; h: number } & (
  /** The scope: everyone, or one person. */
  | { kind: "root"; person: string; sessions: LensSession[]; folded: boolean; canFold: boolean }
  /** `canFold`: false for an open person under a find, which ignores folds. */
  | { kind: "person"; person: string; sessions: LensSession[]; folded: boolean; canFold: boolean }
  /** `hidden`: the agents under it that draw no card. */
  | { kind: "orch"; session: LensSession; members: LensSession[]; hidden: number; folded: boolean; canFold: boolean }
  | { kind: "session"; session: LensSession }
  /**
   * AMR-5b. An orchestrator outside the project. `person` is the person it
   * draws under, or `null` when it draws after the people. It never folds.
   */
  | { kind: "context"; context: LensContext; person: string | null }
  /** The idle and done agents of a person, under the Active switch. */
  | { kind: "rest"; person: string; count: number; open: boolean }
  /** `next`: where a click starts the next cards of the state (`StateNode.next`). */
  | { kind: "bucket"; person: string; state: LensState; sessions: LensSession[]; next: number }
  | { kind: "sub"; sessionId: string; row: SubRow }
  | { kind: "submore"; sessionId: string; count: number }
  /** The open session's rows are loading, or did not load. */
  | { kind: "subnote"; sessionId: string; failed: boolean }
);

export type GraphEdgeKind = "tree" | "dispatch";

export interface GraphEdge {
  id: string;
  from: string;
  to: string;
  kind: GraphEdgeKind;
  /** The target is not right of the source, so the line bends out to the right. */
  side: boolean;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface GraphOptions extends PlanOptions {
  /** The root font size over 16, so density scales the graph. */
  unit: number;
  /** The one session whose sub-agents show. */
  subs?: OpenSubs | null;
}

/** The one open session. `rows` is `null` while they load, or after a failed read. */
export interface OpenSubs {
  sessionId: string;
  rows: readonly SubRow[] | null;
  failed?: boolean;
}

/** The four columns, left to right. They never move, so a refresh keeps each node in place. */
export const GRAPH_X = { root: 0, person: 166, orch: 388, session: 626 } as const;
export const GRAPH_W = { root: 142, person: 206, orch: 214, session: 264, sub: 230 } as const;
export const GRAPH_H = { root: 96, person: 72, card: 130, orch: 96, context: 72, session: 82, row: 40, sub: 30 } as const;
export const ROOT_ID = "root";
/** The space between the agents and the next column. */
export const COLUMN_GAP = 32;
/** How far below the top of what it holds a node may sit, so the first view holds it. */
const ROOT_LOWEST = 140;
const GAP = 10;
const PERSON_GAP = 24;

export { contextNodeId, personNodeId, sessionNodeId };
export const restNodeId = (person: string): string => `r:${person}`;
export const bucketNodeId = (person: string, state: LensState): string => `k:${person}:${state}`;
/**
 * The sub-agent rows of the open session, from the top of its card: at most
 * `SUB_ROWS_MAX`, then one row that opens the full list. Returns the bottom of
 * the column, or `top` when the session is not the open one.
 */
function placeSubs(
  graph: Graph,
  subs: OpenSubs | null | undefined,
  session: LensSession,
  at: { x: number; top: number; u: number },
): number {
  if (!subs || subs.sessionId !== session.id || session.subs.total === 0) return at.top;
  const from = sessionNodeId(session.id);
  const h = GRAPH_H.sub * at.u;
  let y = at.top;
  const put = (id: string, node: DistributiveOmit<GraphNode, "id" | "x" | "y" | "w" | "h">) => {
    graph.nodes.push({ ...node, id, x: at.x, y, w: GRAPH_W.sub * at.u, h } as GraphNode);
    graph.edges.push({ id: `${from}>${id}`, from, to: id, kind: "tree", side: false });
    y += h + 6 * at.u;
  };
  if (!subs.rows) {
    put(`u:${session.id}:note`, { kind: "subnote", sessionId: session.id, failed: subs.failed === true });
    return y;
  }
  const rows = subs.rows.slice(0, SUB_ROWS_MAX);
  for (const row of rows) put(`u:${session.id}:${row.id}`, { kind: "sub", sessionId: session.id, row });
  const more = Math.max(session.subs.total, subs.rows.length) - rows.length;
  if (more > 0) put(`u:${session.id}:more`, { kind: "submore", sessionId: session.id, count: more });
  return y;
}

type DistributiveOmit<T, K extends keyof never> = T extends unknown ? Omit<T, K> : never;

/**
 * The one map of the Agents view (AMR-5): the scope, each person, their
 * orchestrators, their agents and the sub-agents of the one open agent.
 * `planMap` bounds the cards. A fold, a state node and the "idle or done" node
 * stand for what they hide, so a dispatch line always has an end.
 */
export function layoutGraph(model: LensModel, options: GraphOptions): Graph {
  const { unit: u, subs, person: scope, folds, find, nameOf } = options;
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  const graph: Graph = { nodes, edges };
  const seen = new Set<string>();
  const link = (from: string, to: string, kind: GraphEdgeKind) => {
    const id = `${from}>${to}`;
    if (from === to || seen.has(id)) return;
    seen.add(id);
    edges.push({ id, from, to, kind, side: false });
  };
  const inScope = scope ? model.sessions.filter((s) => s.person === scope) : model.sessions;
  if (inScope.length === 0) return graph;
  const rootFolded = !find.trim() && folds.get(ROOT_ID) === false;
  const plans = planMap(model, options);
  const orchX = GRAPH_X.orch;
  const sessionX = GRAPH_X.session;
  const subX = (sessionX + GRAPH_W.session + COLUMN_GAP) * u;
  /** Session id to the node that draws it, or that stands for it. */
  const rep = new Map<string, string>();
  /** The sessions that draw their own card. */
  const own = new Set<string>();
  const centers: number[] = [];
  let y = 0;
  // AMR-5b: a context card draws only while a lane that it dispatched draws a card.
  const drawn = new Set(rootFolded ? [] : plans.filter((p) => p.open).flatMap((p) => [...p.shown]));
  const contexts = model.contexts.filter((c) => c.lanes.some((id) => drawn.has(id)));
  /** A context draws under its person when that person is open on the map. Else after the people. */
  const homeOf = new Map(
    contexts.map((c) => [c.id, plans.some((p) => p.open && p.person === c.person) ? c.person : null]),
  );
  const placed = new Set<string>();

  for (const plan of rootFolded ? [] : plans) {
    const person = model.people.find((p) => p.person === plan.person)!;
    const pid = personNodeId(plan.person);
    const h = (plan.open ? GRAPH_H.person : GRAPH_H.card) * u;
    const blockTop = y;
    const kids: number[] = [];
    const put = (node: DistributiveOmit<GraphNode, "x" | "y" | "w" | "h">, x: number, w: number, tall: number, top: number) => {
      nodes.push({ ...node, x, y: top, w: w * u, h: tall * u } as GraphNode);
    };
    /** One agent card in the agent column, with its sub-agent rows. */
    const card = (s: LensSession, holder: string) => {
      const id = sessionNodeId(s.id);
      put({ id, kind: "session", session: s }, sessionX * u, GRAPH_W.session, GRAPH_H.session, y);
      own.add(s.id);
      rep.set(s.id, id);
      const mine = s.dispatchers.some((d) => model.byId.get(d)?.person === s.person && plan.shown.has(d));
      // A card that no agent of its own person dispatched hangs from its holder.
      if (!mine) link(holder, id, "tree");
      const centre = y + (GRAPH_H.session * u) / 2;
      const bottom = placeSubs(graph, subs, s, { x: subX, top: y, u });
      y = Math.max(y + (GRAPH_H.session + GAP) * u, bottom + (GAP - 6) * u);
      return centre;
    };

    /**
     * AMR-5b. A context card under this person, and the lanes of the person that
     * it dispatched with no orchestrator of their own, which hang from it.
     */
    const contextCard = (c: LensContext, lanes: readonly LensSession[]) => {
      const id = contextNodeId(c.id);
      const from = y;
      const inGroup = lanes.map((s) => card(s, id));
      const tall = GRAPH_H.context * u;
      const top = inGroup.length > 0
        ? Math.max(Math.min((inGroup[0] + inGroup[inGroup.length - 1]) / 2 - tall / 2, from + ROOT_LOWEST * u), from)
        : from;
      put({ id, kind: "context", context: c, person: plan.person }, orchX * u, GRAPH_W.orch, GRAPH_H.context, top);
      placed.add(c.id);
      link(pid, id, "tree");
      kids.push(top + tall / 2);
      y = Math.max(y, top + tall + GAP * u);
    };
    /** The lanes of a group with no orchestrator: each context of this person takes its own first. */
    const ownLanes = (lanes: readonly LensSession[]) => {
      let left = [...lanes];
      for (const c of contexts) {
        if (homeOf.get(c.id) !== plan.person || placed.has(c.id)) continue;
        const held = left.filter((s) => s.contexts.includes(c.id));
        left = left.filter((s) => !held.includes(s));
        contextCard(c, held);
      }
      for (const s of left) kids.push(card(s, pid));
    };

    if (plan.open) {
      for (const group of orderGroups(person.groups, options.starts)) {
        const orch = group.orch;
        const lanes = group.sessions.filter((s) => plan.shown.has(s.id));
        if (!orch) {
          ownLanes(lanes);
          continue;
        }
        if (!plan.shown.has(orch.id)) {
          for (const s of lanes) kids.push(card(s, pid));
          continue;
        }
        const id = sessionNodeId(orch.id);
        const folded = !find.trim() && folds.get(id) === false;
        const inGroup: number[] = [];
        const from = y;
        for (const s of lanes) inGroup.push(card(s, id));
        const tall = GRAPH_H.orch * u;
        let top = from;
        if (inGroup.length > 0) {
          // A tall group keeps its card near its top, where the view is.
          const centred = (inGroup[0] + inGroup[inGroup.length - 1]) / 2 - tall / 2;
          top = Math.max(Math.min(centred, from + ROOT_LOWEST * u), from);
        }
        const members = membersOf(group).filter((s) => matchesFind(s, find, nameOf(s.person)));
        const hidden = members.filter((s) => s.id !== orch.id && !plan.shown.has(s.id)).length;
        put(
          { id, kind: "orch", session: orch, members, hidden, folded, canFold: !find.trim() && group.sessions.length > 0 },
          orchX * u,
          GRAPH_W.orch,
          GRAPH_H.orch,
          top,
        );
        own.add(orch.id);
        rep.set(orch.id, id);
        link(pid, id, "tree");
        kids.push(top + tall / 2);
        // A short group must not let the tall orchestrator overlap what follows.
        y = Math.max(y, top + tall + GAP * u);
        const bottom = placeSubs(graph, subs, orch, { x: subX, top: Math.max(top, from), u });
        if (bottom > top) y = Math.max(y, bottom + (GAP - 6) * u);
      }
      // A context of this person whose lanes are all of other people, or under an orchestrator.
      ownLanes([]);
      const row = (node: DistributiveOmit<GraphNode, "x" | "y" | "w" | "h">) => {
        put(node, sessionX * u, GRAPH_W.session, GRAPH_H.row, y);
        link(pid, node.id, "tree");
        kids.push(y + (GRAPH_H.row * u) / 2);
        y += (GRAPH_H.row + GAP) * u;
      };
      for (const bucket of plan.buckets) {
        const id = bucketNodeId(plan.person, bucket.state);
        row({ id, kind: "bucket", person: plan.person, state: bucket.state, sessions: bucket.sessions, next: bucket.next });
        for (const s of bucket.sessions) if (!rep.has(s.id)) rep.set(s.id, id);
      }
      if (plan.rest) {
        const id = restNodeId(plan.person);
        row({ id, kind: "rest", person: plan.person, count: plan.rest.count, open: plan.rest.open });
        for (const s of plan.sessions) if (!rep.has(s.id) && !isActive(s)) rep.set(s.id, id);
      }
      // An agent under a folded orchestrator stands where its orchestrator does:
      // on its card, in its state node or in the "idle or done" node. An agent
      // with nothing else stands on its person, so no line is lost.
      for (const group of person.groups) {
        const stand = group.orch ? rep.get(group.orch.id) : undefined;
        const folded = group.orch !== null && folds.get(sessionNodeId(group.orch.id)) === false;
        for (const s of group.sessions) {
          if (folded && stand && !own.has(s.id)) rep.set(s.id, stand);
        }
      }
      for (const s of plan.sessions) if (!rep.has(s.id)) rep.set(s.id, pid);
    } else {
      for (const s of plan.sessions) rep.set(s.id, pid);
    }
    const middle = kids.length > 0 ? (Math.min(...kids) + Math.max(...kids)) / 2 : blockTop + h / 2;
    const top = Math.max(Math.min(middle - h / 2, blockTop + ROOT_LOWEST * u), blockTop);
    put(
      {
        id: pid,
        kind: "person",
        person: plan.person,
        sessions: plan.sessions,
        folded: !plan.open,
        // A find ignores a fold, but the bound can fold a person under it.
        canFold: !find.trim() || !plan.open,
      },
      GRAPH_X.person * u,
      GRAPH_W.person,
      plan.open ? GRAPH_H.person : GRAPH_H.card,
      top,
    );
    link(ROOT_ID, pid, "tree");
    centers.push(top + h / 2);
    y = Math.max(y, top + h + GAP * u) + (PERSON_GAP - GAP) * u;
  }

  // AMR-5b: a context whose person is not open on the map draws after the people.
  for (const c of contexts) {
    if (placed.has(c.id)) continue;
    const id = contextNodeId(c.id);
    const h = GRAPH_H.context * u;
    nodes.push({ id, kind: "context", context: c, person: null, x: orchX * u, y, w: GRAPH_W.orch * u, h });
    placed.add(c.id);
    link(ROOT_ID, id, "tree");
    centers.push(y + h / 2);
    y += h + GAP * u;
  }

  const tall = GRAPH_H.root * u;
  const middle = centers.length > 0 ? (centers[0] + centers[centers.length - 1]) / 2 : tall / 2;
  nodes.push({
    id: ROOT_ID,
    kind: "root",
    person: scope,
    sessions: inScope,
    folded: rootFolded,
    canFold: !find.trim(),
    x: GRAPH_X.root,
    y: Math.max(Math.min(middle - tall / 2, ROOT_LOWEST * u), 0),
    w: GRAPH_W.root * u,
    h: tall,
  });

  // The dispatch lines, between whatever stands for each end.
  for (const s of inScope) {
    const to = rep.get(s.id);
    if (!to) continue;
    for (const dispatcher of s.dispatchers) {
      const from = rep.get(dispatcher);
      if (!from) continue;
      // Two fold nodes of one person say nothing: the person holds both.
      if (!own.has(s.id) && !own.has(dispatcher) && model.byId.get(dispatcher)?.person === s.person) continue;
      link(from, to, "dispatch");
    }
  }
  // AMR-5b: a context card to each lane it dispatched, or what stands for it.
  for (const c of contexts) {
    for (const lane of c.lanes) {
      const to = rep.get(lane);
      if (to) link(contextNodeId(c.id), to, "dispatch");
    }
  }

  for (const edge of edges) {
    const from = nodes.find((n) => n.id === edge.from)!;
    const to = nodes.find((n) => n.id === edge.to)!;
    edge.side = to.x <= from.x;
  }
  return graph;
}

/**
 * The chain that a hover lights: the node, everything above it and everything
 * under it or dispatched by it.
 */
export function relatedNodes(graph: Graph, id: string): Set<string> {
  const lit = new Set<string>([id]);
  const walk = (at: string, direction: "up" | "down") => {
    for (const edge of graph.edges) {
      const [near, far] = direction === "up" ? [edge.to, edge.from] : [edge.from, edge.to];
      if (near === at && !lit.has(far)) {
        lit.add(far);
        walk(far, direction);
      }
    }
  };
  walk(id, "up");
  walk(id, "down");
  return lit;
}
