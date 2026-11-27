/**
 * The Agents view · one model for the map and the phone view
 * (WS-MCP AMR-1 to AMR-3, `agent_coordination.md` §5.12).
 *
 * It turns the checked map read of `agentMap.ts` into people, orchestrator
 * groups and sessions. It draws nothing and it reads nothing.
 *
 * ⚠️ It adds no fact. A state, a wait, a dispatch line and a machine all come
 * from the map read. A question text comes from the questions read, joined by
 * run id. A session with no structured wait draws no "waits on" edge.
 *
 * ⚠️ One vocabulary. A state is a key of `AGENT_STATES` (`statusAccent.ts`),
 * its words are those of the board lanes (`AGENT_LANES`), and its card edge is
 * `agentCardLook`. Every name is untrusted text: a view draws it as text.
 */
import { AGENT_DONE_KEY, agentMarkFill, agentStateChip, agentStateVisual } from "@/lib/statusAccent";

import { type AgentEdge, agentCardLook } from "./agentCardLook";
import {
  type AgentMap,
  type MapChild,
  type MapRun,
  type MapSession,
  type MapTaskWords,
  childrenOf,
  dispatchGraph,
  sortSessions,
} from "./agentMap";
import { NOT_REPORTED } from "./agentPlace";
import { AGENT_ENDED_WORDS, AGENT_LANES, type AgentWaitingOn, agentPillText, waitingText } from "./agentRuns";

export type LensState = "you" | "failed" | "working" | "waiting" | "idle" | "done";

/** The order of the chips, the cards and the squares: what needs a person comes first. */
export const LENS_STATES: readonly LensState[] = ["you", "failed", "working", "waiting", "idle", "done"];

/** Each lens state is one state of the one agent vocabulary. */
const VOCABULARY: Record<LensState, string> = {
  you: "awaiting",
  failed: "failed",
  working: "working",
  waiting: "waiting",
  idle: "stale",
  done: AGENT_DONE_KEY,
};

/** A completed run reads and draws as Done here. */
const vocabularyKey = (key: string): string => (key === "completed" ? AGENT_DONE_KEY : key);

function vocabularyWord(key: string): string {
  const ended = key === AGENT_DONE_KEY ? "completed" : key;
  return AGENT_LANES.find((lane) => lane.key === ended)?.label ?? AGENT_ENDED_WORDS[ended] ?? ended;
}

export function lensWord(state: LensState): string {
  return vocabularyWord(VOCABULARY[state]);
}

/** The fill of the square of a state, a token of `AGENT_MARKS`. */
export function lensMark(state: LensState): string {
  return agentMarkFill(VOCABULARY[state]);
}

export function lensIcon(state: LensState): string {
  return agentStateVisual(VOCABULARY[state])?.icon ?? "Bot";
}

export function lensChip(state: LensState): string {
  return agentStateChip(VOCABULARY[state]);
}

export function lensEdge(state: LensState): AgentEdge | null {
  return agentCardLook(VOCABULARY[state]).edge;
}

/**
 * The word and the chip of one run, in the same words as a session. A quiet run
 * reads Idle. `askedOf` is the line of a session whose question is another
 * member's: its asking run reads that line, never "Needs you" (EA3-50).
 */
export function lensRun(
  run: Pick<MapRun, "state" | "quiet">,
  askedOf: string | null = null,
): { word: string; chip: string } {
  if (askedOf !== null && !run.quiet && run.state === "awaiting") {
    return { word: askedOf, chip: agentStateChip("waiting") };
  }
  const key = vocabularyKey(run.quiet ? "stale" : run.state);
  return { word: vocabularyWord(key), chip: agentStateChip(key) };
}

/** The lens state of a sub-agent. A stopped or released one reads Idle: it does nothing now. */
export function lensOfChild(state: string): LensState {
  if (state === "failed") return "failed";
  if (state === "completed") return "done";
  if (state === "waiting") return "waiting";
  return state === "working" || state === "queued" || state === "answered" ? "working" : "idle";
}

/** One sub-agent, as the graph, the dock and the phone sheet draw it. */
export interface SubRow {
  id: string;
  name: string;
  state: LensState;
  model: string | null;
  /** Seconds since it started. */
  age: number | null;
}

/** The sub-agents of a read: the most urgent first, then the newest. */
export function subRows(children: readonly MapChild[]): SubRow[] {
  return children
    .map((c) => ({ id: c.child_id, name: c.name, state: lensOfChild(c.state), model: c.model, age: c.age }))
    .sort(
      (a, b) =>
        LENS_STATES.indexOf(a.state) - LENS_STATES.indexOf(b.state) ||
        (a.age ?? Infinity) - (b.age ?? Infinity) ||
        a.id.localeCompare(b.id),
    );
}

export interface SubCounts {
  total: number;
  working: number;
  failed: number;
  done: number;
}

/** "7 subagents · 2 working · 1 failed", or "" with none. The parts, so a view colours "failed". */
export function subParts(subs: SubCounts): { total: string; working: string; failed: string } {
  return {
    total: subs.total === 0 ? "" : `${subs.total} ${subs.total === 1 ? "subagent" : "subagents"}`,
    working: subs.working > 0 ? `${subs.working} working` : "",
    failed: subs.failed > 0 ? `${subs.failed} failed` : "",
  };
}

export function subWords(subs: SubCounts): string {
  const parts = subParts(subs);
  return [parts.total, parts.working, parts.failed].filter(Boolean).join(" · ");
}

/** What a waiting session waits on, from the structured field of a run. */
export interface LensWait {
  on: AgentWaitingOn;
  until: string | null;
}

export interface LensSession {
  id: string;
  person: string;
  name: string;
  state: LensState;
  lastAt: string | null;
  client: string;
  host: MapSession["host"];
  hostKind: MapSession["host_kind"];
  hostSource: MapSession["host_source"];
  model: string | null;
  runCount: number;
  quietCount: number;
  /** The listed runs: the live ones first. */
  runs: MapRun[];
  /** The open sub-agents of those runs (SW-110). Empty on the summary read. */
  children: MapChild[];
  /** AMR-4: how many sub-agents it holds, by state. */
  subs: SubCounts;
  /** AMR-4: the sub-agent names, so the find box reaches them. */
  subNames: string[];
  /** AMR-4: the tasks that the summary names. The map of SW-102 sends none: its runs hold them. */
  tasks: MapTaskWords[];
  wait: LensWait | null;
  /** "Waiting for Ben": a run of ANOTHER member waits for that member's answer. */
  askedOf: string | null;
  /** The question of a run that waits on a person, when the viewer may read it. */
  question: string | null;
  /** The ids of the sessions that dispatched this one. */
  dispatchers: string[];
  /** The ids of the sessions that this one dispatched. */
  dispatched: string[];
}

export interface LensGroup {
  /** Stable: the person, and the orchestrator id or "own". */
  key: string;
  /** The orchestrator of the group, or `null` for sessions with none on this person. */
  orch: LensSession | null;
  /** The sessions under it, sorted. Never the orchestrator itself. */
  sessions: LensSession[];
}

export interface LensPerson {
  person: string;
  groups: LensGroup[];
}

export interface LensModel {
  people: LensPerson[];
  sessions: LensSession[];
  byId: Map<string, LensSession>;
}

const STATE_OF_MAP = {
  waiting_on_person: "you",
  waiting: "waiting",
  working: "working",
  quiet: "idle",
  failed: "failed",
  done: "done",
} as const;

const RANK = Object.fromEntries(LENS_STATES.map((s, i) => [s, i])) as Record<LensState, number>;

/** The sub-agents of a session: the counts of the summary, else a count of its listed children. */
function subsOf(s: MapSession, children: readonly MapChild[]): { subs: SubCounts; subNames: string[] } {
  if (s.subagents) {
    const { names, ...subs } = s.subagents;
    return { subs, subNames: names };
  }
  const states = children.map((c) => lensOfChild(c.state));
  return {
    subs: {
      total: children.length,
      working: states.filter((x) => x === "working").length,
      failed: states.filter((x) => x === "failed").length,
      done: states.filter((x) => x === "done").length,
    },
    subNames: [...new Set(children.map((c) => c.name))],
  };
}

export function compareSessions(a: LensSession, b: LensSession): number {
  return (
    RANK[a.state] - RANK[b.state] ||
    a.name.toLowerCase().localeCompare(b.name.toLowerCase()) ||
    a.id.localeCompare(b.id)
  );
}

function waitOf(runs: readonly MapRun[]): LensWait | null {
  const run = runs.find((r) => r.state === "waiting" && r.waiting_on !== null);
  return run?.waiting_on ? { on: run.waiting_on, until: run.waiting_until } : null;
}

/** Who looks at the view. `labels` resolves an address to a name. */
export interface LensViewer {
  email?: string | null;
  labels?: ReadonlyMap<string, string>;
}

/**
 * Build the model. `questions` maps a run id to the text of its open question.
 *
 * ⚠️ EA3-50: "Needs you" reads to the member of the session only. A session of
 * another member that waits for an answer is `waiting` on a person, and its
 * line names that member. With no viewer, no session needs "you".
 *
 * An orchestrator is a session with no dispatcher on the map that dispatched at
 * least one session. Its group holds each session of ITS OWN person below it in
 * the dispatch chain. A session of another person stays under that person, and
 * its dispatch line still joins the two.
 */
export function buildLensModel(
  map: AgentMap,
  questions: ReadonlyMap<string, string> = new Map(),
  viewer: LensViewer = {},
): LensModel {
  const me = (viewer.email ?? "").trim().toLowerCase();
  const dispatch = dispatchGraph(map);
  const sessions: LensSession[] = sortSessions(map.sessions, dispatch.waiting).map((s) => {
    const runs = map.runs
      .filter((r) => r.session_id === s.id)
      .sort((a, b) => Number(a.quiet) - Number(b.quiet) || (a.age ?? 0) - (b.age ?? 0) || a.run_id.localeCompare(b.run_id));
    // SW-109: a quiet orchestrator with a live lane waits. It is never "lost".
    const mapped: LensState = dispatch.waiting.has(s.id) ? "waiting" : STATE_OF_MAP[s.state];
    const others = mapped === "you" && (!me || s.person.trim().toLowerCase() !== me);
    const state: LensState = others ? "waiting" : mapped;
    const asked = runs.find((r) => r.state === "awaiting" && questions.has(r.run_id));
    const children = runs.flatMap((r) => childrenOf(map, r.run_id));
    return {
      id: s.id,
      person: s.person,
      name: s.name,
      state,
      lastAt: s.last_at,
      client: s.client,
      host: s.host,
      hostKind: s.host_kind,
      hostSource: s.host_source,
      model: s.model,
      runCount: s.run_count,
      quietCount: s.quiet_count,
      runs,
      children,
      ...subsOf(s, children),
      tasks: s.tasks ?? [],
      // The awaiting state is the fact that names the wait of another member.
      wait: others ? { on: "member", until: null } : state === "waiting" ? waitOf(runs) : null,
      askedOf: others
        ? agentPillText("awaiting", { memberEmail: s.person, viewerEmail: me, labels: viewer.labels }).text
        : null,
      question: state === "you" && asked ? (questions.get(asked.run_id) ?? null) : null,
      dispatchers: dispatch.dispatchers.get(s.id) ?? [],
      dispatched: dispatch.dispatched.get(s.id) ?? [],
    };
  });
  const byId = new Map(sessions.map((s) => [s.id, s]));

  /** The top of the chain of a session. The lines hold no loop, so this ends. */
  const rootOf = (s: LensSession): LensSession => {
    let at = s;
    const seen = new Set<string>();
    while (at.dispatchers.length > 0 && !seen.has(at.id)) {
      seen.add(at.id);
      at = byId.get(at.dispatchers[0]) ?? at;
    }
    return at;
  };
  const isOrch = (s: LensSession): boolean => s.dispatchers.length === 0 && s.dispatched.length > 0;

  const people = new Map<string, Map<string, LensGroup>>();
  const groupOf = (person: string, orch: LensSession | null): LensGroup => {
    const groups = people.get(person) ?? new Map<string, LensGroup>();
    people.set(person, groups);
    const key = `${person}|${orch?.id ?? "own"}`;
    const held = groups.get(key) ?? { key, orch, sessions: [] };
    groups.set(key, held);
    return held;
  };
  for (const s of sessions) {
    if (isOrch(s)) {
      groupOf(s.person, s);
      continue;
    }
    const root = rootOf(s);
    groupOf(s.person, isOrch(root) && root.person === s.person ? root : null).sessions.push(s);
  }

  const urgency = (list: LensSession[]): number => Math.min(...list.map((s) => RANK[s.state]));
  const out: LensPerson[] = [...people.entries()].map(([person, groups]) => ({
    person,
    groups: [...groups.values()]
      .map((g) => ({ ...g, sessions: [...g.sessions].sort(compareSessions) }))
      // Orchestrators first, by urgency. The sessions with no orchestrator come last.
      .sort(
        (a, b) =>
          Number(a.orch === null) - Number(b.orch === null) ||
          urgency(membersOf(a)) - urgency(membersOf(b)) ||
          a.key.localeCompare(b.key),
      ),
  }));
  out.sort(
    (a, b) =>
      urgency(a.groups.flatMap(membersOf)) - urgency(b.groups.flatMap(membersOf)) ||
      a.person.localeCompare(b.person),
  );
  return { people: out, sessions, byId };
}

/** The orchestrator of a group, then its sessions. */
export function membersOf(group: LensGroup): LensSession[] {
  return group.orch ? [group.orch, ...group.sessions] : group.sessions;
}

export function personSessions(person: LensPerson): LensSession[] {
  return person.groups.flatMap(membersOf);
}

export type LensCounts = Record<LensState, number>;

export function countStates(sessions: readonly LensSession[]): LensCounts {
  const counts: LensCounts = { you: 0, failed: 0, working: 0, waiting: 0, idle: 0, done: 0 };
  for (const s of sessions) counts[s.state] += 1;
  return counts;
}

/* ── The switch and the find box ────────────────────────────────────────── */

/** The states of the Active switch. Idle and Done fold (`agent_coordination.md` §3.12.2). */
export const DEFAULT_STATES: readonly LensState[] = ["you", "failed", "working", "waiting"];

/** A task as the find box reads it: its title and its number. */
const taskText = (number: number | null, title: string): string[] => [title, number === null ? "" : `#${number}`];

export function findText(s: LensSession, personName: string): string {
  return [
    s.name,
    s.person,
    personName,
    s.client,
    s.host ?? "",
    s.model ?? "",
    ...s.subNames,
    ...s.runs.flatMap((r) => taskText(r.task_number, r.title)),
    ...s.tasks.flatMap((t) => taskText(t.number, t.title)),
  ]
    .join(" ")
    .toLowerCase();
}

export function matchesFind(s: LensSession, find: string, personName: string): boolean {
  const q = find.trim().toLowerCase();
  return !q || findText(s, personName).includes(q);
}

/** The second line of a card: the question, else the wait, else the task. */
export function sessionLine(s: LensSession, nowMs: number): string {
  if (s.question) return s.question;
  if (s.askedOf) return s.askedOf;
  if (s.wait) {
    const text = waitingText(s.wait, nowMs);
    return text.charAt(0).toUpperCase() + text.slice(1);
  }
  const run = s.runs[0];
  if (!run) {
    if (s.quietCount === 0) return "No open task";
    return s.quietCount === 1 ? "1 quiet run" : `${s.quietCount} quiet runs`;
  }
  return run.task_number === null ? run.title : `#${run.task_number} ${run.title}`;
}

export function machineWord(s: Pick<LensSession, "host">): string {
  return s.host ?? NOT_REPORTED;
}

/** "3 need you", "2 working": the counts of a person or an orchestrator, in words. */
export function countWords(counts: LensCounts): Array<{ state: LensState; text: string }> {
  const words: Record<LensState, string> = {
    you: "need you",
    failed: "failed",
    working: "working",
    waiting: "waiting",
    idle: "idle",
    done: "done",
  };
  return LENS_STATES.filter((s) => counts[s] > 0).map((s) => ({
    state: s,
    text: s === "you" && counts[s] === 1 ? "1 needs you" : `${counts[s]} ${words[s]}`,
  }));
}

/** How many sessions of a list are not idle and not done. */
export function activeCount(sessions: readonly LensSession[]): number {
  return sessions.filter((s) => s.state !== "idle" && s.state !== "done").length;
}
