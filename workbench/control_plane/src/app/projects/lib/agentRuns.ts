/**
 * Agent runs on a task: the client half of EA-12c (`external_agent_access.md`
 * §12.1, D-EA-10).
 *
 * An outside agent works on a task inside a RUN. The run holds typed
 * ACTIVITIES. An `elicitation` asks the member a question, and the run waits
 * for a `prompt` that answers it. This file holds the wire types, the pure
 * rules the pill and the answer card read, and the three calls.
 *
 * ⚠️ **The wire types are in THIS file only, on purpose.** EA-12c was built
 * before the EA-12a routes merged, against the shapes in the spec. One file
 * keeps the reconcile to one diff. Each guess about a shape is marked
 * "ASSUMED" below, and `normalizeRunDetail` reads both of the forms that
 * the detail route can plausibly take.
 *
 * The calls go through `projectsCall`, the one request seam for
 * `/api/projects/*`. The BFF proxy relays any path under it, so the routes
 * need no proxy change. A POST through it drops the cached Projects reads,
 * so the board re-reads the task and its pill.
 *
 * Fence: `agentRuns.test.ts`.
 */

import type { ElicitationAnswers, ElicitationQuestion } from "@/lib/elicitationAnswer";

import { ProjectsApiError, projectsCall } from "./api";

/* ── The wire types ──────────────────────────────────────────────────────── */

/** The derived state of the newest open run, on a task row (EA12-10). */
export type AgentState =
  | "queued"
  | "working"
  | "awaiting"
  | "answered"
  | "stopping"
  | "stale"
  | "failed";

export const AGENT_STATE_VALUES: readonly AgentState[] = [
  "queued",
  "working",
  "awaiting",
  "answered",
  "stopping",
  "stale",
  "failed",
];

/** The eight run statuses, as Plane names them. */
export type AgentRunStatus =
  | "created"
  | "in_progress"
  | "awaiting"
  | "completed"
  | "stopping"
  | "stopped"
  | "failed"
  | "stale";

/** A run is open while it can still take an agent write (EA12-6). */
export const OPEN_RUN_STATUSES: readonly AgentRunStatus[] = [
  "created",
  "in_progress",
  "awaiting",
  "stopping",
];

export type AgentActivityType =
  | "prompt"
  | "thought"
  | "action"
  | "response"
  | "elicitation"
  | "error";

export type AgentSignal = "continue" | "stop" | "select";

/** One option of a `select`. At most one is `recommended`. */
export interface AgentOption {
  id: string;
  label: string;
  description?: string | null;
  recommended?: boolean;
}

export interface AgentActivity {
  id: string;
  /** The cursor. Reads order by it (EA12-2). */
  seq: number;
  type: AgentActivityType;
  content: {
    body?: string | null;
    /** A prompt that answers a `select`: the picked option ids. */
    selected?: string[] | null;
    /** An `action`: the tool name. */
    action?: string | null;
    parameters?: Record<string, string> | null;
  };
  signal?: AgentSignal | null;
  signal_metadata?: {
    options?: AgentOption[];
    allow_multiple?: boolean;
  } | null;
  /** The member email. */
  actor?: string | null;
  /** `mcp:<slug>` for an agent, null for a person. */
  actor_via?: string | null;
  /** The elicitation that a prompt answers. The server sets it. */
  in_reply_to?: string | null;
  created_at?: string | null;
}

export interface AgentRun {
  id: string;
  task_id: string;
  /** The member the agent acts as. */
  member_email: string;
  /** `mcp:<slug>`. */
  client_via: string;
  status: AgentRunStatus;
  /** The derived state. Null on a closed run. */
  state?: AgentState | null;
  started_at?: string | null;
  ended_at?: string | null;
  external_link?: string | null;
  last_activity_at?: string | null;
  /**
   * The open elicitation, if any. ASSUMED key name: the spec says "the open
   * elicitation" and names no key, so both spellings are read.
   */
  open_elicitation?: AgentActivity | null;
  elicitation?: AgentActivity | null;
}

/** What `GET /projects/agent-runs/{run_id}` gives, after `normalizeRunDetail`. */
export interface AgentRunDetail {
  run: AgentRun;
  activities: AgentActivity[];
}

/** The body of `POST /projects/agent-runs/{run_id}/activities` from a member. */
export interface AgentPromptBody {
  type: "prompt";
  body?: string;
  selected?: string[];
  signal?: "stop";
  idempotency_key: string;
}

/* ── The calls ───────────────────────────────────────────────────────────── */

/**
 * Read the detail route in either shape. ASSUMED: the spec says "the run
 * and its activities", which can be `{run, activities}` or the run's own
 * fields with `activities` beside them.
 */
export function normalizeRunDetail(raw: unknown): AgentRunDetail {
  const body = (raw ?? {}) as Record<string, unknown>;
  const activities = Array.isArray(body.activities)
    ? (body.activities as AgentActivity[])
    : [];
  const run = (body.run && typeof body.run === "object" ? body.run : body) as AgentRun;
  return { run, activities };
}

/** The runs list, in either `{rows}` or bare-array form. */
function rowsOf<T>(raw: unknown): T[] {
  if (Array.isArray(raw)) return raw as T[];
  const rows = (raw as { rows?: unknown } | null)?.rows;
  return Array.isArray(rows) ? (rows as T[]) : [];
}

export const agentRunsApi = {
  /**
   * The runs of a task, newest first. `null` means the routes are OFF: the
   * flag `agent_runs_enabled` makes every route answer 404, and then the
   * task detail draws nothing at all.
   */
  async list(taskId: string): Promise<AgentRun[] | null> {
    try {
      const raw = await projectsCall<unknown>(
        `tasks/${encodeURIComponent(taskId)}/agent-runs`,
      );
      return rowsOf<AgentRun>(raw);
    } catch (err) {
      if (err instanceof ProjectsApiError && err.status === 404) return null;
      throw err;
    }
  },

  /** One run, with the activities after the cursor `after` (a `seq`). */
  async get(runId: string, after?: number): Promise<AgentRunDetail> {
    const query = after !== undefined ? `?after=${after}` : "";
    const raw = await projectsCall<unknown>(
      `agent-runs/${encodeURIComponent(runId)}${query}`,
    );
    return normalizeRunDetail(raw);
  },

  /** A member writes one `prompt`: an answer, a reply or a Stop. */
  post(runId: string, body: AgentPromptBody): Promise<unknown> {
    return projectsCall<unknown>(
      `agent-runs/${encodeURIComponent(runId)}/activities`,
      { method: "POST", body: JSON.stringify(body) },
    );
  },

  /**
   * Are the routes on? One read of the caller's own runs. A 404 means the
   * flag is off. The "Waiting on me" filter shows only while this is true.
   */
  async enabled(): Promise<boolean> {
    try {
      await projectsCall<unknown>("agent-runs?status=awaiting");
      return true;
    } catch {
      return false;
    }
  },
};

/** A fresh idempotency key for one write. */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

/* ── Names ───────────────────────────────────────────────────────────────── */

/**
 * Names that title case gets wrong. Every other slug draws in title case,
 * so `mcp:claude-code` reads "Claude Code".
 */
const CLIENT_NAMES: Record<string, string> = {
  chatgpt: "ChatGPT",
  "claude-ai": "Claude",
  vscode: "VS Code",
  "vs-code": "VS Code",
  "github-copilot": "GitHub Copilot",
  openai: "OpenAI",
};

/** `mcp:claude-code` → "Claude Code". An empty value reads "Agent". */
export function clientName(via?: string | null): string {
  const raw = (via ?? "").trim();
  const slug = (raw.startsWith("mcp:") ? raw.slice(4) : raw).toLowerCase();
  if (!slug) return "Agent";
  if (CLIENT_NAMES[slug]) return CLIENT_NAMES[slug];
  return slug
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** A member's display name: the directory label, else the address's local part. */
export function personName(
  email?: string | null,
  labels?: ReadonlyMap<string, string>,
): string {
  const raw = (email ?? "").trim();
  if (!raw) return "";
  const label = labels?.get(raw);
  if (label && label.trim()) return label.trim();
  const local = raw.split("@")[0] || raw;
  return local
    .split(/[._-]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** The first name only, for "Waiting for Priya". */
export function firstName(
  email?: string | null,
  labels?: ReadonlyMap<string, string>,
): string {
  return personName(email, labels).split(/\s+/)[0] ?? "";
}

/* ── The pill ────────────────────────────────────────────────────────────── */

export interface AgentPillFacts {
  /** `mcp:<slug>` of the run, when the caller knows it. */
  clientVia?: string | null;
  /** The run's member, when the caller knows it. */
  memberEmail?: string | null;
  /** The newest thought, for the tooltip of "working". */
  thought?: string | null;
  labels?: ReadonlyMap<string, string>;
}

export interface AgentPillText {
  text: string;
  /** The tooltip. */
  title: string;
}

/**
 * The pill's words, by the table in §12.1. A task row carries only
 * `agent_state`, so a row with no client or member reads a generic name
 * ("Agent", "a person"). The task detail knows both, from the run.
 */
export function agentPillText(
  state: AgentState,
  facts: AgentPillFacts = {},
): AgentPillText {
  const client = clientName(facts.clientVia);
  const member = firstName(facts.memberEmail, facts.labels);
  switch (state) {
    case "awaiting":
      return {
        text: member ? `Waiting for ${member}` : "Waiting for an answer",
        title: `${client} asked a question and waits for an answer`,
      };
    case "answered":
      return {
        text: "Answered · agent picks up next",
        title: `${client} reads the answer at its next step`,
      };
    case "working":
      return {
        text: `${client} working`,
        title: facts.thought?.trim() || `${client} works on this task`,
      };
    case "queued":
      return {
        text: `Queued for ${client}`,
        title: `${client} starts this run at its next session`,
      };
    case "stale":
      return {
        text: `${client} idle`,
        title: `${client} sent nothing for 5 minutes`,
      };
    case "stopping":
      return {
        text: "Stopping",
        title: `${client} stops at its next step`,
      };
    case "failed":
      return {
        text: "Agent failed",
        title: `The run of ${client} failed`,
      };
  }
}

/** A value from the wire → a known state, or null. Never throws. */
export function asAgentState(value: unknown): AgentState | null {
  return (AGENT_STATE_VALUES as readonly unknown[]).includes(value)
    ? (value as AgentState)
    : null;
}

/* ── The run panel ───────────────────────────────────────────────────────── */

export function isOpenRun(run: AgentRun): boolean {
  return (OPEN_RUN_STATUSES as readonly string[]).includes(run.status);
}

/**
 * The run the task detail shows: the newest open run, else a newest run
 * that failed. A failed run shows until a newer run exists (EA12-12).
 */
export function runToShow(runs: readonly AgentRun[]): AgentRun | null {
  const open = runs.find(isOpenRun);
  if (open) return open;
  const newest = runs[0];
  return newest && newest.status === "failed" ? newest : null;
}

/** Add new activities to the ones held, by `seq`, with no duplicate. */
export function mergeActivities(
  held: readonly AgentActivity[],
  incoming: readonly AgentActivity[],
): AgentActivity[] {
  const bySeq = new Map<number, AgentActivity>();
  for (const a of held) bySeq.set(a.seq, a);
  for (const a of incoming) bySeq.set(a.seq, a);
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

/** The highest `seq` held, for the next `after`. */
export function lastSeq(activities: readonly AgentActivity[]): number | undefined {
  return activities.length ? activities[activities.length - 1].seq : undefined;
}

/** The newest `thought` or `action`, as one line. */
export function newestEphemeral(activities: readonly AgentActivity[]): string | null {
  for (let i = activities.length - 1; i >= 0; i -= 1) {
    const a = activities[i];
    if (a.type === "thought" && a.content.body?.trim()) return a.content.body.trim();
    if (a.type === "action") {
      const name = a.content.action?.trim();
      if (name) return `Running ${name}`;
      if (a.content.body?.trim()) return a.content.body.trim();
    }
  }
  return null;
}

/** The newest elicitation of the run, from the activities or the run row. */
export function latestElicitation(
  activities: readonly AgentActivity[],
  run?: AgentRun | null,
): AgentActivity | null {
  for (let i = activities.length - 1; i >= 0; i -= 1) {
    if (activities[i].type === "elicitation") return activities[i];
  }
  return run?.open_elicitation ?? run?.elicitation ?? null;
}

/** The member's prompt that answers this elicitation, if one exists. */
export function answerTo(
  activities: readonly AgentActivity[],
  elicitation: AgentActivity,
): AgentActivity | null {
  for (let i = activities.length - 1; i >= 0; i -= 1) {
    const a = activities[i];
    if (a.type === "prompt" && a.in_reply_to === elicitation.id && a.signal !== "stop") {
      return a;
    }
  }
  return null;
}

/** The elicitation as the chat's question card reads it. */
export function cardQuestion(elicitation: AgentActivity): ElicitationQuestion {
  const options = elicitation.signal_metadata?.options ?? [];
  return {
    header: "Question",
    question: elicitation.content.body?.trim() || "The agent asks a question.",
    multiSelect: Boolean(elicitation.signal_metadata?.allow_multiple),
    allowFreeformInput: true,
    options: options.map((o) => ({
      label: o.label,
      description: o.description ?? null,
      recommended: Boolean(o.recommended),
    })),
  };
}

/**
 * The card's answer → the prompt body. The card picks by label, and the
 * wire wants option ids, so each label maps back to the id of its option.
 * The typed text goes in `body`.
 */
export function answerPayload(
  elicitation: AgentActivity,
  answers: ElicitationAnswers,
  idempotencyKey: string = newIdempotencyKey(),
): AgentPromptBody {
  const picked = answers.Question ?? Object.values(answers)[0] ?? {};
  const options = elicitation.signal_metadata?.options ?? [];
  const selected = (picked.selected ?? [])
    .map((label) => options.find((o) => o.label === label)?.id)
    .filter((id): id is string => Boolean(id));
  const body = picked.freeform?.trim();
  const out: AgentPromptBody = { type: "prompt", idempotency_key: idempotencyKey };
  if (selected.length) out.selected = selected;
  if (body) out.body = body;
  return out;
}

/** A free reply under the run. */
export function replyPayload(
  body: string,
  idempotencyKey: string = newIdempotencyKey(),
): AgentPromptBody {
  return { type: "prompt", body: body.trim(), idempotency_key: idempotencyKey };
}

/**
 * Stop. ASSUMED: EA12-7 says a prompt needs `selected`, `body` or both, so
 * the Stop carries a short body too. The comment row then reads it.
 */
export const STOP_BODY = "Stop requested.";

export function stopPayload(idempotencyKey: string = newIdempotencyKey()): AgentPromptBody {
  return {
    type: "prompt",
    signal: "stop",
    body: STOP_BODY,
    idempotency_key: idempotencyKey,
  };
}

/** "Answered by Priya: Option A, Option B", the collapsed card's one line. */
export function answeredLine(
  prompt: AgentActivity,
  elicitation: AgentActivity,
  labels?: ReadonlyMap<string, string>,
): string {
  const who = personName(prompt.actor, labels) || "a member";
  const options = elicitation.signal_metadata?.options ?? [];
  const picked = (prompt.content.selected ?? [])
    .map((id) => options.find((o) => o.id === id)?.label ?? id)
    .filter(Boolean);
  const text = prompt.content.body?.trim();
  const parts = [...picked];
  if (text) parts.push(`“${text.length > 80 ? `${text.slice(0, 79)}…` : text}”`);
  return `Answered by ${who}: ${parts.join(", ") || "no choice"}`;
}

/**
 * The state the panel shows now. The server's `state` wins. After a write
 * and before the re-read lands, the panel shows its own guess.
 */
export function panelState(run: AgentRun, optimistic?: AgentState | null): AgentState | null {
  if (optimistic) return optimistic;
  const state = asAgentState(run.state);
  if (state) return state;
  if (run.status === "failed") return "failed";
  return null;
}

/* ── The timeline ────────────────────────────────────────────────────────── */

/**
 * The byline mark of a comment that an agent run wrote (`meta.agent_kind`).
 * The run panel draws the interactive card, so the timeline row of an
 * elicitation stays text, with this mark, and never a second card.
 */
export function agentKindLabel(kind: unknown): string | null {
  switch (kind) {
    case "elicitation":
      return "Agent question";
    case "response":
      return "Agent reply";
    case "error":
      return "Agent error";
    case "prompt":
      return "Answer to agent";
    default:
      return null;
  }
}

/* ── The "Waiting on me" filter ──────────────────────────────────────────── */

/**
 * The fields a task row carries for agent runs. `agent_state` is in the
 * spec (EA12-10). ASSUMED and optional: `agent_client_via` and
 * `agent_member_email` name the run's client and member on the row, so a
 * row's pill can say "Waiting for Priya" and not "Waiting for an answer".
 * The run panel fills them in on the open task.
 */
export interface AgentRowFacts {
  agent_state?: AgentState | null;
  agent_client_via?: string | null;
  agent_member_email?: string | null;
  assignees?: string[];
}

/**
 * §12.1: a task whose `agent_state` is `awaiting`, and whose run member is
 * the caller, or whose assignees include the caller. This slice filters the
 * rows already loaded, so a task on a page not yet read does not show.
 */
export function isWaitingOnMe(task: AgentRowFacts, me: string): boolean {
  if (task.agent_state !== "awaiting") return false;
  const who = me.trim().toLowerCase();
  if (!who) return false;
  if (task.agent_member_email?.trim().toLowerCase() === who) return true;
  return (task.assignees ?? []).some((a) => a.trim().toLowerCase() === who);
}
