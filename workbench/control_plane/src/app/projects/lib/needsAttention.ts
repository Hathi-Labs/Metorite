/**
 * WS-55 SW-11a — "Needs attention", as pure rules.
 *
 * Spec: `project-docs/specs/external_agent_access.md` §12.7.
 *
 * The server decides every row, every count and who may act
 * (`routes/projects/needs_attention.py`). This module only says how the list,
 * the header badge and the two acts word what the server sent. It computes no
 * rule of its own, so the board and the chat read one answer.
 *
 * ⚠️ **`can_act` is the server's.** It is the rule of Release (EA-14a). The
 * run panel's own Release button still reads `access.is_admin` (HANDOFF
 * H-409 item 2), and this list does not add a third rule.
 *
 * Fence: `needsAttention.test.ts`.
 */
import type { AccentHue } from "@/lib/statusAccent";

import { clientName, newIdempotencyKey } from "./agentRuns";
import { taskLabel, waitedFor } from "./waiting";

/** The three kinds, in the order the server sends them and the list draws them. */
export const ATTENTION_KINDS = ["stalled", "dangling", "blocked"] as const;
export type AttentionKind = (typeof ATTENTION_KINDS)[number];

/** The acts. `resume` and `close_run` write. `open_blocker` only opens a task. */
export type AttentionAction = "resume" | "close_run" | "open_blocker";

/** One row of `GET /projects/needs-attention`. A field that does not apply is null. */
export interface AttentionRow {
  kind: AttentionKind;
  task_id: string;
  task_number: number | null;
  title: string;
  project_id: string;
  project_name: string | null;
  parent_task_id: string | null;
  category: string | null;
  archived: boolean;
  owner: string | null;
  owner_name: string | null;
  since: string | null;
  age_hours: number | null;
  run_id: string | null;
  run_status: string | null;
  client_via: string | null;
  client_name: string | null;
  /** The strand reason of a stalled row, or why the task of a dangling row is closed. */
  reason: string | null;
  blocker_id: string | null;
  blocker_number: number | null;
  blocker_title: string | null;
  action: AttentionAction;
  actions: AttentionAction[];
  /** The rule of Release, decided by the server. */
  can_act: boolean;
  /** The caller owns the row: the run's member, or an assignee of the blocked task. */
  yours: boolean;
}

/** The whole answer of the route. */
export interface AttentionPage {
  rows: AttentionRow[];
  counts: Record<AttentionKind, number>;
  total: number;
  listed: number;
  offset: number;
  limit: number;
  capped: boolean;
}

/** What `POST /projects/agent-runs/{id}/resume` answers. */
export interface ResumeResult {
  resumed: boolean;
  created: boolean;
}

/** The largest page the route serves. The list asks for all of it. */
export const ATTENTION_LIMIT = 200;

/** The heading, the one plain line and the dot hue of each kind. */
export const KIND_COPY: Readonly<
  Record<AttentionKind, { heading: string; hint: string; hue: AccentHue }>
> = {
  stalled: {
    heading: "Stalled agent runs",
    hint: "An agent stopped working, and no session came back after one nudge.",
    hue: "amber",
  },
  dangling: {
    heading: "Agent runs on closed tasks",
    hint: "The task is done, cancelled or archived, and its agent run is still open.",
    hue: "red",
  },
  blocked: {
    heading: "Blocked by parked work",
    hint: "The blocker sits in the backlog, and nobody holds it.",
    hue: "blue",
  },
};

/** The label of each act. One act has one label, everywhere. */
export const ACTION_LABEL: Readonly<Record<AttentionAction, string>> = {
  resume: "Resume",
  close_run: "Close the run",
  open_blocker: "Open the blocker",
};

/** The icon of each act. Lucide names, through `<Icon>`. */
export const ACTION_ICON: Readonly<Record<AttentionAction, string>> = {
  resume: "Play",
  close_run: "LogOut",
  open_blocker: "ArrowUpRight",
};

/** What a row says when the caller may not take its act. */
export const CANNOT_ACT =
  "Only the run's member, an assignee of the task or an admin can act on this run.";

/** The words of the toggle beside the view switch. */
export const LENS_LABEL = "Needs attention";

/**
 * The header badge, or null when nothing needs attention: "1 needs
 * attention", "3 need attention". The badge then draws nothing.
 */
export function badgeText(total: number | null | undefined): string | null {
  if (typeof total !== "number" || !Number.isFinite(total) || total <= 0) return null;
  return total === 1 ? "1 needs attention" : `${total} need attention`;
}

/** Read the route's answer defensively. A shape it cannot read is an empty page. */
export function attentionPage(raw: unknown): AttentionPage {
  const body = (raw && typeof raw === "object" ? raw : {}) as Partial<AttentionPage>;
  const rows = Array.isArray(body.rows) ? body.rows.filter(isRow) : [];
  const counts = { stalled: 0, dangling: 0, blocked: 0 };
  for (const kind of ATTENTION_KINDS) {
    const n = (body.counts as Record<string, unknown> | undefined)?.[kind];
    counts[kind] = typeof n === "number" && Number.isFinite(n) ? n : 0;
  }
  const total = typeof body.total === "number" ? body.total : rows.length;
  return {
    rows,
    counts,
    total,
    listed: typeof body.listed === "number" ? body.listed : rows.length,
    offset: typeof body.offset === "number" ? body.offset : 0,
    limit: typeof body.limit === "number" ? body.limit : ATTENTION_LIMIT,
    capped: body.capped === true,
  };
}

function isRow(value: unknown): value is AttentionRow {
  const row = value as Partial<AttentionRow> | null;
  return Boolean(
    row &&
      typeof row.task_id === "string" &&
      typeof row.kind === "string" &&
      (ATTENTION_KINDS as readonly string[]).includes(row.kind),
  );
}

/** One section of the list: a kind with its rows, in the server's order. */
export interface AttentionGroup {
  kind: AttentionKind;
  heading: string;
  hint: string;
  hue: AccentHue;
  rows: AttentionRow[];
  /** The exact count of the kind, which can exceed the rows when it is capped. */
  count: number;
}

/** The sections with rows, in the order of `ATTENTION_KINDS`. */
export function attentionGroups(page: AttentionPage): AttentionGroup[] {
  return ATTENTION_KINDS.map((kind) => ({
    kind,
    ...KIND_COPY[kind],
    rows: page.rows.filter((r) => r.kind === kind),
    count: page.counts[kind],
  })).filter((group) => group.rows.length > 0);
}

/** A stable key for a row: one run, or one link. */
export function rowKey(row: AttentionRow): string {
  return `${row.kind}:${row.run_id ?? row.blocker_id ?? ""}:${row.task_id}`;
}

/** "#12 Fix the extruder". */
export function rowTitle(row: AttentionRow): string {
  return taskLabel(row);
}

/** The client of a run row, in the one name map. */
function client(row: AttentionRow): string {
  return row.client_name || clientName(row.client_via) || "The agent";
}

/** Why the row is here, and since when, in one line. */
export function rowDetail(row: AttentionRow): string {
  const age = waitedFor(row.age_hours);
  if (row.kind === "stalled") {
    return row.reason === "answer_unread"
      ? `${client(row)} did not read the answer for ${age}.`
      : `${client(row)} sent nothing for ${age}.`;
  }
  if (row.kind === "dangling") {
    const why =
      row.reason === "archived" ? "archived" : row.reason === "cancelled" ? "cancelled" : "done";
    return `The task is ${why}. The run of ${client(row)} is still open, quiet for ${age}.`;
  }
  const blocker = taskLabel({
    task_number: row.blocker_number,
    title: row.blocker_title ?? "a task",
  });
  return `Blocked by ${blocker} for ${age}. It is in the backlog, and nobody holds it.`;
}

/** "Alice Archer", "You", or "No owner". *labels* maps an address to a name. */
export function ownerText(row: AttentionRow, labels?: ReadonlyMap<string, string>): string {
  if (row.yours) return "You";
  if (!row.owner) return "No owner";
  return row.owner_name && row.owner_name !== row.owner
    ? row.owner_name
    : labels?.get(row.owner.toLowerCase()) ?? row.owner;
}

/**
 * The acts a row offers the caller, the next one first. A run act shows only
 * when the server says the caller may take it. Opening a blocker is a read,
 * so it always shows.
 */
export function rowActions(row: AttentionRow): AttentionAction[] {
  const acts = row.actions?.length ? row.actions : [row.action];
  return acts.filter((act) => act === "open_blocker" || row.can_act);
}

/** True when a row has a run act that the caller may not take. */
export function runActBlocked(row: AttentionRow): boolean {
  return row.kind !== "blocked" && !row.can_act;
}

/** The confirm of Close the run. It names the client, and what stays. */
export function closeRunCopy(row: AttentionRow): {
  title: string;
  body: string;
  confirm: string;
} {
  return {
    title: "Close this agent run?",
    body: `The run of ${client(row)} ends, and the agent stops on this task. The task keeps its status and its assignees.`,
    confirm: ACTION_LABEL.close_run,
  };
}

/** The body of Resume: a fresh key, so a retry of one press writes once. */
export function resumeBody(idempotencyKey: string = newIdempotencyKey()): {
  idempotency_key: string;
} {
  return { idempotency_key: idempotencyKey };
}

/** What the list says when it holds nothing. */
export const EMPTY_COPY = {
  message: "Nothing needs attention in this project.",
  hint: "Stalled agent runs, runs on closed tasks and work blocked by a parked task show here.",
} as const;

/** The line under a list that holds only part of a kind. */
export function cappedLine(page: AttentionPage): string | null {
  if (!page.capped && page.listed <= page.rows.length) return null;
  return `Showing ${page.rows.length} of ${page.total}. The oldest come first.`;
}
