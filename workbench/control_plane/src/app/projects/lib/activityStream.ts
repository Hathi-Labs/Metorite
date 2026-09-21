/**
 * Projects · the task panel's two streams — what each entry SAYS, and how the
 * two lists are shaped.
 *
 * ## Why this is a module and not four functions inside `TaskPanel.tsx`
 *
 * `describe` lived in the panel and had no test. It is the function that turns
 * a row of `pm_activities` into the sentence a member reads about their own
 * work, and it was rendering `Edited due_at, start_date` — nine times in a
 * row, in the screenshot the owner sent on 2026-09-21. A panel-local helper is
 * a helper nobody can check.
 *
 * `vitest.config.ts` is `environment: "node"`, so nothing here may touch the
 * DOM or import React. That is the constraint that makes these testable, and
 * it is why the shaping lives here while the panel only draws.
 *
 * ## The four owner requests this file answers
 *
 * 1. *"shouldn't we have human-readable names for the names of the fields"* —
 *    {@link describeActivity}, through `changeLabel`.
 * 2. *"display only the last 5 or so activity updates, and roll up everything
 *    else"* — {@link ROLLUP_AT} and {@link rollUp}.
 * 3. *"separate out activity and comments … two separate systems"* — the
 *    `kind` filter on the timeline endpoint, and {@link isComment} for the
 *    client half.
 * 4. *"enable threaded comments … just one layer of reply"* —
 *    {@link threadComments}.
 */
import type { ActivityRow } from "./api";
import { changeLabel, type FieldDef } from "./customFields";

// ── What an entry says ─────────────────────────────────────────────────────

/**
 * One activity row → the line the timeline prints.
 *
 * ⚠️ **Moved here from `TaskPanel.tsx` unchanged except for the default
 * branch**, which used to fall through to `activity.type` — so an activity
 * type added in Python and not here reached the member as the bare word
 * `agent_run`. `ACTIVITY_TYPES` in `core.py` is the vocabulary, and this is a
 * mirror of it; {@link ACTIVITY_VERBS} keeps the mirror's failure legible
 * rather than raw.
 */
export function describeActivity(
  activity: ActivityRow,
  defs: FieldDef[] = [],
): string {
  const meta = (activity.meta ?? {}) as Record<string, unknown>;
  switch (activity.type) {
    case "comment":
      return activity.body ?? "";
    case "status_change":
      return activity.body ?? "Status changed";
    case "assignment": {
      const added = (meta.added as string[] | undefined) ?? [];
      const removed = (meta.removed as string[] | undefined) ?? [];
      const parts: string[] = [];
      if (added.length) parts.push(`assigned ${added.join(", ")}`);
      if (removed.length) parts.push(`unassigned ${removed.join(", ")}`);
      return parts.join("; ") || "Assignment changed";
    }
    case "field_change": {
      const changes = (meta.changes as Array<{ field: string }> | undefined) ?? [];
      const named = changes.map((c) => changeLabel(c.field, defs));
      return `Edited ${named.join(", ") || "fields"}`;
    }
    case "agent_run":
      return `Agent run ${String(meta.agent ?? "")}`.trim();
    case "sync":
      return activity.body ?? "Synced";
    case "attachment":
      return activity.body ?? "Attachment changed";
    case "mention": {
      const who = (meta.mentioned as string[] | undefined) ?? [];
      return who.length ? `Mentioned ${who.join(", ")}` : "Mentioned somebody";
    }
    case "link":
      return activity.body ?? "Link changed";
    default:
      // ⚠️ Never the raw `type`. A word like `agent_run` in front of a member
      // is the same defect as `due_at` was, one layer up.
      return activity.body ?? ACTIVITY_VERBS(activity.type);
  }
}

/** An unknown activity type, made readable rather than printed raw. */
const ACTIVITY_VERBS = (type: string): string => {
  const words = String(type || "").replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "Changed";
};

// ── The two streams ────────────────────────────────────────────────────────

/**
 * Is this row something a person wrote, or something that happened?
 *
 * ⚠️ **One predicate, and the server's `kind` filter is its other half.** The
 * endpoint narrows by `type = 'comment'` / `type <> 'comment'`; this is the
 * same cut written once for the client. Two different definitions of "a
 * comment" is how a row ends up in neither list.
 */
export const isComment = (row: ActivityRow): boolean => row.type === "comment";

// ── Threading ──────────────────────────────────────────────────────────────

/** A root comment and the replies under it, oldest reply first. */
export interface Thread {
  root: ActivityRow;
  replies: ActivityRow[];
}

/**
 * A page of comments → threads, one level deep.
 *
 * ⚠️ **An orphan is PROMOTED, never dropped**, and that is the case worth
 * reading the code for. A reply arrives without its root in two ordinary
 * situations, neither of them an error:
 *
 * * The root is older than the page boundary. The timeline is ordered by
 *   time, so a thread can straddle it.
 * * The root was deleted. A soft-deleted comment is withheld from the
 *   timeline, and migration 208 deliberately does NOT cascade — one person
 *   tidying up their own words must not destroy somebody else's reply.
 *
 * Dropping such a row would lose a member's writing from the product with no
 * error anywhere. So it becomes a root of its own.
 *
 * Roots come back NEWEST first, matching the endpoint's order and the rest of
 * the panel. Replies inside a thread run OLDEST first, because a conversation
 * reads downwards.
 */
export function threadComments(rows: readonly ActivityRow[]): Thread[] {
  const comments = rows.filter(isComment);
  const present = new Set(comments.map((row) => row.id));

  const threads: Thread[] = [];
  const byId = new Map<string, Thread>();
  for (const row of comments) {
    const parent = row.parent_id ?? null;
    if (parent === null || !present.has(parent)) {
      const thread: Thread = { root: row, replies: [] };
      threads.push(thread);
      byId.set(row.id, thread);
    }
  }
  for (const row of comments) {
    const parent = row.parent_id ?? null;
    if (parent === null || !present.has(parent)) continue;
    // ⚠️ `byId` holds ROOTS only. A reply whose parent is itself a reply
    // cannot happen — `add_comment` refuses it — but a client must not fall
    // over if one ever does, so it is promoted like any other orphan.
    const thread = byId.get(parent);
    if (thread) thread.replies.push(row);
    else byId.set(row.id, pushRoot(threads, row));
  }
  for (const thread of threads) {
    thread.replies.sort((a, b) => stamp(a) - stamp(b));
  }
  return threads;
}

function pushRoot(threads: Thread[], row: ActivityRow): Thread {
  const thread: Thread = { root: row, replies: [] };
  threads.push(thread);
  return thread;
}

const stamp = (row: ActivityRow): number => {
  const at = row.created_at ? Date.parse(row.created_at) : NaN;
  return Number.isNaN(at) ? 0 : at;
};

// ── The roll-up ────────────────────────────────────────────────────────────

/**
 * How many activity entries the panel shows before it folds the rest away.
 *
 * Owner request, 2026-09-21: *"display only the last 5 or so activity updates,
 * and roll up everything else"*. Five, because the screenshot that prompted it
 * held nine consecutive `Edited due_at, start_date` rows and nothing else was
 * reachable without scrolling past them.
 *
 * ⚠️ Applied to the ACTIVITY list only. Comments are the thing a person came
 * to read, and hiding the older half of a conversation behind a button is the
 * opposite of what splitting the two lists was for.
 */
export const ROLLUP_AT = 5;

export interface RolledUp<T> {
  /** What to draw now. */
  shown: T[];
  /**
   * How many more exist. ⚠️ Counts rows the SERVER holds, not only the ones
   * in hand — the panel's button says "Show 45 older", and computing that
   * from the page alone would promise five when there are fifty.
   */
  hidden: number;
}

/**
 * The first `ROLLUP_AT` entries, plus an honest count of the rest.
 *
 * `total` is the endpoint's count for the same `kind`. Pass it and the hidden
 * count covers rows beyond this page; omit it and it covers only what is here.
 * Expanded, everything in hand is shown — and `hidden` then reports what is
 * still on the server, which is what tells the panel whether to fetch more.
 */
export function rollUp<T>(
  rows: readonly T[],
  { expanded = false, limit = ROLLUP_AT, total }: {
    expanded?: boolean;
    limit?: number;
    total?: number;
  } = {},
): RolledUp<T> {
  const held = total === undefined ? rows.length : Math.max(total, rows.length);
  if (expanded) return { shown: [...rows], hidden: held - rows.length };
  return { shown: rows.slice(0, limit), hidden: Math.max(held - limit, 0) };
}
