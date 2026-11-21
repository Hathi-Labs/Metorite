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
 */
import { agentStateChip, agentStateVisual } from "@/lib/statusAccent";
import { relativeTime } from "@/lib/taskCard";

import { outcomeWord } from "./agentsAtWork";

export type MapSessionState = "waiting_on_person" | "waiting" | "working" | "quiet";

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
}

export interface MapLine {
  from: string;
  to: string;
}

export interface AgentMap {
  people: MapPerson[];
  sessions: MapSession[];
  runs: MapRun[];
  lines: MapLine[];
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

export const MAP_LABEL = "Map";
export const LIST_LABEL = "List";
export const SWITCH_LABEL = "Agents view";
export const CANVAS_LABEL = "Map of people, sessions and tasks";
export const OUTLINE_LABEL = "Outline of people, sessions and tasks";
export const MAP_ERROR_COPY = "The map did not load. Check your connection, then open it again.";
export const MAP_EMPTY_COPY = "No agent session is open now.";
export const MAP_TRUNCATED_COPY = "The map shows the first sessions and runs only.";
export const SHOW_QUIET_LABEL = "Show quiet runs";
export const OPEN_TASK_LABEL = "Open task";

const SESSION_STATES: readonly MapSessionState[] = ["waiting_on_person", "waiting", "working", "quiet"];

/** Waiting sessions come first, then working ones, then quiet ones. */
const STATE_RANK: Record<MapSessionState, number> = {
  waiting_on_person: 0,
  waiting: 1,
  working: 2,
  quiet: 3,
};

/** Each state of a session maps to a state of the one agent vocabulary. */
const STATE_VOCABULARY: Record<MapSessionState, string> = {
  waiting_on_person: "awaiting",
  waiting: "waiting",
  working: "working",
  quiet: "stale",
};

const STATE_WORD: Record<MapSessionState, string> = {
  waiting_on_person: "Waiting for a person",
  waiting: "Waiting for an agent",
  working: "Working",
  quiet: "Quiet",
};

export function sessionStateWord(state: MapSessionState): string {
  return STATE_WORD[state];
}

export function sessionStateChip(state: MapSessionState): string {
  return agentStateChip(STATE_VOCABULARY[state]);
}

export function sessionStateIcon(state: MapSessionState): string {
  return agentStateVisual(STATE_VOCABULARY[state])?.icon ?? "Bot";
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

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function whole(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.floor(value) : 0;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
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
    count: whole(body.count),
    truncated: body.truncated === true,
    viewer: text(body.viewer),
  };
}

/* ── The layout ─────────────────────────────────────────────────────────── */

export type MapNodeKind = "person" | "session" | "task" | "quiet";

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
  /** The session id, the task id or the person, by kind. */
  ref: string;
  /** The task id of a task node. */
  taskId?: string;
  /** The session a node belongs to. */
  sessionId?: string;
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

export const COLUMN_X = { person: 0, session: 300, task: 620 } as const;
export const NODE_W = { person: 220, session: 250, task: 300, quiet: 300 } as const;
export const NODE_H = { person: 48, session: 72, task: 52, quiet: 32 } as const;
const GAP = 12;
const BLOCK_GAP = 24;

function compareText(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

/** Waiting first, then working, then quiet. Ties by name, then by id. */
export function sortSessions(sessions: readonly MapSession[]): MapSession[] {
  return [...sessions].sort(
    (a, b) =>
      STATE_RANK[a.state] - STATE_RANK[b.state] ||
      compareText(a.name.toLowerCase(), b.name.toLowerCase()) ||
      compareText(a.id, b.id),
  );
}

function runsOf(map: AgentMap, sessionId: string): MapRun[] {
  return map.runs
    .filter((r) => r.session_id === sessionId)
    .sort((a, b) => Number(a.quiet) - Number(b.quiet) || compareText(a.run_id, b.run_id));
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

function groupByPerson(map: AgentMap): Array<[string, MapSession[]]> {
  const groups = new Map<string, MapSession[]>();
  for (const s of map.sessions) {
    const list = groups.get(s.person) ?? [];
    list.push(s);
    groups.set(s.person, list);
  }
  return [...groups.entries()]
    .map(([person, list]): [string, MapSession[]] => [person, sortSessions(list)])
    .sort((a, b) => STATE_RANK[a[1][0].state] - STATE_RANK[b[1][0].state] || compareText(a[0], b[0]));
}

/**
 * Place the nodes. Three columns from left to right: person, session, task.
 * The same input gives the same output. Each block of one session takes the
 * height of its tasks, so no two nodes overlap. No layout library.
 */
export function layoutAgentMap(map: AgentMap, nowMs: number = Date.now()): MapLayout {
  const nodes: MapNode[] = [];
  const edges: MapEdge[] = [];
  const sessionIds = new Set<string>();
  let y = 0;

  for (const [person, sessions] of groupByPerson(map)) {
    const personId = `p:${person}`;
    nodes.push({
      id: personId,
      kind: "person",
      x: COLUMN_X.person,
      y,
      w: NODE_W.person,
      h: NODE_H.person,
      label: person,
      detail: sessions.length === 1 ? "1 session" : `${sessions.length} sessions`,
      state: null,
      ref: person,
    });
    for (const session of sessions) {
      const sid = `s:${session.id}`;
      sessionIds.add(sid);
      const runs = runsOf(map, session.id);
      const hidden = hiddenQuiet(session, runs);
      let ty = y;
      for (const run of runs) {
        const tid = `t:${run.run_id}`;
        nodes.push({
          id: tid,
          kind: "task",
          x: COLUMN_X.task,
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
          x: COLUMN_X.task,
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
        x: COLUMN_X.session,
        y,
        w: NODE_W.session,
        h: NODE_H.session,
        label: session.name,
        detail: session.client,
        state: session.state,
        ref: session.id,
        sessionId: session.id,
      });
      edges.push({ id: `${personId}>${sid}`, from: personId, to: sid, kind: "tree" });
      y += Math.max(NODE_H.session, ty - y - GAP) + BLOCK_GAP;
    }
  }

  // The lines of SW-107: only a pair of sessions that this map draws.
  for (const line of map.lines) {
    const from = `s:${line.from}`;
    const to = `s:${line.to}`;
    if (from !== to && sessionIds.has(from) && sessionIds.has(to)) {
      edges.push({ id: `${from}~${to}`, from, to, kind: "line" });
    }
  }
  return { nodes, edges };
}

/* ── The outline for a phone ────────────────────────────────────────────── */

export interface OutlineTask {
  runId: string;
  taskId: string;
  title: string;
  number: number | null;
  word: string;
  chip: string;
}

export interface OutlineSession {
  id: string;
  name: string;
  client: string;
  state: MapSessionState;
  word: string;
  chip: string;
  tasks: OutlineTask[];
  quiet: string | null;
}

export interface OutlinePerson {
  person: string;
  sessions: OutlineSession[];
}

/** The same order as the canvas: person > sessions > tasks. */
export function agentMapOutline(map: AgentMap, nowMs: number = Date.now()): OutlinePerson[] {
  return groupByPerson(map).map(([person, sessions]) => ({
    person,
    sessions: sessions.map((s) => {
      const runs = runsOf(map, s.id);
      const hidden = hiddenQuiet(s, runs);
      return {
        id: s.id,
        name: s.name,
        client: s.client,
        state: s.state,
        word: sessionStateWord(s.state),
        chip: sessionStateChip(s.state),
        tasks: runs.map((r) => ({
          runId: r.run_id,
          taskId: r.task_id,
          title: r.title,
          number: r.task_number,
          word: runStateWord(r),
          chip: runStateChip(r),
        })),
        quiet: hidden > 0 ? quietLine(hidden, s.quiet_last_at, nowMs) : null,
      };
    }),
  }));
}
