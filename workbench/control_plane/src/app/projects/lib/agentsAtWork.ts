/**
 * Agents at work (WS-AGA AGA-8, `project-docs/specs/agents_view.md` §6).
 *
 * The words and the rules of the section. The gateway reads the agents
 * (`GET /projects/agents/overview`, AGA-7). This file checks the shape of
 * the answer, drops each field it does not know, and names each state in
 * plain words. It draws nothing.
 *
 * ⚠️ One colour vocabulary. A state becomes a chip through
 * `agentStateChip` of `lib/statusAccent.ts`, and through nothing else.
 * A test fails if this file holds a colour of its own.
 */
import { agentStateChip, agentStateVisual } from "@/lib/statusAccent";
import { relativeTime } from "@/lib/taskCard";

export type AgentAtWorkState = "working" | "idle" | "waiting_on_person";
export type AgentSource = "built_in" | "org" | "external";

export interface AgentLastRun {
  at: string | null;
  state: string;
}

export interface AgentLastError {
  at: string | null;
  what: string;
  next_step: string;
}

export interface AgentAtWork {
  key: string;
  name: string;
  job: string;
  source: AgentSource;
  /** "shared by Metorite" for a built-in agent, else null. */
  label: string | null;
  state: AgentAtWorkState;
  runs: number;
  last_run: AgentLastRun | null;
  last_error: AgentLastError | null;
}

export interface AgentsOverview {
  agents: AgentAtWork[];
  count: number;
  truncated: boolean;
}

/** What the section shows. `dark` draws nothing: the feature is off. */
export type AgentsAtWorkRead =
  | { kind: "loading" }
  | { kind: "dark" }
  | { kind: "error" }
  | { kind: "ready"; overview: AgentsOverview };

export const SECTION_TITLE = "Agents at work";
export const LENS_LABEL = "Agents";
export const EMPTY_COPY = "No agent has worked in this organization yet.";
export const ERROR_COPY =
  "The agents did not load. Check your connection, then open this section again.";
export const TRUNCATED_COPY = "The list shows the first agents only.";

const SOURCES: readonly AgentSource[] = ["built_in", "org", "external"];
const STATES: readonly AgentAtWorkState[] = ["working", "idle", "waiting_on_person"];

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function lastRun(value: unknown): AgentLastRun | null {
  const raw = record(value);
  if (!raw) return null;
  return { at: text(raw.at) || null, state: text(raw.state) };
}

function lastError(value: unknown): AgentLastError | null {
  const raw = record(value);
  if (!raw) return null;
  const what = text(raw.what);
  if (!what) return null;
  return { at: text(raw.at) || null, what, next_step: text(raw.next_step) };
}

/** One row, or null when it is not an agent row. */
export function parseAgent(value: unknown): AgentAtWork | null {
  const raw = record(value);
  if (!raw) return null;
  const key = text(raw.key);
  const name = text(raw.name);
  const source = text(raw.source) as AgentSource;
  const state = text(raw.state) as AgentAtWorkState;
  if (!key || !name || !SOURCES.includes(source) || !STATES.includes(state)) return null;
  const runs = typeof raw.runs === "number" && Number.isFinite(raw.runs) ? raw.runs : 0;
  return {
    key,
    name,
    job: text(raw.job),
    source,
    label: text(raw.label) || null,
    state,
    runs,
    last_run: lastRun(raw.last_run),
    last_error: lastError(raw.last_error),
  };
}

/**
 * The answer of the gateway as the section reads it, or null when the body is
 * not an overview. A row it cannot read is dropped, and each field it does not
 * know is dropped with it.
 */
export function parseOverview(raw: unknown): AgentsOverview | null {
  const body = record(raw);
  if (!body || !Array.isArray(body.agents)) return null;
  const agents = body.agents.map(parseAgent).filter((a): a is AgentAtWork => a !== null);
  return { agents, count: agents.length, truncated: body.truncated === true };
}

/** Each state of an agent maps to a state of the one agent vocabulary. */
const STATE_VOCABULARY: Record<AgentAtWorkState, string> = {
  waiting_on_person: "awaiting",
  working: "working",
  idle: "stale",
};

const STATE_WORD: Record<AgentAtWorkState, string> = {
  waiting_on_person: "Waiting for a person",
  working: "Working",
  idle: "Idle",
};

export function stateWord(state: AgentAtWorkState): string {
  return STATE_WORD[state];
}

export function stateChip(state: AgentAtWorkState): string {
  return agentStateChip(STATE_VOCABULARY[state]);
}

export function stateIcon(state: AgentAtWorkState): string {
  return agentStateVisual(STATE_VOCABULARY[state])?.icon ?? "Bot";
}

const OUTCOME_WORD: Record<string, string> = {
  failed: "Failed",
  completed: "Finished",
  done: "Finished",
  finished: "Finished",
  released: "Closed",
  awaiting: "Waiting for a person",
  answered: "Answered",
  working: "Working",
  waiting: "Waiting",
  queued: "Queued",
  stale: "Quiet",
};

/** The outcome of the last run, in a word. */
export function outcomeWord(state: string): string {
  const key = state.trim().toLowerCase();
  if (!key) return "No outcome";
  if (OUTCOME_WORD[key]) return OUTCOME_WORD[key];
  const plain = key.replace(/_/g, " ");
  return plain.charAt(0).toUpperCase() + plain.slice(1);
}

/** The chip of the last outcome. An unknown outcome is gray. */
export function outcomeChip(state: string): string {
  return agentStateChip(state);
}

export function outcomeIcon(state: string): string | null {
  return agentStateVisual(state)?.icon ?? null;
}

/** "Failed, 3h ago", or "Has not run yet". */
export function lastRunLine(run: AgentLastRun | null, nowMs = Date.now()): string {
  if (!run || (!run.at && !run.state)) return "Has not run yet";
  const when = run.at ? relativeTime(run.at, nowMs) : "";
  const outcome = outcomeWord(run.state);
  return when ? `${outcome}, ${when}` : outcome;
}

/** "3 runs", "1 run". */
export function runsWord(runs: number): string {
  return `${runs} ${runs === 1 ? "run" : "runs"}`;
}

// ── AGA-9: the drill-down of one agent (`GET /projects/agents/{key}/runs`) ──

export interface AgentRunError {
  at: string | null;
  what: string;
  next_step: string;
}

export interface AgentRunLine {
  id: string;
  /** The run state in the words of the task runs. */
  state: string;
  started_at: string | null;
  ended_at: string | null;
  /** Null while the run is open. */
  run_seconds: number | null;
  task: { id: string; title: string } | null;
  error: AgentRunError | null;
}

export interface AgentRuns {
  key: string;
  runs: AgentRunLine[];
  count: number;
  truncated: boolean;
}

/** What the drill-down shows for one agent. */
export type AgentRunsRead =
  | { kind: "loading" }
  | { kind: "unavailable" }
  | { kind: "error" }
  | { kind: "ready"; runs: AgentRuns };

export const RUNS_TITLE = "Recent runs";
export const RUNS_EMPTY_COPY = "This agent has no run that you may see.";
export const RUNS_ERROR_COPY = "The runs did not load. Close this list, then open it again.";
export const RUNS_UNAVAILABLE_COPY = "You may not see the runs of this agent.";
export const RUNS_TRUNCATED_COPY = "The list shows the last 40 runs only.";

/** One run, or null when it is not a run row. The trace is never a field here. */
export function parseRunLine(value: unknown): AgentRunLine | null {
  const raw = record(value);
  if (!raw) return null;
  const id = text(raw.id);
  const state = text(raw.state);
  if (!id || !state) return null;
  const task = record(raw.task);
  const err = lastError(raw.error);
  const seconds =
    typeof raw.run_seconds === "number" && Number.isFinite(raw.run_seconds) ? raw.run_seconds : null;
  return {
    id,
    state,
    started_at: text(raw.started_at) || null,
    ended_at: text(raw.ended_at) || null,
    run_seconds: seconds,
    task: task && text(task.id) ? { id: text(task.id), title: text(task.title) || "Untitled task" } : null,
    error: err,
  };
}

/** The answer of the drill-down, or null when the body is not one. */
export function parseAgentRuns(raw: unknown): AgentRuns | null {
  const body = record(raw);
  if (!body || !Array.isArray(body.runs)) return null;
  const runs = body.runs.map(parseRunLine).filter((r): r is AgentRunLine => r !== null);
  return { key: text(body.key), runs, count: runs.length, truncated: body.truncated === true };
}

/** "45 s", "3 min 20 s", "2 h 5 min", or "Running" while the run is open. */
export function runTimeWord(seconds: number | null): string {
  if (seconds === null) return "Running";
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `${m} min ${s % 60} s` : `${m} min`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
}
