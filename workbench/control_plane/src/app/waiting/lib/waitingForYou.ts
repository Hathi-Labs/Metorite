/**
 * The agent questions of the page `/waiting` (CIV-14k, moved from
 * `/approvals` by WS-AP AP-2a): every open agent question of the member,
 * across all projects, oldest first, until it has an answer. The rows come
 * from `GET /projects/agent-runs?status=awaiting&order=oldest&with_task=true`,
 * which lists the runs where `member_email` is the caller (AP-Q2 rule a).
 * These are the pure rules the page reads.
 *
 * Fence: `waitingForYou.test.ts`.
 */

import {
  archiveRequestOf,
  runIdentity,
  type AgentActivity,
  type AgentRun,
} from "@/app/projects/lib/agentRuns";

/** The task facts that `with_task=true` adds to each row. */
export interface WaitingTaskFacts {
  number: number | null;
  title: string | null;
  project_name?: string | null;
}

export type WaitingRun = AgentRun & { task?: WaitingTaskFacts | null };

/** The open question of a run. Both spellings of the key are read. */
export function openQuestion(run: WaitingRun): AgentActivity | null {
  return run.open_elicitation ?? run.elicitation ?? null;
}

/** When the run started to wait: its open question, else its last activity. */
export function askedAt(run: WaitingRun): string | null {
  return openQuestion(run)?.created_at ?? run.last_activity_at ?? null;
}

function askedAtMs(run: WaitingRun): number {
  const stamp = askedAt(run);
  const ms = stamp ? Date.parse(stamp) : NaN;
  return Number.isNaN(ms) ? Number.MAX_SAFE_INTEGER : ms;
}

/** The runs that wait for an answer, oldest question first. */
export function waitingRuns(runs: readonly WaitingRun[]): WaitingRun[] {
  return runs
    .filter((r) => r.status === "awaiting" && openQuestion(r) !== null)
    .map((run, index) => ({ run, index }))
    .sort((a, b) => askedAtMs(a.run) - askedAtMs(b.run) || a.index - b.index)
    .map((x) => x.run);
}

/** "#12 Fix the extruder", or the plain "Task" when the facts are absent. */
export function rowTitle(run: WaitingRun): string {
  const number = run.task?.number;
  const title = run.task?.title?.trim();
  const head = number !== null && number !== undefined ? `#${number}` : "";
  return [head, title].filter(Boolean).join(" ") || "Task";
}

/** The agent's name for the row, "Claude Code · claude.ai". */
export function rowAgent(run: WaitingRun): string {
  return runIdentity(run);
}

/** The link to the task card. */
export function taskHref(run: WaitingRun): string {
  return `/projects?task=${encodeURIComponent(run.task_id)}`;
}

/**
 * An archive request needs the archive approval route, and not a prompt. The
 * list shows it and links to the task card, where the member answers it.
 */
export function answersInPlace(run: WaitingRun): boolean {
  return archiveRequestOf(openQuestion(run)) === null;
}

/**
 * The runs whose OPEN question the page has not answered yet. The page keys
 * an answer by the question's id, never by the run's id: a run that gets an
 * answer goes back to work and can ask again (`awaiting` -> `in_progress` ->
 * `awaiting`). Its new question has a new id, so it shows at once.
 */
export function unanswered(
  runs: readonly WaitingRun[],
  answered: ReadonlySet<string>,
): WaitingRun[] {
  return runs.filter((run) => {
    const id = openQuestion(run)?.id;
    return !id || !answered.has(id);
  });
}
