/**
 * The Agents view · one model for the Graph, Board, List and phone views
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
import { agentStateChip, agentStateVisual } from "@/lib/statusAccent";

import { type AgentEdge, agentCardLook } from "./agentCardLook";
import {
  type AgentMap,
  type MapChild,
  type MapRun,
  type MapSession,
  childrenOf,
  dispatchGraph,
  sortSessions,
} from "./agentMap";
import { NOT_REPORTED } from "./agentPlace";
import { AGENT_LANES, type AgentWaitingOn, waitingText } from "./agentRuns";

export type LensState = "you" | "working" | "waiting" | "idle";

/** The order of the chips and of the cards: what waits on a person comes first. */
export const LENS_STATES: readonly LensState[] = ["you", "working", "waiting", "idle"];

/** Each lens state is one state of the one agent vocabulary. */
const VOCABULARY: Record<LensState, string> = {
  you: "awaiting",
  working: "working",
  waiting: "waiting",
  idle: "stale",
};

export function lensWord(state: LensState): string {
  return AGENT_LANES.find((lane) => lane.key === VOCABULARY[state])?.label ?? VOCABULARY[state];
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

/** The word and the chip of one run, in the same words as a session. A quiet run reads Idle. */
export function lensRun(run: Pick<MapRun, "state" | "quiet">): { word: string; chip: string } {
  const key = run.quiet ? "stale" : run.state;
  return { word: AGENT_LANES.find((lane) => lane.key === key)?.label ?? key, chip: agentStateChip(key) };
}

/** What a waiting session waits on, from the structured field of a run. */
export interface LensWait {
  on: AgentWaitingOn;
  until: string | null;
}

/** The words of a "waits on" node of the graph. */
export const WAIT_NODE_WORD: Record<AgentWaitingOn, string> = {
  ci: "CI",
  review: "A review",
  deploy: "A deploy",
  member: "A person",
  agent: "Another agent",
};

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
  /** The open sub-agents of those runs (SW-110). */
  children: MapChild[];
  wait: LensWait | null;
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
} as const;

const RANK: Record<LensState, number> = { you: 0, working: 1, waiting: 2, idle: 3 };

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

/**
 * Build the model. `questions` maps a run id to the text of its open question.
 *
 * An orchestrator is a session with no dispatcher on the map that dispatched at
 * least one session. Its group holds each session of ITS OWN person below it in
 * the dispatch chain. A session of another person stays under that person, and
 * its dispatch line still joins the two.
 */
export function buildLensModel(map: AgentMap, questions: ReadonlyMap<string, string> = new Map()): LensModel {
  const dispatch = dispatchGraph(map);
  const sessions: LensSession[] = sortSessions(map.sessions, dispatch.waiting).map((s) => {
    const runs = map.runs
      .filter((r) => r.session_id === s.id)
      .sort((a, b) => Number(a.quiet) - Number(b.quiet) || (a.age ?? 0) - (b.age ?? 0) || a.run_id.localeCompare(b.run_id));
    // SW-109: a quiet orchestrator with a live lane waits. It is never "lost".
    const state: LensState = dispatch.waiting.has(s.id) ? "waiting" : STATE_OF_MAP[s.state];
    const asked = runs.find((r) => r.state === "awaiting" && questions.has(r.run_id));
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
      children: runs.flatMap((r) => childrenOf(map, r.run_id)),
      wait: state === "waiting" ? waitOf(runs) : null,
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
  const counts: LensCounts = { you: 0, working: 0, waiting: 0, idle: 0 };
  for (const s of sessions) counts[s.state] += 1;
  return counts;
}

/* ── The filters of the toolbar ─────────────────────────────────────────── */

export interface LensFilters {
  /** The states that show. A state that is off folds into one line. */
  states: ReadonlySet<LensState>;
  /** One person, or "" for everyone. */
  person: string;
  /** Free text, over the session, its tasks, its machine and its model. */
  find: string;
}

/** Idle starts folded (`agent_coordination.md` §3.12.2). */
export const DEFAULT_STATES: readonly LensState[] = ["you", "working", "waiting"];

export function findText(s: LensSession, personName: string): string {
  return [
    s.name,
    s.person,
    personName,
    s.client,
    s.host ?? "",
    s.model ?? "",
    ...s.runs.flatMap((r) => [r.title, r.task_number === null ? "" : `#${r.task_number}`]),
  ]
    .join(" ")
    .toLowerCase();
}

export function matchesFind(s: LensSession, find: string, personName: string): boolean {
  const q = find.trim().toLowerCase();
  return !q || findText(s, personName).includes(q);
}

export interface SplitGroup {
  shown: LensSession[];
  /** Folded into one line, because their state chip is off. */
  folded: LensSession[];
}

/**
 * Which sessions of a group draw, and which fold. A find shows each match, a
 * folded one too, and hides the rest. `opened` unfolds one group.
 */
export function splitSessions(
  sessions: readonly LensSession[],
  filters: LensFilters,
  opened: boolean,
  nameOf: (email: string) => string,
): SplitGroup {
  const q = filters.find.trim();
  if (q) return { shown: sessions.filter((s) => matchesFind(s, q, nameOf(s.person))), folded: [] };
  const shown: LensSession[] = [];
  const folded: LensSession[] = [];
  for (const s of sessions) (opened || filters.states.has(s.state) ? shown : folded).push(s);
  return { shown, folded };
}

/** "3 idle sessions", or "2 hidden sessions" for a fold of other states. */
export function foldWord(folded: readonly LensSession[]): string {
  const noun = folded.length === 1 ? "session" : "sessions";
  const kind = folded.every((s) => s.state === "idle") ? "idle" : "hidden";
  return `${folded.length} ${kind} ${noun}`;
}

/** The second line of a card: the question, else the wait, else the task. */
export function sessionLine(s: LensSession, nowMs: number): string {
  if (s.question) return s.question;
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
  const words: Record<LensState, string> = { you: "need you", working: "working", waiting: "waiting", idle: "idle" };
  return LENS_STATES.filter((s) => counts[s] > 0).map((s) => ({
    state: s,
    text: s === "you" && counts[s] === 1 ? "1 needs you" : `${counts[s]} ${words[s]}`,
  }));
}

/** How many sessions of a list are not idle. */
export function activeCount(sessions: readonly LensSession[]): number {
  return sessions.filter((s) => s.state !== "idle").length;
}
