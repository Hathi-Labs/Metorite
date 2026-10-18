/**
 * Agent runs on a task: the client half of EA-12c (`external_agent_access.md`
 * §12.1, D-EA-10).
 *
 * An outside agent works on a task inside a RUN. The run holds typed
 * ACTIVITIES. An `elicitation` asks the member a question, and the run waits
 * for a `prompt` that answers it. This file holds the wire types, the pure
 * rules the pill and the answer card read. The calls are `agentRunsApi` in
 * `api.ts`, so the F3 fence (`test_projects_ui_actions.py`) reads them.
 *
 * ⚠️ **The wire types are in THIS file only, on purpose.** EA-12c was built
 * before the EA-12a routes merged, against the shapes in the spec. One file
 * keeps the reconcile to one diff. Each guess about a shape is marked
 * "ASSUMED" below, and `normalizeRunDetail` reads both of the forms that
 * the detail route can plausibly take.
 *
 * The calls go through the one request seam for `/api/projects/*`. The BFF
 * proxy relays any path under it, so the routes need no proxy change. A POST
 * through it drops the cached Projects reads, so the board re-reads the task
 * and its pill.
 *
 * **The archive request (EA-6g, EA3-40).** An agent may ask to archive a
 * task. The server types that question (`content_metadata.request`), and only
 * the run's member answers it, through the archive approval route. The rules
 * of that card are at the end of this file.
 *
 * Fence: `agentRuns.test.ts`.
 */

import type { ElicitationAnswers, ElicitationQuestion } from "@/lib/elicitationAnswer";


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

/**
 * The `state` of one run on the runs routes. An open run reads as its
 * pill state. A closed run reads as its status, so `completed` and
 * `stopped` occur here and never on a task row.
 */
export type AgentRunState = AgentState | "completed" | "stopped";

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
    /** EA-6g: the member's answer to an archive request archived the task. */
    archived?: boolean | null;
  };
  /**
   * The server's own facts. EA-6g: an archive request carries `request`.
   * No body of an agent can write it, so only the server types a request.
   */
  content_metadata?: { request?: ArchiveRequest | null } | null;
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
  /** The derived state (`AGENT_RUN_STATE_SQL`). A closed run reads its status. */
  state?: AgentRunState | null;
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
  /** The `seq` to send as `after` next, or null when the run has none. */
  cursor: number | null;
}

/** The body of `POST /projects/agent-runs/{run_id}/activities` from a member. */
export interface AgentPromptBody {
  type: "prompt";
  body?: string;
  selected?: string[];
  signal?: "stop";
  idempotency_key: string;
}

/**
 * The body of `POST /projects/tasks/{task_id}/agent-runs` from a member
 * (EA-12d): one of their own live connections, and the first `prompt`.
 */
export interface AgentStartBody {
  connection_id: string;
  body: string;
  idempotency_key: string;
}

/** The answer of a start. `created` is false when an open run answered. */
export interface AgentStartResult {
  run: AgentRun;
  created: boolean;
}

/* ── The wire readers ────────────────────────────────────────────────────── */

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
  const cursor = typeof body.cursor === "number" ? body.cursor : null;
  return { run, activities, cursor };
}

/** The runs list, in either `{rows}` or bare-array form. */
export function agentRunRows<T>(raw: unknown): T[] {
  if (Array.isArray(raw)) return raw as T[];
  const rows = (raw as { rows?: unknown } | null)?.rows;
  return Array.isArray(rows) ? (rows as T[]) : [];
}

/** A fresh idempotency key for one write. */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

/* ── Names ───────────────────────────────────────────────────────────────── */

/**
 * WS-55 EA-13a (spec §12.2 item 1, EA3-45, EA3-47). The curated name of a
 * client stamp. A stamp is `mcp:<host>:<slug>` for a client with a metadata
 * document, or the legacy `mcp:<host>`. This map is `CLIENT_LABELS` of
 * `acb_auth/agent_oauth.py`, key for key. `tests/fixtures/agent_client_names.json`
 * holds both, and `agentRuns.test.ts` and `test_agent_client_stamp.py` fail
 * when either map differs from it.
 *
 * ⚠️ The self-declared `client_name` of a document never names a client here.
 * Any host can publish a document that says "Claude Code".
 */
export const CLIENT_LABELS: Readonly<Record<string, string>> = {
  "claude.ai:claude-code": "Claude Code",
  // The legacy host stamp. It is true for every client on that host.
  "claude.ai": "Claude",
};

/**
 * The name of a client stamp. A known stamp reads its label. Else the label
 * of its host, else the host itself, so `mcp:evil.example:claude-code` reads
 * "evil.example". An empty value reads "Agent".
 */
export function clientName(via?: string | null): string {
  const raw = (via ?? "").trim().toLowerCase();
  const body = raw.startsWith("mcp:") ? raw.slice(4) : raw;
  // An own key only: `mcp:constructor` must not read a property of Object.
  const label = (key: string) =>
    Object.prototype.hasOwnProperty.call(CLIENT_LABELS, key) ? CLIENT_LABELS[key] : null;
  // A body with more than one colon is an IPv6 host stamp: the gateway adds
  // no slug to an IPv6 host, so the whole body is the host.
  const host = (body.match(/:/g) ?? []).length > 1 ? body : body.split(":")[0];
  return label(body) ?? label(host) ?? (host || "Agent");
}

/* ── The honest timeline (EA-13b) ─────────────────────────────────────────── */

/** The `via` of a row that the GitHub App wrote (`github_integration.md` §12.4). */
export const GITHUB_VIA = "github";
/** The `created_by` of a row that the GitHub App wrote. */
export const GITHUB_ACTOR = "system:github";

/**
 * WS-55 EA-13b (spec §12.2 item 5, EA3-44). The ONE label of a `via` stamp:
 * the client label of EA-13a for `mcp:…`, and "GitHub" for `github`. Null
 * for every other stamp, the chat's `chat:projects-assistant` too, and for no
 * stamp, which is a person's own act.
 */
export function viaLabel(via?: string | null): string | null {
  const raw = (via ?? "").trim().toLowerCase();
  if (raw === GITHUB_VIA) return "GitHub";
  if (raw.startsWith("mcp:")) return clientName(raw);
  return null;
}

/** The parts of a timeline row that the stamp decides. */
export interface ActivityAttribution {
  /** "GitHub" for a row of the GitHub App. Null: the byline names the author. */
  who: string | null;
  /** `through Claude Code` for an act through an agent. Null for a person's act. */
  through: string | null;
  /** The sentence of a GitHub merge row, which replaces the row's text. */
  reason: string | null;
}

/** The fields of one timeline row that `activityAttribution` reads. */
export interface AttributedRow {
  created_by?: string | null;
  meta?: Record<string, unknown> | null;
}

/**
 * The byline and the text of one timeline row, by its stamp (EA3-44).
 *
 * - A row whose `meta.via` starts with `mcp:` reads "through <client>": a
 *   comment, a status move, an assignment, a claim and a link alike. So
 *   "Ravi through Claude Code" sits above "moved this to In review".
 * - A row of `system:github` reads "GitHub". Its merge row (§12.4 of
 *   `github_integration.md`) reads "GitHub moved this to Done after
 *   owner/repo#12 merged".
 * - A person's own row, and a chat row, get nothing.
 */
export function activityAttribution(row: AttributedRow): ActivityAttribution {
  const meta = row.meta ?? {};
  const via = typeof meta.via === "string" ? meta.via.trim().toLowerCase() : "";
  const fromGithub = (row.created_by ?? "").trim().toLowerCase() === GITHUB_ACTOR;
  return {
    who: fromGithub ? "GitHub" : null,
    through: via.startsWith("mcp:") ? `through ${clientName(via)}` : null,
    reason: fromGithub ? githubMergeReason(meta) : null,
  };
}

/**
 * "GitHub moved this to Done after owner/repo#12 merged", from the `github`
 * object of a merge row's meta. Null when the row names no pull request. The
 * lane name is `meta.to` when the row carries one, else "Done".
 */
export function githubMergeReason(meta: Record<string, unknown>): string | null {
  const gh = meta.github;
  if (!gh || typeof gh !== "object") return null;
  const { repo, number } = gh as { repo?: unknown; number?: unknown };
  if (typeof repo !== "string" || !repo.trim()) return null;
  if (typeof number !== "number" || !Number.isInteger(number) || number < 1) return null;
  const lane = typeof meta.to === "string" && meta.to.trim() ? meta.to.trim() : "Done";
  return `GitHub moved this to ${lane} after ${repo.trim()}#${number} merged`;
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
 * The run the task detail shows, by the pill's rule (§12.1, "The task
 * pill"): an open `awaiting` run, else an open `answered` run, else the
 * newest open run. A newer run of another member must not hide a question.
 * With no open run, a newest run that failed shows until a newer run
 * exists (EA12-12). `runs` comes newest first.
 */
export function runToShow(runs: readonly AgentRun[]): AgentRun | null {
  const openRuns = runs.filter(isOpenRun);
  const open =
    openRuns.find((r) => r.state === "awaiting") ??
    openRuns.find((r) => r.state === "answered") ??
    openRuns[0];
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

/**
 * The words after a member's write fails, and whether the panel reads the
 * run again. A 409 means the run moved under the member: another member
 * answered first, or the agent stopped. A retry cannot help, so the panel
 * reads the run and shows the server's reason with no "Try again".
 */
export function writeFailure(
  status: number | undefined,
  message: string | undefined,
  failure: string,
): { text: string; reload: boolean } {
  const why = message?.trim() ?? "";
  if (status === 409) {
    return { text: why || "This run changed. It now shows the newest state.", reload: true };
  }
  return { text: `${failure}${why ? ` ${why}` : ""} Try again.`, reload: false };
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

/**
 * What an empty board column, or an empty list, says while "Waiting on me"
 * is on. "Drop here" invited a drag into a lane that the filter emptied.
 */
export const WAITING_ON_ME_EMPTY = "Nothing waits on you";

/** The hint under the list's empty state while the filter is on. */
export const WAITING_ON_ME_HINT = "No agent waits for your answer on a task in this view.";

/* ── The archive request (EA-6g, EA3-33 and EA3-40) ──────────────────────── */

/** The `kind` of a typed archive request, as the gateway writes it. */
export const ARCHIVE_REQUEST_KIND = "archive_task";

/** `content_metadata.request` of an archive request. */
export interface ArchiveRequest {
  kind: string;
  /** The server stamps it 24 hours after the request. */
  expires_at?: string | null;
}

/** The two answers of the run's member. They are the option ids too. */
export type ArchiveDecision = "approve" | "decline";

/** The body of `POST /projects/agent-runs/{run_id}/archive-approval`. */
export interface ArchiveApprovalBody {
  decision: ArchiveDecision;
  idempotency_key: string;
}

/** The answer of the approval route. `archived` is true after an approve. */
export interface ArchiveApprovalResult {
  activity?: AgentActivity | null;
  run?: AgentRun | null;
  created?: boolean;
  archived: boolean;
  /** The archived task row after an approve, else null. */
  task?: { archived_at?: string | null } | null;
}

/** The title of the member's card, in place of "Question". */
export const ARCHIVE_CARD_TITLE = "Archive request";

/** The line beside the member's Submit. */
export const ARCHIVE_HINT = "Approve archives this task. Its subtasks stay where they are.";

/**
 * What every member reads after the request expired. The run does not stay
 * stuck: the member may decline it, and any member may reply (fix round 1).
 */
export const ARCHIVE_EXPIRED_TEXT = "This archive request expired. Nothing was archived.";

/** The line under the expired text, for the run's member. */
export const ARCHIVE_EXPIRED_HINT = "Decline it to close it, or reply to the agent below.";

/** The typed archive request of an elicitation, or null for any other one. */
export function archiveRequestOf(activity?: AgentActivity | null): ArchiveRequest | null {
  if (!activity || activity.type !== "elicitation") return null;
  const request = activity.content_metadata?.request;
  return request && request.kind === ARCHIVE_REQUEST_KIND ? request : null;
}

/**
 * True when the request is past its `expires_at`. A stamp that does not
 * parse reads as expired, as the gateway reads it, so no button shows.
 */
export function archiveExpired(request: ArchiveRequest, now: number = Date.now()): boolean {
  const at = Date.parse(request.expires_at ?? "");
  return Number.isNaN(at) || now >= at;
}

/** Is *me* the run's member? Only that member answers an archive request. */
export function isRunMember(run: Pick<AgentRun, "member_email">, me?: string | null): boolean {
  const who = (me ?? "").trim().toLowerCase();
  return Boolean(who) && run.member_email.trim().toLowerCase() === who;
}

/**
 * The archive request as the question card reads it: the server's two
 * options and no free text, because the approval takes a decision only.
 */
export function archiveCardQuestion(elicitation: AgentActivity): ElicitationQuestion {
  return { ...cardQuestion(elicitation), allowFreeformInput: false, multiSelect: false };
}

/** The card's answer → the decision, by the label of the picked option. */
export function archiveDecisionOf(
  elicitation: AgentActivity,
  answers: ElicitationAnswers,
): ArchiveDecision | null {
  const picked = answers.Question ?? Object.values(answers)[0] ?? {};
  const label = (picked.selected ?? [])[0];
  const id = (elicitation.signal_metadata?.options ?? []).find((o) => o.label === label)?.id;
  return id === "approve" || id === "decline" ? id : null;
}

/** The approval body, with a fresh key. */
export function archiveApprovalPayload(
  decision: ArchiveDecision,
  idempotencyKey: string = newIdempotencyKey(),
): ArchiveApprovalBody {
  return { decision, idempotency_key: idempotencyKey };
}

/** What every other member reads while the request waits: "Waiting for Priya". */
export function archiveWaitingText(
  run: Pick<AgentRun, "member_email">,
  labels?: ReadonlyMap<string, string>,
): string {
  const who = firstName(run.member_email, labels);
  return who ? `Waiting for ${who}` : "Waiting for the member of this run";
}
