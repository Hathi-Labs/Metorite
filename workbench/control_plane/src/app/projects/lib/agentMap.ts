/**
 * Agents at work · the Map (WS-MCP SW-103, `agent_coordination.md` §3.12).
 *
 * The gateway reads the orchestration map (`GET /projects/agents/overview?shape=map`,
 * SW-102). This file checks the shape of the answer, places the nodes from
 * left to right, and builds the outline for a phone. It draws nothing.
 *
 * ⚠️ Every string in the answer is untrusted display text. The view draws it
 * as plain text and never as markup.
 *
 * ⚠️ One colour vocabulary. A state becomes a chip through `agentStateChip`
 * of `lib/statusAccent.ts`, and through nothing else.
 *
 * SW-110: a session opens into the sub-agents (child runs of SW-39) of its
 * runs. The gateway lists a child only under a run that the viewer may see.
 * Collapsed is the default, and the view keeps the open sessions in local state.
 */
import { agentStateChip, agentStateVisual } from "@/lib/statusAccent";
import { relativeTime } from "@/lib/taskCard";

import {
  type HostKind,
  type HostSource,
  KIND_RANK,
  NOT_REPORTED,
  type Place,
  placeOfRow,
} from "./agentPlace";
import { type AgentWaitingOn, WAITING_ON_LABELS } from "./agentRuns";
import { outcomeWord } from "./agentsAtWork";

/** `failed` and `done` come from the summary read of AMR-4 only (§3.12.9). */
export type MapSessionState = "waiting_on_person" | "waiting" | "working" | "quiet" | "failed" | "done";

/** AMR-4: the sub-agents of one session of the summary read, as counts. The names are untrusted text. */
export interface MapSubagents {
  total: number;
  working: number;
  failed: number;
  done: number;
  names: string[];
}

/** One task of a session of the summary read, so find reaches it. The title is untrusted text. */
export interface MapTaskWords {
  number: number | null;
  title: string;
}

export interface MapPerson {
  person: string;
  sessions: number;
}

export interface MapSession {
  id: string;
  person: string;
  name: string;
  client: string;
  state: MapSessionState;
  last_at: string | null;
  run_count: number;
  quiet_count: number;
  quiet_last_at: string | null;
  /**
   * SW-106. The machine, its kind, how it is known and the model. The agent
   * chose the name and the model, so both are untrusted text. `null` is "Not
   * reported". `source` is `checked` only for a box token.
   */
  host: string | null;
  host_kind: HostKind | null;
  host_source: HostSource | null;
  model: string | null;
  /** AMR-4: present on the summary read. The map of SW-102 sends `children` instead. */
  subagents?: MapSubagents;
  /** AMR-4: the tasks of its listed runs, on the summary read, capped by the gateway. */
  tasks?: MapTaskWords[];
}

export interface MapRun {
  run_id: string;
  session_id: string;
  task_id: string;
  task_number: number | null;
  title: string;
  state: string;
  quiet: boolean;
  age: number | null;
  last_at: string | null;
  /** AMR-1. What a waiting run waits on, and until when. `null` for a run that does not wait. */
  waiting_on: AgentWaitingOn | null;
  waiting_until: string | null;
}

export interface MapLine {
  from: string;
  to: string;
}

/**
 * SW-110. One sub-agent (a child run of SW-39) of a listed run. `run_id` is
 * the parent run, as in `runs`. The name and the model are untrusted text.
 * `model` is the child's own model, or `null` when it reported none.
 */
export interface MapChild {
  child_id: string;
  run_id: string;
  session_id: string;
  name: string;
  state: string;
  model: string | null;
  age: number | null;
  last_at: string | null;
}

export interface AgentMap {
  people: MapPerson[];
  sessions: MapSession[];
  runs: MapRun[];
  lines: MapLine[];
  /** SW-110. The open sub-agents of the listed runs. Not in `count`. */
  children: MapChild[];
  count: number;
  truncated: boolean;
  viewer: string;
}

/** What the Map shows. `dark` means the map read answered 404, 401 or 403. */
export type AgentMapRead =
  | { kind: "loading" }
  | { kind: "dark" }
  | { kind: "error" }
  | { kind: "ready"; map: AgentMap };

/** The performance mark of a summary answer (AMR-4). */
export const MAP_RESPONSE_MARK = "agents-view:map-response";
export const MAP_LABEL = "Map";
export const LIST_LABEL = "List";
export const SWITCH_LABEL = "Agents view";
export const CANVAS_LABEL = "Map of people, sessions and tasks";
export const OUTLINE_LABEL = "Outline of people, sessions and tasks";
export const MAP_ERROR_COPY = "The map did not load. Check your connection, then open it again.";
export const MAP_EMPTY_COPY = "No agent session is open now.";
export const PROJECT_MAP_EMPTY_COPY = "No agent works on this project yet.";
export const MAP_TRUNCATED_COPY = "The map shows the first sessions, runs and sub-agents only.";
export const SHOW_QUIET_LABEL = "Show quiet runs";
export const GROUP_BY_MACHINE_LABEL = "Group by machine";
export const DISPATCHED_BY_LABEL = "Dispatched by";
export const DISPATCHED_LABEL = "Dispatched";
export const OPEN_TASK_LABEL = "Open task";
/** SW-110. The name of a sub-agent that reported no name. */
export const UNNAMED_CHILD = "Sub-agent";

/** SW-110. The name of a sub-agent in a spoken label. An unnamed one is not doubled: "Sub-agent", not "Sub-agent Sub-agent". */
export function childSpokenName(name: string): string {
  return name === UNNAMED_CHILD ? UNNAMED_CHILD : `${UNNAMED_CHILD} ${name}`;
}

/**
 * SW-110. The key of the canvas. A new key refits the view, so the group
 * switch and the open or close of a session refit, and a re-read with the same
 * sessions open keeps the pan. It reads the layout, so an id of a session that
 * is gone, or has no sub-agent, changes nothing.
 */
export function canvasKey(byMachine: boolean, nodes: readonly Pick<MapNode, "id" | "expanded">[]): string {
  const open = nodes.filter((n) => n.expanded === true).map((n) => n.id).sort(compareText);
  return `${byMachine ? "by-machine" : "by-person"}|${open.join(",")}`;
}

/** SW-110. The words of the control that opens the sub-agents of a session. */
export function childToggleLabel(count: number, expanded: boolean): string {
  const noun = count === 1 ? "sub-agent" : "sub-agents";
  return `${expanded ? "Hide" : "Show"} ${count} ${noun}`;
}

/** SW-110. "5m ago", from the age of a sub-agent in seconds. Empty for none. */
export function childStartedWhen(age: number | null, nowMs: number): string {
  if (age === null) return "";
  return relativeTime(new Date(nowMs - age * 1000).toISOString(), nowMs);
}

/** SW-110. "Started 5m ago". Empty for none. */
export function childAgeWord(age: number | null, nowMs: number): string {
  const when = childStartedWhen(age, nowMs);
  return when ? `Started ${when}` : "";
}

const SESSION_STATES: readonly MapSessionState[] = [
  "waiting_on_person",
  "waiting",
  "working",
  "quiet",
  "failed",
  "done",
];

/** Waiting sessions come first, then failed, working, quiet and done ones. */
const STATE_RANK: Record<MapSessionState, number> = {
  waiting_on_person: 0,
  failed: 1,
  waiting: 2,
  working: 3,
  quiet: 4,
  done: 5,
};

/** Each state of a session maps to a state of the one agent vocabulary. */
const STATE_VOCABULARY: Record<MapSessionState, string> = {
  waiting_on_person: "awaiting",
  waiting: "waiting",
  working: "working",
  quiet: "stale",
  failed: "failed",
  done: "completed",
};

const STATE_WORD: Record<MapSessionState, string> = {
  waiting_on_person: "Waiting for a person",
  waiting: "Waiting for an agent",
  working: "Working",
  quiet: "Quiet",
  failed: "Failed",
  done: "Done",
};

/**
 * SW-109. A quiet orchestrator with a live lane is in a quiet stretch between events. It reads
 * "Waiting", and never "lost" (`agent_coordination.md` §3.12.2). A session
 * that dispatched no lane keeps the word "Quiet".
 */
export const ORCHESTRATOR_QUIET_WORD = "Waiting";

const isQuietWait = (state: MapSessionState, waits: boolean): boolean => waits && state === "quiet";

/**
 * `waits` is true when the session dispatched at least one lane that is not
 * quiet (`DispatchGraph.waiting`). If every lane is quiet, the session reads Quiet.
 */
export function sessionStateWord(state: MapSessionState, waits = false): string {
  return isQuietWait(state, waits) ? ORCHESTRATOR_QUIET_WORD : STATE_WORD[state];
}

export function sessionStateChip(state: MapSessionState, waits = false): string {
  return agentStateChip(isQuietWait(state, waits) ? "waiting" : STATE_VOCABULARY[state]);
}

export function sessionStateIcon(state: MapSessionState, waits = false): string {
  const key = isQuietWait(state, waits) ? "waiting" : STATE_VOCABULARY[state];
  return agentStateVisual(key)?.icon ?? "Bot";
}

/** The word of a run. A quiet run reads as quiet, whatever its state. */
export function runStateWord(run: Pick<MapRun, "state" | "quiet">): string {
  return run.quiet ? "Quiet" : outcomeWord(run.state);
}

export function runStateChip(run: Pick<MapRun, "state" | "quiet">): string {
  return agentStateChip(run.quiet ? "stale" : run.state);
}

export function runStateIcon(run: Pick<MapRun, "state" | "quiet">): string {
  return agentStateVisual(run.quiet ? "stale" : run.state)?.icon ?? "Bot";
}

/** SW-110. The word, the chip and the icon of a sub-agent: the words of a run that is not quiet. */
export function childStateWord(child: Pick<MapChild, "state">): string {
  return runStateWord({ state: child.state, quiet: false });
}

export function childStateChip(child: Pick<MapChild, "state">): string {
  return runStateChip({ state: child.state, quiet: false });
}

export function childStateIcon(child: Pick<MapChild, "state">): string {
  return runStateIcon({ state: child.state, quiet: false });
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/** A lane in one of these states does no work, so it holds no orchestrator in a wait. */
const ENDED_LANE: ReadonlySet<MapSessionState> = new Set(["quiet", "failed", "done"]);

/** The sub-agent counts of a summary row, or `undefined` when the row sends none. */
function subagentsOf(value: unknown): MapSubagents | undefined {
  const row = record(value);
  if (!row) return undefined;
  return {
    total: whole(row.total),
    working: whole(row.working),
    failed: whole(row.failed),
    done: whole(row.done),
    names: rows(row.names).map(text).filter(Boolean),
  };
}

/** The tasks of a summary row. A row with no title is dropped. */
function taskWordsOf(value: unknown[]): MapTaskWords[] {
  return value.flatMap((item) => {
    const row = record(item);
    const title = row ? text(row.title) : "";
    if (!row || !title) return [];
    return [{ number: typeof row.number === "number" && Number.isFinite(row.number) ? row.number : null, title }];
  });
}

/** One of the words of `WAITING_ON_LABELS`, else `null`. An older gateway sends none. */
function waitingOn(value: unknown): AgentWaitingOn | null {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(WAITING_ON_LABELS, value)
    ? (value as AgentWaitingOn)
    : null;
}

function whole(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.floor(value) : 0;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/** The four place fields of a session row (SW-106). Each bad value is `null`. */
function placeFields(row: Record<string, unknown>): Pick<MapSession, "host" | "host_kind" | "host_source" | "model"> {
  const { host, kind, source, model } = placeOfRow(row);
  return { host, host_kind: kind, host_source: source, model };
}

function rows(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/**
 * Check the answer of the map read. A row it cannot read is dropped. A body
 * with no `sessions` list is refused (`null`).
 */
export function parseAgentMap(raw: unknown): AgentMap | null {
  const body = record(raw);
  if (!body || !Array.isArray(body.sessions) || !Array.isArray(body.runs)) return null;

  const sessions: MapSession[] = [];
  const seen = new Set<string>();
  for (const item of body.sessions) {
    const row = record(item);
    if (!row) continue;
    const id = text(row.id);
    const person = text(row.person);
    const state = text(row.state) as MapSessionState;
    if (!id || !person || !SESSION_STATES.includes(state) || seen.has(id)) continue;
    seen.add(id);
    sessions.push({
      id,
      person,
      name: text(row.name) || "Unnamed session",
      client: text(row.client) || "Agent",
      state,
      last_at: text(row.last_at) || null,
      run_count: whole(row.run_count),
      quiet_count: whole(row.quiet_count),
      quiet_last_at: text(row.quiet_last_at) || null,
      ...placeFields(row),
      ...(record(row.subagents) ? { subagents: subagentsOf(row.subagents) } : {}),
      ...(Array.isArray(row.tasks) ? { tasks: taskWordsOf(row.tasks) } : {}),
    });
  }

  const runs: MapRun[] = [];
  const seenRuns = new Set<string>();
  for (const item of body.runs) {
    const row = record(item);
    if (!row) continue;
    const run_id = text(row.run_id);
    const session_id = text(row.session_id);
    const task_id = text(row.task_id);
    if (!run_id || !task_id || !seen.has(session_id) || seenRuns.has(run_id)) continue;
    seenRuns.add(run_id);
    runs.push({
      run_id,
      session_id,
      task_id,
      task_number: typeof row.task_number === "number" && Number.isFinite(row.task_number) ? row.task_number : null,
      title: text(row.title) || "Untitled task",
      state: text(row.state),
      quiet: row.quiet === true,
      age: typeof row.age === "number" && Number.isFinite(row.age) ? row.age : null,
      last_at: text(row.last_at) || null,
      waiting_on: waitingOn(row.waiting_on),
      waiting_until: text(row.waiting_until) || null,
    });
  }

  // SW-110. A child must name a listed run, and the session of that run.
  const sessionOfRun = new Map(runs.map((r) => [r.run_id, r.session_id]));
  const children: MapChild[] = [];
  const seenChildren = new Set<string>();
  for (const item of rows(body.children)) {
    const row = record(item);
    if (!row) continue;
    const child_id = text(row.child_id);
    const run_id = text(row.run_id);
    const session_id = sessionOfRun.get(run_id);
    if (!child_id || !session_id || text(row.session_id) !== session_id || seenChildren.has(child_id)) continue;
    seenChildren.add(child_id);
    children.push({
      child_id,
      run_id,
      session_id,
      name: text(row.name) || UNNAMED_CHILD,
      state: text(row.state) || "working",
      model: text(row.model) || null,
      age: typeof row.age === "number" && Number.isFinite(row.age) && row.age >= 0 ? Math.floor(row.age) : null,
      last_at: text(row.last_at) || null,
    });
  }

  const lines: MapLine[] = [];
  for (const item of rows(body.lines)) {
    const row = record(item);
    const from = text(row?.from);
    const to = text(row?.to);
    if (from && to) lines.push({ from, to });
  }

  const people: MapPerson[] = [];
  const counts = new Map<string, number>();
  for (const s of sessions) counts.set(s.person, (counts.get(s.person) ?? 0) + 1);
  for (const [person, n] of counts) people.push({ person, sessions: n });

  return {
    people,
    sessions,
    runs,
    lines,
    children,
    count: whole(body.count),
    truncated: body.truncated === true,
    viewer: text(body.viewer),
  };
}

/* ── The layout ─────────────────────────────────────────────────────────── */

export type MapNodeKind = "person" | "machine" | "session" | "task" | "quiet" | "child";

export interface MapNode {
  id: string;
  kind: MapNodeKind;
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  /** The second line: the client, the task number, or the quiet words. */
  detail: string;
  /** The session state, or the run state word key. Empty for a person. */
  state: MapSessionState | null;
  /** The raw run state, for a task node. */
  runState?: string;
  quiet?: boolean;
  /** The session id, the task id, the person or the machine key, by kind. */
  ref: string;
  /** The task id of a task node. */
  taskId?: string;
  /** The session a node belongs to. */
  sessionId?: string;
  /** SW-106. The machine and the model of a session or a machine node. */
  place?: Place;
  /** SW-109. True for a quiet session node that waits on a lane that is not quiet. It reads "Waiting". */
  quietWait?: boolean;
  /** SW-110. On a session node: how many sub-agents its listed runs hold. */
  childCount?: number;
  /** SW-110. On a session node with sub-agents: true when they show. */
  expanded?: boolean;
  /** SW-110. On a child node: its own model, or `null` for none. */
  model?: string | null;
  /** SW-110. On a child node: the parent run. */
  runId?: string;
}

export interface MapEdge {
  id: string;
  from: string;
  to: string;
  kind: "tree" | "line";
}

export interface MapLayout {
  nodes: MapNode[];
  edges: MapEdge[];
}

export interface MapLayoutOptions {
  /** SW-106. Group the sessions under a machine node. The default is off. */
  groupByMachine?: boolean;
  /** SW-110. The ids of the sessions whose sub-agents show. The default is none. */
  expanded?: ReadonlySet<string>;
}

export const COLUMN_X = { person: 0, session: 300, task: 640 } as const;
/**
 * The columns of the map grouped by machine: machine, session, task. No person
 * node and no person line: the person shows on the client line of each session.
 */
export const COLUMN_X_BY_MACHINE = { machine: 0, session: 280, task: 620 } as const;
/**
 * SW-109. The by-person map has one session column for each depth of the
 * dispatch chain. A session at depth 0 has no dispatcher on the map. Each
 * deeper session sits one step to the right of the session that dispatched it.
 * The depth stops at the cap, so the canvas stays bounded.
 *
 * Accepted limits: a chain longer than the cap puts a lane in the column of its
 * dispatcher, so its arrow runs backwards. A line that skips a depth can pass
 * behind the card of a session in between. Both are rare, and no data is lost.
 */
export const MAX_DISPATCH_DEPTH = 4;
export const DEPTH_STEP = 340;
export const NODE_W = { person: 220, machine: 220, session: 280, task: 300, quiet: 300, child: 260 } as const;
/** Each height is fixed. A session holds four lines: name, state, machine, model. */
export const NODE_H = { person: 48, machine: 48, session: 96, task: 52, quiet: 32, child: 52 } as const;
/** SW-110. A session with sub-agents is this much taller: the row of its control. */
export const CHILD_TOGGLE_H = 32;
/** SW-110. The sub-agents sit one column to the right of the tasks of their session. */
/** The gap between the task column and the first sub-agent column equals the session-to-task gap. */
export const CHILD_GAP = DEPTH_STEP - NODE_W.session;
/** The x of the first sub-agent column, from the x of the task column. */
export const CHILD_OFFSET = NODE_W.task + CHILD_GAP;
/** The x step from one sub-agent column to the next. */
export const CHILD_STEP = NODE_W.child + CHILD_GAP;
/**
 * The most sub-agent rows in one column: more fill the next column. Eight keeps
 * up to eight sub-agents in one column, as wide as the case of three, so the
 * fit scale stays readable. The map pane is wide and has room in its height.
 */
export const CHILD_ROWS_MAX = 8;
const GAP = 12;
const BLOCK_GAP = 24;

function compareText(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

/** A quiet session that reads "Waiting" (SW-109) ranks as a waiting session. */
function rankOf(s: MapSession, waiting?: ReadonlySet<string>): number {
  return waiting?.has(s.id) ? STATE_RANK.waiting : STATE_RANK[s.state];
}

/** Waiting first, then working, then quiet. Ties by name, person, then id. */
export function sortSessions(sessions: readonly MapSession[], waiting?: ReadonlySet<string>): MapSession[] {
  return [...sessions].sort(
    (a, b) =>
      rankOf(a, waiting) - rankOf(b, waiting) ||
      compareText(a.name.toLowerCase(), b.name.toLowerCase()) ||
      compareText(a.person, b.person) ||
      compareText(a.id, b.id),
  );
}

function runsOf(map: AgentMap, sessionId: string): MapRun[] {
  return map.runs
    .filter((r) => r.session_id === sessionId)
    .sort((a, b) => Number(a.quiet) - Number(b.quiet) || compareText(a.run_id, b.run_id));
}

/**
 * SW-110. The sub-agents of one run, oldest first (`age` is seconds since the
 * start, so a larger age is older), then by id. A sub-agent with no age sorts
 * last. The order never depends on the input.
 */
export function childrenOf(map: Pick<AgentMap, "children">, runId: string): MapChild[] {
  return map.children
    .filter((c) => c.run_id === runId)
    .sort(
      (a, b) =>
        Number(a.age === null) - Number(b.age === null) ||
        (b.age ?? 0) - (a.age ?? 0) ||
        compareText(a.child_id, b.child_id),
    );
}

/** How many quiet runs a session holds that the answer does not list. */
function hiddenQuiet(session: MapSession, runs: readonly MapRun[]): number {
  const listed = runs.filter((r) => r.quiet).length;
  return Math.max(0, session.quiet_count - listed);
}

/** "3 quiet runs, newest 3h ago". */
export function quietLine(count: number, lastAt: string | null, nowMs: number): string {
  const noun = count === 1 ? "quiet run" : "quiet runs";
  const when = relativeTime(lastAt, nowMs);
  return when ? `${count} ${noun}, newest ${when}` : `${count} ${noun}`;
}

/* ── Who dispatched whom (SW-109) ───────────────────────────────────────── */

export interface DispatchGraph {
  /** The lines that the map draws: both ends on the map, no self line, no copy, no loop. */
  lines: MapLine[];
  /** Session id to its depth. Only a session that holds a line is in the map; others read 0. */
  depth: Map<string, number>;
  /** Session id to the ids of the sessions that dispatched it, sorted. */
  dispatchers: Map<string, string[]>;
  /** Session id to the ids of the sessions that it dispatched, sorted. */
  dispatched: Map<string, string[]>;
  /**
   * The quiet sessions that read "Waiting": each holds a kept line to a lane
   * that is not quiet. A quiet session whose lanes are all quiet reads Quiet,
   * so a whole tree never reads Waiting over quiet lanes.
   */
  waiting: Set<string>;
}

/**
 * Clean the lines of the map read and compute the depth of each session.
 *
 * - A line with an end that is not a session of the map, or with the same end
 *   twice, drops. A copy of a line drops.
 * - The data may hold a loop, because two claims at once can write one
 *   (§3.12.4). A depth-first pass visits the sessions by sorted id, and takes
 *   the lines of each by sorted id. A line into a session that is still open
 *   on the walk closes a loop, and drops. The result depends on the ids, and
 *   never on the input order.
 * - The depth of a session is the longest chain of dispatchers above it,
 *   stopped at `MAX_DISPATCH_DEPTH`.
 */
export function dispatchGraph(map: Pick<AgentMap, "sessions" | "lines">): DispatchGraph {
  const known = new Set(map.sessions.map((s) => s.id));
  const unique = new Map<string, MapLine>();
  for (const line of map.lines) {
    if (line.from === line.to || !known.has(line.from) || !known.has(line.to)) continue;
    unique.set(`${line.from}\u0000${line.to}`, line);
  }
  const out = new Map<string, string[]>();
  for (const { from, to } of unique.values()) {
    const list = out.get(from) ?? [];
    list.push(to);
    out.set(from, list);
  }
  for (const list of out.values()) list.sort(compareText);
  const involved = new Set<string>();
  for (const { from, to } of unique.values()) {
    involved.add(from);
    involved.add(to);
  }

  const OPEN = 1;
  const DONE = 2;
  const mark = new Map<string, number>();
  const finished: string[] = [];
  const kept: MapLine[] = [];
  for (const root of [...involved].sort(compareText)) {
    if (mark.has(root)) continue;
    mark.set(root, OPEN);
    const stack: Array<{ id: string; next: number }> = [{ id: root, next: 0 }];
    while (stack.length > 0) {
      const top = stack[stack.length - 1];
      const kids = out.get(top.id) ?? [];
      if (top.next < kids.length) {
        const kid = kids[top.next++];
        const seen = mark.get(kid);
        if (seen === OPEN) continue; // This line closes a loop: drop it.
        kept.push({ from: top.id, to: kid });
        if (seen === undefined) {
          mark.set(kid, OPEN);
          stack.push({ id: kid, next: 0 });
        }
      } else {
        mark.set(top.id, DONE);
        finished.push(top.id);
        stack.pop();
      }
    }
  }

  const dispatchers = new Map<string, string[]>();
  const dispatched = new Map<string, string[]>();
  for (const { from, to } of kept) {
    dispatchers.set(to, [...(dispatchers.get(to) ?? []), from]);
    dispatched.set(from, [...(dispatched.get(from) ?? []), to]);
  }
  for (const list of dispatchers.values()) list.sort(compareText);
  for (const list of dispatched.values()) list.sort(compareText);

  // The reverse of the finish order puts each dispatcher before its lanes.
  const depth = new Map<string, number>();
  for (const id of [...finished].reverse()) {
    let d = 0;
    for (const up of dispatchers.get(id) ?? []) d = Math.max(d, (depth.get(up) ?? 0) + 1);
    depth.set(id, Math.min(d, MAX_DISPATCH_DEPTH));
  }
  kept.sort((a, b) => compareText(a.from, b.from) || compareText(a.to, b.to));
  const stateOf = new Map(map.sessions.map((s) => [s.id, s.state]));
  const waiting = new Set<string>();
  for (const [id, lanes] of dispatched) {
    if (stateOf.get(id) === "quiet" && lanes.some((lane) => !ENDED_LANE.has(stateOf.get(lane) ?? "quiet"))) {
      waiting.add(id);
    }
  }
  return { lines: kept, depth, dispatchers, dispatched, waiting };
}

/** "Claude Code · 3h ago": the last event of a quiet orchestrator joins the client line. */
function withLastEvent(client: string, s: MapSession, waits: boolean, nowMs: number): string {
  if (!isQuietWait(s.state, waits)) return client;
  const when = relativeTime(s.last_at, nowMs);
  return when ? `${client} \u00b7 ${when}` : client;
}

function groupByPerson(map: AgentMap, waiting?: ReadonlySet<string>): Array<[string, MapSession[]]> {
  const groups = new Map<string, MapSession[]>();
  for (const s of map.sessions) {
    const list = groups.get(s.person) ?? [];
    list.push(s);
    groups.set(s.person, list);
  }
  return [...groups.entries()]
    .map(([person, list]): [string, MapSession[]] => [person, sortSessions(list, waiting)])
    .sort((a, b) => rankOf(a[1][0], waiting) - rankOf(b[1][0], waiting) || compareText(a[0], b[0]));
}

export function sessionPlace(s: Pick<MapSession, "host" | "host_kind" | "host_source" | "model">): Place {
  return { host: s.host, kind: s.host_kind, source: s.host_source, model: s.model };
}

/** One machine of the grouped map (SW-106, §3.12.7 rule 5). */
export interface MachineGroup {
  key: string;
  place: Place;
  sessions: MapSession[];
}

/** The key of the group with no machine and no kind: it sorts last. */
export const NO_MACHINE_KEY = "none";

/**
 * Group the sessions by the kind of machine, then by its name, then by its
 * source. A reported machine never joins a checked machine of the same name,
 * so the mark "checked" of a box token cannot flip. A session that reports
 * neither a name nor a kind goes in the group "Not reported", which comes
 * last. The order is fixed: the same input gives the same groups.
 */
export function groupByMachine(sessions: readonly MapSession[], waiting?: ReadonlySet<string>): MachineGroup[] {
  const groups = new Map<string, MapSession[]>();
  for (const s of sessions) {
    const key =
      !s.host && !s.host_kind
        ? NO_MACHINE_KEY
        : `${s.host_kind ?? "-"}|${(s.host ?? "").toLowerCase()}|${s.host_source ?? "-"}`;
    const list = groups.get(key) ?? [];
    list.push(s);
    groups.set(key, list);
  }
  const rank = (key: string, first: MapSession): number =>
    key === NO_MACHINE_KEY ? 4 : first.host_kind ? KIND_RANK[first.host_kind] : 3;
  return [...groups.entries()]
    .map(([key, list]): MachineGroup => {
      const ordered = sortSessions(list, waiting);
      const first = ordered[0];
      const allChecked = ordered.every((s) => s.host_source === "checked");
      return {
        key,
        sessions: ordered,
        place: {
          host: first.host,
          kind: first.host_kind,
          source: !first.host && !first.host_kind ? null : allChecked ? "checked" : "reported",
          model: null,
        },
      };
    })
    .sort(
      (a, b) =>
        rank(a.key, a.sessions[0]) - rank(b.key, b.sessions[0]) ||
        compareText((a.place.host ?? "").toLowerCase(), (b.place.host ?? "").toLowerCase()) ||
        compareText(a.key, b.key),
    );
}

/**
 * Place the nodes. Columns from left to right: person, one column of sessions
 * for each depth of the dispatch chain (SW-109), then task. A session with no
 * dispatcher on the map has depth 0 and hangs from its person. A session that
 * another session dispatched sits one column right of it, in the block of its
 * own person, and hangs from its dispatcher by a dashed line (also across
 * people). A loop in the data drops one line, so the layout has no loop.
 * With `groupByMachine`, the columns are machine, session and task. The
 * sessions sit in blocks beside their machine, and no person node or person
 * line is drawn: the person shows on the client line of the session. The
 * same input gives the same output. Each block of one session takes the
 * height of its tasks, so no two nodes overlap. No layout library.
 */
export function layoutAgentMap(
  map: AgentMap,
  nowMs: number = Date.now(),
  options: MapLayoutOptions = {},
): MapLayout {
  const nodes: MapNode[] = [];
  const edges: MapEdge[] = [];
  const grouped = options.groupByMachine === true;
  const dispatch = dispatchGraph(map);
  // Grouped, there are no depth columns: every session sits in the one column.
  const depthOf = (id: string): number => (grouped ? 0 : (dispatch.depth.get(id) ?? 0));
  const columns = grouped ? COLUMN_X_BY_MACHINE : { ...COLUMN_X, machine: 0 };
  // By person, the tasks of a session sit one step right of that session. Grouped, they share one column.
  const sessionX = (id: string): number => columns.session + DEPTH_STEP * depthOf(id);
  const taskX = (id: string): number => (grouped ? columns.task : sessionX(id) + DEPTH_STEP);
  const waits = (id: string): boolean => dispatch.waiting.has(id);
  // Grouped, the person joins the client line of the session (it has no node).
  const detailOf = (s: MapSession): string => {
    const client = withLastEvent(s.client, s, waits(s.id), nowMs);
    return grouped ? `${client} \u00b7 ${s.person}` : client;
  };
  let y = 0;

  /** Place one session and its tasks at `y`, then move `y` below the block. */
  const placeSession = (session: MapSession, personId: string | null, machineId: string | null): void => {
    const sid = `s:${session.id}`;
    const runs = runsOf(map, session.id);
    const hidden = hiddenQuiet(session, runs);
    // SW-110. The control row makes a session with sub-agents taller. Expanded, the sub-agents show.
    const childCount = runs.reduce((n, run) => n + childrenOf(map, run.run_id).length, 0);
    const expanded = childCount > 0 && options.expanded?.has(session.id) === true;
    const sessionH = childCount > 0 ? NODE_H.session + CHILD_TOGGLE_H : NODE_H.session;
    let ty = y;
    // SW-110. The sub-agents fill a grid right of the task column: CHILD_ROWS_MAX rows, column by column.
    const kids = expanded ? runs.flatMap((run) => childrenOf(map, run.run_id)) : [];
    const rowsH = Math.min(kids.length, CHILD_ROWS_MAX) * (NODE_H.child + GAP) - GAP;
    kids.forEach((child, i) => {
      const cid = `c:${child.child_id}`;
      const run = runs.find((r) => r.run_id === child.run_id)!;
      nodes.push({
        id: cid,
        kind: "child",
        x: taskX(session.id) + CHILD_OFFSET + Math.floor(i / CHILD_ROWS_MAX) * CHILD_STEP,
        y: y + (i % CHILD_ROWS_MAX) * (NODE_H.child + GAP),
        w: NODE_W.child,
        h: NODE_H.child,
        label: child.name,
        detail: childAgeWord(child.age, nowMs),
        state: null,
        runState: child.state,
        ref: child.child_id,
        taskId: run.task_id,
        sessionId: session.id,
        runId: run.run_id,
        model: child.model,
      });
      edges.push({ id: `t:${run.run_id}>${cid}`, from: `t:${run.run_id}`, to: cid, kind: "tree" });
    });
    for (const run of runs) {
      const tid = `t:${run.run_id}`;
      nodes.push({
        id: tid,
        kind: "task",
        x: taskX(session.id),
        y: ty,
        w: NODE_W.task,
        h: NODE_H.task,
        label: run.title,
        detail: run.task_number === null ? "" : `Task ${run.task_number}`,
        state: null,
        runState: run.state,
        quiet: run.quiet,
        ref: run.run_id,
        taskId: run.task_id,
        sessionId: session.id,
      });
      edges.push({ id: `${sid}>${tid}`, from: sid, to: tid, kind: "tree" });
      ty += NODE_H.task + GAP;
    }
    if (hidden > 0) {
      const qid = `q:${session.id}`;
      nodes.push({
        id: qid,
        kind: "quiet",
        x: taskX(session.id),
        y: ty,
        w: NODE_W.quiet,
        h: NODE_H.quiet,
        label: quietLine(hidden, session.quiet_last_at, nowMs),
        detail: "",
        state: "quiet",
        ref: session.id,
        sessionId: session.id,
      });
      edges.push({ id: `${sid}>${qid}`, from: sid, to: qid, kind: "tree" });
      ty += NODE_H.quiet + GAP;
    }
    nodes.push({
      id: sid,
      kind: "session",
      x: sessionX(session.id),
      y,
      w: NODE_W.session,
      h: sessionH,
      label: session.name,
      detail: detailOf(session),
      state: session.state,
      ref: session.id,
      sessionId: session.id,
      place: sessionPlace(session),
      quietWait: waits(session.id),
      ...(childCount > 0 ? { childCount, expanded } : {}),
    });
    // A lane that a session dispatched hangs from that session, and not from a person.
    if (personId && !dispatch.dispatchers.has(session.id)) {
      edges.push({ id: `${personId}>${sid}`, from: personId, to: sid, kind: "tree" });
    }
    if (machineId) edges.push({ id: `${machineId}>${sid}`, from: machineId, to: sid, kind: "tree" });
    y += Math.max(sessionH, ty - y - GAP, rowsH) + BLOCK_GAP;
  };

  const personNode = (person: string, count: number, at: number): MapNode => ({
    id: `p:${person}`,
    kind: "person",
    x: COLUMN_X.person,
    y: at,
    w: NODE_W.person,
    h: NODE_H.person,
    label: person,
    detail: count === 1 ? "1 session" : `${count} sessions`,
    state: null,
    ref: person,
  });

  if (!grouped) {
    for (const [person, sessions] of groupByPerson(map, dispatch.waiting)) {
      nodes.push(personNode(person, sessions.length, y));
      for (const session of sessions) placeSession(session, `p:${person}`, null);
    }
  } else {
    for (const group of groupByMachine(map.sessions, dispatch.waiting)) {
      const machineId = `m:${group.key}`;
      nodes.push({
        id: machineId,
        kind: "machine",
        x: columns.machine,
        y,
        w: NODE_W.machine,
        h: NODE_H.machine,
        label: group.place.host ?? NOT_REPORTED,
        detail: group.sessions.length === 1 ? "1 session" : `${group.sessions.length} sessions`,
        state: null,
        ref: group.key,
        place: group.place,
      });
      for (const session of group.sessions) placeSession(session, null, machineId);
    }
  }

  // The lines of SW-107, cleaned by `dispatchGraph`: a pair of sessions that this map draws.
  for (const line of dispatch.lines) {
    const from = `s:${line.from}`;
    const to = `s:${line.to}`;
    edges.push({ id: `${from}~${to}`, from, to, kind: "line" });
  }
  return { nodes, edges };
}

/* ── The outline for a phone ────────────────────────────────────────────── */

/** SW-110. One sub-agent under its task in the outline. */
export interface OutlineChild {
  id: string;
  name: string;
  word: string;
  chip: string;
  /** Its own model, or `null`: the outline then shows no model text. */
  model: string | null;
  /** "Started 5m ago", or empty. */
  age: string;
}

export interface OutlineTask {
  runId: string;
  taskId: string;
  title: string;
  number: number | null;
  word: string;
  chip: string;
  /** SW-110. The sub-agents of the run. The outline always lists them. */
  children: OutlineChild[];
}

export interface OutlineSession {
  id: string;
  name: string;
  client: string;
  state: MapSessionState;
  word: string;
  chip: string;
  /** SW-106. The machine and the model of the session. */
  place: Place;
  /** SW-109. The names of the sessions that dispatched this one, untrusted text. */
  dispatchedBy: string[];
  /** SW-109. True when the session is quiet and waits on a lane that is not quiet. */
  quietWait: boolean;
  /** SW-109. The last event of a quiet orchestrator, for example "3h ago". */
  lastEvent: string | null;
  tasks: OutlineTask[];
  quiet: string | null;
}

export interface OutlinePerson {
  person: string;
  sessions: OutlineSession[];
}

/** The same order as the canvas: person > sessions > tasks. */
export function agentMapOutline(map: AgentMap, nowMs: number = Date.now()): OutlinePerson[] {
  const dispatch = dispatchGraph(map);
  const nameOf = new Map(map.sessions.map((s) => [s.id, s.name]));
  return groupByPerson(map, dispatch.waiting).map(([person, sessions]) => ({
    person,
    sessions: sessions.map((s) => {
      const runs = runsOf(map, s.id);
      const hidden = hiddenQuiet(s, runs);
      const waits = dispatch.waiting.has(s.id);
      return {
        id: s.id,
        name: s.name,
        client: s.client,
        state: s.state,
        word: sessionStateWord(s.state, waits),
        chip: sessionStateChip(s.state, waits),
        place: sessionPlace(s),
        quietWait: waits,
        dispatchedBy: (dispatch.dispatchers.get(s.id) ?? []).map((id) => nameOf.get(id) ?? ""),
        lastEvent: isQuietWait(s.state, waits) ? relativeTime(s.last_at, nowMs) || null : null,
        tasks: runs.map((r) => ({
          runId: r.run_id,
          taskId: r.task_id,
          title: r.title,
          number: r.task_number,
          word: runStateWord(r),
          chip: runStateChip(r),
          children: childrenOf(map, r.run_id).map((c) => ({
            id: c.child_id,
            name: c.name,
            word: childStateWord(c),
            chip: childStateChip(c),
            model: c.model,
            age: childAgeWord(c.age, nowMs),
          })),
        })),
        quiet: hidden > 0 ? quietLine(hidden, s.quiet_last_at, nowMs) : null,
      };
    }),
  }));
}
