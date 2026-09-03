/**
 * Projects · dependencies and subtasks in the browser (WS-27p).
 *
 * The gateway derives what is blocked; this decides how it reads. The awkward
 * part is that one table carries three relationships and each one means
 * something different depending on which end you are standing at — "blocks"
 * outgoing is *"this holds those up"*, incoming is *"this is waiting"*, and a
 * client that showed them under one heading would be telling people the
 * opposite of the truth half the time.
 */

export type LinkType = "blocks" | "relates_to" | "duplicates";
export type Direction = "outgoing" | "incoming";

export interface RelatedTask {
  id: string;
  link_id: string;
  link_type: LinkType;
  direction: Direction;
  title: string;
  task_number?: number | null;
  status_name?: string | null;
  category?: string | null;
  completed_at?: string | null;
  /**
   * WS-27t — carried so the schedule-conflict warning (D-PM-12) can be
   * computed from the SAME pure rule the timeline draws its red arrows with.
   * Two implementations of "does this start before its blocker finishes" would
   * eventually disagree, and the surface that got it wrong would be the one
   * nobody was looking at.
   */
  start_date?: string | null;
  due_at?: string | null;
}

export interface SubtaskRow {
  id: string;
  title: string;
  task_number?: number | null;
  status_id: string;
  status_name?: string | null;
  category?: string | null;
  completed_at?: string | null;
  start_date?: string | null;
  due_at?: string | null;
}

export interface Relations {
  subtasks: SubtaskRow[];
  progress: { done: number; total: number };
  links: RelatedTask[];
  blocked_by: RelatedTask[];
}

/**
 * A whole `Relations`, whatever the server sent.
 *
 * ⚠️ **THE TYPE IS A CLAIM, NOT A GUARANTEE.** `api.call` parses JSON and casts
 * it, so nothing at runtime checks that the four fields arrived. `RelationsBlock`
 * guarded `!data` and then read `data.links`, which is the mistake the type
 * invites: an object one field short is still truthy.
 *
 * Measured 2026-09-03 with the visual-review rig. A body without `links` threw
 * `Cannot read properties of undefined`, and the throw reached the React root
 * and blanked the document. `TaskPanel` renders OUTSIDE `LayoutBoundary`,
 * because that boundary wraps the canvases only. A blank page beside a working
 * tree reads as "this task has nothing", and not as "something failed".
 *
 * One normalisation here beats a guard at each read site. The next field this
 * component reads is then covered, without anybody remembering to add one.
 */
export function completeRelations(
  raw: Partial<Relations> | null | undefined
): Relations | null {
  if (!raw) return null;
  return {
    subtasks: raw.subtasks ?? [],
    links: raw.links ?? [],
    blocked_by: raw.blocked_by ?? [],
    progress: raw.progress ?? { done: 0, total: 0 },
  };
}

/** Mirrors the gateway's `CLOSING_CATEGORIES`. */
export const CLOSED = ["done", "cancelled"];

export const isResolved = (category: string | null | undefined): boolean =>
  CLOSED.includes(category ?? "");

/**
 * The headings a relations block shows, in the order it shows them.
 *
 * "Blocked by" comes FIRST because it is the only one that changes what
 * somebody should do next. The others are context.
 */
export const SECTIONS: Array<{
  key: string;
  label: string;
  type: LinkType;
  direction: Direction;
}> = [
  { key: "blocked_by", label: "Blocked by", type: "blocks", direction: "incoming" },
  { key: "blocks", label: "Blocks", type: "blocks", direction: "outgoing" },
  { key: "relates", label: "Related", type: "relates_to", direction: "outgoing" },
  { key: "relates_in", label: "Related", type: "relates_to", direction: "incoming" },
  { key: "dupes", label: "Duplicates", type: "duplicates", direction: "outgoing" },
  { key: "dupes_in", label: "Duplicated by", type: "duplicates", direction: "incoming" },
];

/** The links belonging to one section. */
export function section(
  links: RelatedTask[],
  type: LinkType,
  direction: Direction
): RelatedTask[] {
  return links.filter((l) => l.link_type === type && l.direction === direction);
}

/**
 * Sections that have something in them, with their links.
 *
 * Empty ones are dropped rather than rendered as headings with nothing under —
 * six empty headings on every task is how a panel becomes something people
 * scroll past.
 */
export function populated(links: RelatedTask[]): Array<{
  key: string;
  label: string;
  links: RelatedTask[];
}> {
  return SECTIONS.map(({ key, label, type, direction }) => ({
    key,
    label,
    links: section(links, type, direction),
  })).filter((s) => s.links.length > 0);
}

/**
 * How the subtask progress reads.
 *
 * "0 of 3" rather than "0%": a percentage of three things is a precision
 * nobody asked for, and 33% is a worse answer than "1 of 3" to the question
 * people are actually asking.
 */
export function progressLabel(progress: { done: number; total: number }): string {
  return `${progress.done} of ${progress.total}`;
}

/** 0–100 for a bar. `0` when there is nothing, rather than NaN. */
export function progressPercent(progress: { done: number; total: number }): number {
  if (progress.total <= 0) return 0;
  return Math.round((progress.done / progress.total) * 100);
}

/**
 * The one-line summary a card shows.
 *
 * `null` when there is nothing worth saying, so a card that has no relations
 * grows no extra row. Blocked wins over progress: a task with two subtasks and
 * an unfinished blocker cannot be started, and that is the more urgent fact.
 */
export function cardSummary(relations: Relations | undefined): string | null {
  if (!relations) return null;
  if (relations.blocked_by.length) {
    return `Blocked by ${relations.blocked_by.length}`;
  }
  if (relations.progress.total > 0) return progressLabel(relations.progress);
  return null;
}
