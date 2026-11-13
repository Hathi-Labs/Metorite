/**
 * The pure half of the Process flow block (`ai_studio.md` §5.1, slice STU-D8).
 *
 * The block draws a workflow graph and the state of each step of one run. This file reads the
 * node of the page, turns the graph into the nodes and edges of `@xyflow/react`, and turns the
 * run events into a state for each step. No React import, so a test can drive every rule.
 *
 * The block is read-only and shows no secret. A step node carries its label and its type, and
 * nothing else: the config of a step can hold a secret, and the workflow answer also holds a
 * hook token and a hook URL. `flowGraph` copies the label and the type and drops the rest, so
 * a token cannot reach the screen. Fence: `flowModel.test.ts`, `StudioFlow.test.ts`.
 */
import type { FlowState } from "@/lib/statusAccent";
import { relativeTime } from "@/lib/taskCard";

const isObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

/** What the node of the page names: one workflow, and optionally one run of it. */
export interface FlowSource {
  workflow: string;
  /** A run id. With none, the block shows the latest run of the workflow. */
  run?: string;
}

/**
 * Read `props.source` of a flow node: `{action: "workflow", args: {id, run?}}`. The block does not
 * read through the package door, because a workflow belongs to the organization and not to a
 * package. So `action` only names the kind, and `args` names the workflow.
 */
export function parseFlowSource(raw: unknown): FlowSource | null {
  if (!isObject(raw) || !isObject(raw.args)) return null;
  const id = typeof raw.args.id === "string" ? raw.args.id.trim() : "";
  if (!id) return null;
  const run = typeof raw.args.run === "string" ? raw.args.run.trim() : "";
  return { workflow: id, ...(run ? { run } : {}) };
}

/** A node of the page is a flow when it is the `flow` template with a valid source. */
export function isFlowNode(type: string, props: Record<string, unknown>): boolean {
  return type === "template" && props.name === "flow" && parseFlowSource(props.source) !== null;
}

// --- the graph -----------------------------------------------------------------

/** The step kinds that the editor card can draw. Another kind draws as a tool step. */
const NODE_TYPES = ["trigger", "agent", "tool", "module", "condition", "set", "approval", "wait", "output"] as const;
export type FlowNodeType = (typeof NODE_TYPES)[number];

export interface FlowNodeView {
  id: string;
  type: "ccNode";
  position: { x: number; y: number };
  draggable: false;
  connectable: false;
  selectable: false;
  /** A first size, so a first paint and a test draw the card before the browser measures it. */
  initialWidth: number;
  initialHeight: number;
  data: { label: string; nodeType: FlowNodeType; runStatus?: RunNodeStatus; durationMs?: number };
}

export interface FlowEdgeView {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  type: "ccEdge";
  data: { branch: "true" | "false" | null; hot: boolean };
}

/** The status words of a node card, as `WorkflowNode.tsx` takes them. */
export type RunNodeStatus = "running" | "ok" | "error" | "skipped" | "waiting" | "pending";

const finite = (n: unknown): number => (typeof n === "number" && Number.isFinite(n) ? n : 0);

/**
 * The nodes and the edges of a workflow answer. The answer is untrusted, so each part is checked.
 * A node keeps its id, its place, its label and its type. A node with no id and an edge to a
 * node that is not in the graph are left out. Nothing else of the node is copied.
 */
export function flowGraph(raw: unknown): { nodes: FlowNodeView[]; edges: FlowEdgeView[] } {
  const g = isObject(raw) ? raw : {};
  const nodes: FlowNodeView[] = [];
  const seen = new Set<string>();
  for (const n of Array.isArray(g.nodes) ? g.nodes : []) {
    if (!isObject(n) || typeof n.id !== "string" || !n.id || seen.has(n.id)) continue;
    const data = isObject(n.data) ? n.data : {};
    const nodeType: FlowNodeType = (NODE_TYPES as readonly unknown[]).includes(n.type) ? (n.type as FlowNodeType) : "tool";
    const pos = isObject(n.position) ? n.position : {};
    seen.add(n.id);
    nodes.push({
      id: n.id,
      type: "ccNode",
      position: { x: finite(pos.x), y: finite(pos.y) },
      draggable: false,
      connectable: false,
      selectable: false,
      initialWidth: 208,
      initialHeight: 96,
      data: { label: typeof data.label === "string" ? data.label : "", nodeType },
    });
  }
  const edges: FlowEdgeView[] = [];
  for (const e of Array.isArray(g.edges) ? g.edges : []) {
    if (!isObject(e) || typeof e.source !== "string" || typeof e.target !== "string") continue;
    if (!seen.has(e.source) || !seen.has(e.target)) continue;
    const handle = e.sourceHandle === "true" || e.sourceHandle === "false" ? e.sourceHandle : null;
    edges.push({
      id: typeof e.id === "string" && e.id ? e.id : `${e.source}->${e.target}`,
      source: e.source,
      target: e.target,
      ...(handle ? { sourceHandle: handle } : {}),
      type: "ccEdge",
      data: { branch: handle, hot: false },
    });
  }
  return { nodes, edges };
}

// --- run state -----------------------------------------------------------------

/** The status that the gateway gives a node, in the words of the node card. Unknown reads as idle. */
export function nodeStatus(raw: unknown): RunNodeStatus | undefined {
  switch (raw) {
    case "running":
    case "ok":
    case "error":
    case "skipped":
    case "waiting":
    case "pending":
      return raw;
    case "failed":
      return "error";
    case "succeeded":
      return "ok";
    case "paused":
      return "waiting";
    case "queued":
      return "pending";
    default:
      return undefined;
  }
}

/** The state of each step, by node id. A step that is not in it has not run. */
export type NodeStates = Record<string, { status: RunNodeStatus; durationMs?: number }>;

/** The recorded states of one run (`node_results` of the run answer). */
export function statesFromRun(results: unknown): NodeStates {
  const out: NodeStates = {};
  if (!isObject(results)) return out;
  for (const [id, r] of Object.entries(results)) {
    if (!isObject(r)) continue;
    const status = nodeStatus(r.status);
    if (!status) continue;
    out[id] = { status, ...(typeof r.duration_ms === "number" ? { durationMs: r.duration_ms } : {}) };
  }
  return out;
}

/** One frame of the run stream. A node frame sets the state of one step, and any other frame leaves the states as they are. */
export function applyRunEvent(states: NodeStates, ev: unknown): NodeStates {
  if (!isObject(ev) || ev.event !== "node" || typeof ev.node_id !== "string") return states;
  const status = nodeStatus(ev.status);
  if (!status) return states;
  return {
    ...states,
    [ev.node_id]: { status, ...(typeof ev.duration_ms === "number" ? { durationMs: ev.duration_ms } : {}) },
  };
}

/** The run status that a `run` frame of the stream gives, or `null` for any other frame. */
export function runStatusOf(ev: unknown): string | null {
  return isObject(ev) && ev.event === "run" && typeof ev.status === "string" ? ev.status : null;
}

/** True while a run can still change: the block listens to the stream, or polls. */
export function isLive(status: string | null | undefined): boolean {
  return status === "queued" || status === "running";
}

/** The nodes of the graph with the state of each step, and the edges lit where the run flows. */
export function paintFlow(
  graph: { nodes: FlowNodeView[]; edges: FlowEdgeView[] },
  states: NodeStates,
): { nodes: FlowNodeView[]; edges: FlowEdgeView[] } {
  const nodes = graph.nodes.map((n) => {
    const s = states[n.id];
    return s ? { ...n, data: { ...n.data, runStatus: s.status, durationMs: s.durationMs } } : n;
  });
  const edges = graph.edges.map((e) => {
    const from = states[e.source]?.status;
    const to = states[e.target]?.status;
    const hot = from === "ok" && (to === "running" || to === "waiting" || to === "ok" || to === "error");
    return hot ? { ...e, data: { ...e.data, hot } } : e;
  });
  return { nodes, edges };
}

/** The block state of a node card: a step with no result is idle, and "pending" is queued. */
export function flowStateOf(status: RunNodeStatus | undefined): FlowState {
  switch (status) {
    case "running": return "running";
    case "ok": return "ok";
    case "error": return "failed";
    case "skipped": return "skipped";
    case "waiting": return "waiting";
    case "pending": return "queued";
    default: return "idle";
  }
}

/** The state of the run, from its status word. */
export function runState(status: string | null | undefined): FlowState {
  switch (status) {
    case "queued": return "queued";
    case "running": return "running";
    case "succeeded": return "ok";
    case "failed": return "failed";
    case "cancelled": return "skipped";
    case "paused": return "waiting";
    default: return "idle";
  }
}

/** How many steps are in each state, for the legend. A state with no step is left out. */
export function stateCounts(nodes: FlowNodeView[]): Array<{ state: FlowState; count: number }> {
  const order: FlowState[] = ["running", "waiting", "failed", "ok", "queued", "skipped", "idle"];
  const counts = new Map<FlowState, number>();
  for (const n of nodes) {
    const st = flowStateOf(n.data.runStatus);
    counts.set(st, (counts.get(st) ?? 0) + 1);
  }
  return order.filter((s) => counts.has(s)).map((state) => ({ state, count: counts.get(state) as number }));
}

/** The facts of a run that the status line needs. */
export interface FlowRunFacts {
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
}

/** "Started 5m ago" or "Finished 2h ago", in plain words. A run that has not started says so. */
export function runWhen(run: FlowRunFacts, nowMs: number = Date.now()): string {
  const done = !isLive(run.status) && run.status !== "paused";
  const iso = done ? (run.finished_at ?? run.started_at) : run.started_at;
  const ago = relativeTime(iso, nowMs);
  if (!ago) return "";
  const when = ago === "now" ? "just now" : ago;
  return `${done ? "Finished" : "Started"} ${when}`;
}

/** The plain words of a failed read. The fault never shows its raw text. */
export function flowReadError(status: number | undefined): string {
  if (status === 404) return "This workflow no longer exists.";
  if (status === 401 || status === 403) return "You cannot see this workflow.";
  return "Could not load this workflow. Try again.";
}

/** A retry helps only when the fault can pass. A missing or a hidden workflow stays so. */
export function flowCanRetry(error: string | null | undefined): boolean {
  return !!error && error === flowReadError(undefined);
}
