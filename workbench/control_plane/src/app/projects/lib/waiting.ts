/**
 * WS-27bn RA-3 — "Waiting on people", the coordination panel, as pure rules.
 *
 * Spec: `project-docs/specs/projects_reports.md` §6.8 C and E, §8 RA-3.
 *
 * The server decides every row and every count (`analytics_waiting.py`), and
 * it writes the one summary sentence. This module only says how the panel,
 * its table and the email word what the server sent. It computes no figure.
 *
 * ⚠️ **No rank of people (§6.8 E1).** The rows keep the server's order: the
 * longest wait first. Nothing here sorts or groups by person.
 *
 * ⚠️ **An agent never hides its person (§6.8 E2).** `agentLine` names the
 * agent and the member it works for, on every row.
 *
 * Fence: `waiting.test.ts`.
 */
import type { WaitingHandoff, WaitingReport, WaitingReview, WaitingRow } from "./api";

/** The three lists of the panel, in the panel's order. */
export type WaitingKind = "questions" | "reviews" | "handoffs";

/** The heading of each list. */
export const WAITING_HEADINGS: Readonly<Record<WaitingKind, string>> = {
  questions: "Agent questions with no answer",
  reviews: "Agent work waiting for a review",
  handoffs: "Handoffs that block work",
};

/** How many rows of each list the panel shows. The table shows them all. */
export const PANEL_ROWS = 5;

/** The words of an age, as the server's summary says them. */
export function waitedFor(hours: number | null | undefined): string {
  if (typeof hours !== "number" || !Number.isFinite(hours) || hours < 1) return "under an hour";
  if (hours < 48) {
    const n = Math.round(hours);
    return n === 1 ? "1 hour" : `${n} hours`;
  }
  return `${Math.round(hours / 24)} days`;
}

/** "#12 Fix the extruder", or the title alone. */
export function taskLabel(row: Pick<WaitingRow, "task_number" | "title">): string {
  return typeof row.task_number === "number" ? `#${row.task_number} ${row.title}` : row.title;
}

/**
 * The agent and the member it works for: "Claude Code for Asha Rao". An
 * in-app agent works for no member, and the line names the agent alone.
 * `name` resolves an address to a directory name when the row has none.
 */
export function agentLine(
  row: Pick<WaitingRow, "agent" | "member" | "member_name">,
  name: (email: string) => string = (e) => e
): string {
  const agent = row.agent || "Agent";
  const member = row.member_name || (row.member ? name(row.member) : null);
  return member ? `${agent} for ${member}` : agent;
}

/** Why a review row waits, in words. */
export function reviewWhy(row: Pick<WaitingReview, "reasons">): string {
  const lane = row.reasons.includes("review_lane");
  const pr = row.reasons.includes("pull_request");
  if (lane && pr) return "In review, with an open pull request";
  if (pr) return "An open pull request";
  return "In a review lane";
}

/** Which side of a handoff waits on whom, in words. */
export function handoffWhy(
  row: Pick<WaitingHandoff, "agent_side" | "blocker_title" | "person" | "person_name">,
  name: (email: string) => string = (e) => e
): string {
  const person = row.person_name || (row.person ? name(row.person) : "a person");
  return row.agent_side === "blocker"
    ? `The agent's task blocks ${person}`
    : `Blocked by ${person}: ${row.blocker_title}`;
}

/** One row as words: the task, then who waits and why. */
export interface WaitingLine {
  /**
   * The one key of the row. A task makes two handoff rows when two tasks
   * block it, so a handoff key names the blocker. A question key names its
   * run, and a review key the task, which the server lists once.
   */
  key: string;
  /** The task id. A row opens this task. */
  id: string;
  label: string;
  detail: string;
}

/** One list as words, with the server's total. */
export interface WaitingList {
  kind: WaitingKind;
  heading: string;
  total: number;
  rows: WaitingLine[];
}

function rowsOf<T>(list: readonly T[] | undefined): T[] {
  return Array.isArray(list) ? [...list] : [];
}

/**
 * The three lists as words, in the panel's order and the server's row order.
 * The panel, its table and the email all read this, so they say one thing.
 */
export function waitingLists(
  section: WaitingReport,
  name: (email: string) => string = (e) => e
): WaitingList[] {
  const line = (r: WaitingRow, why: string, key: string): WaitingLine => ({
    key,
    id: r.id,
    label: taskLabel(r),
    detail: `${agentLine(r, name)} · ${waitedFor(r.age_hours)} · ${why}`,
  });
  return [
    {
      kind: "questions",
      heading: WAITING_HEADINGS.questions,
      total: section.questions_total ?? 0,
      rows: rowsOf(section.questions).map((r) =>
        line(r, "asks for an answer", `question:${r.run_id ?? r.id}`)
      ),
    },
    {
      kind: "reviews",
      heading: WAITING_HEADINGS.reviews,
      total: section.reviews_total ?? 0,
      // One row for each task: the server picks the task once.
      rows: rowsOf(section.reviews).map((r) => line(r, reviewWhy(r), `review:${r.id}`)),
    },
    {
      kind: "handoffs",
      heading: WAITING_HEADINGS.handoffs,
      total: section.handoffs_total ?? 0,
      rows: rowsOf(section.handoffs).map((r) =>
        line(r, handoffWhy(r, name), `handoff:${r.blocker_id}:${r.id}`)
      ),
    },
  ];
}

/** The rows of one list, and how many the panel leaves out. */
export function listRows<T>(
  rows: readonly T[] | undefined,
  total: number | undefined,
  max = PANEL_ROWS
): { shown: T[]; more: number } {
  const list = Array.isArray(rows) ? rows : [];
  const shown = list.slice(0, max);
  const all = typeof total === "number" && total >= list.length ? total : list.length;
  return { shown, more: all - shown.length };
}

/** The rows the table names: every row of the three lists the server sent. */
export function waitingRowCount(section: WaitingReport): number {
  return waitingLists(section).reduce((n, l) => n + l.rows.length, 0);
}

/** True when nothing waits and no question came in the period. */
export function waitingIsClear(section: WaitingReport | null | undefined): boolean {
  if (!section) return false;
  // RA-4b. With runs off, no question and no answer time is read. The section
  // is not clear then: the panel draws and prints the server's summary.
  if (section.on === false) return false;
  return (
    (section.questions_total ?? 0) === 0 &&
    (section.reviews_total ?? 0) === 0 &&
    (section.handoffs_total ?? 0) === 0 &&
    (section.answer_time?.asked ?? 0) === 0
  );
}

/**
 * True when the Overview hides the panel: no task of the scope has an agent
 * run, and nothing waits. A scope with no agents gets no empty card. That is
 * calmer than a clear row that says the same thing on each visit (§6.8 C,
 * RA-4's rule for an agent panel). A report and the builder never hide it.
 */
export function waitingHidden(section: WaitingReport | null | undefined): boolean {
  // RA-4b. Empty by the old rule, whatever the flag. With runs off a review
  // wait or a handoff blocker still shows the panel, as a row of the section.
  if (!section || section.agent_runs_seen !== false) return false;
  return (
    (section.questions_total ?? 0) === 0 &&
    (section.reviews_total ?? 0) === 0 &&
    (section.handoffs_total ?? 0) === 0 &&
    (section.answer_time?.asked ?? 0) === 0
  );
}

/** The answer time as one line, or `null` when no question had an answer. */
export function answerLine(section: Pick<WaitingReport, "answer_time">): string | null {
  const a = section.answer_time;
  if (!a) return null;
  // No answer at all is a finding, not a gap: say how many questions got
  // none, as the chat does. No figure is printed, because none exists.
  if (a.answered === 0 || a.median_hours === null) {
    if (a.unanswered <= 0) return null;
    return a.unanswered === 1
      ? "1 question in this period got no answer."
      : `${a.unanswered} questions in this period got no answer.`;
  }
  const p90 = a.p90_hours === null ? "" : `, p90 ${waitedFor(a.p90_hours)}`;
  const open = a.unanswered > 0 ? `. ${a.unanswered} with no answer yet` : "";
  return `Answer time: median ${waitedFor(a.median_hours)}${p90}, over ${a.answered} answered${open}.`;
}
