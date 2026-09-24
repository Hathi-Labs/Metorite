// /tasks · group-context quick-add (the WS-27y pattern, backported).
//
// A quick-add lives inside a group — a board column, a grouped-list section —
// and the task it creates must LAND in that group, or the add reads as a
// failure while the task sits in some default bucket off-screen. The mapping
// from "where the input is" to "what the created item must carry" is this
// module, pure and surface-agnostic, exactly like `app/projects/lib/quickAdd`
// on the other side of the wall: the payloads differ (the Tasks item speaks
// workflowStage/context/energy, `pm_tasks` speaks status_id/tags), the
// grammar is the same.
//
// One divergence from the Projects mapping, and it is deliberate: /tasks has
// COMPUTED axes. The priority and mode groups are projections of
// important × leveraged × urgent-from-dueAt (`priority.ts`), so no create
// payload can promise the new task lands in "Critical" — urgency needs a due
// date nobody typed. Where Projects answers an unknown axis with "a plain add,
// never an error", these axes answer `null` — the surface offers no quick-add
// there at all — because in a grouped list a plain add that visibly files
// itself into a SIBLING group is not a plain add, it is a lie about where the
// task went.

import type { GroupBy } from "./ordering";
import { NO_CONTEXT_GROUP } from "./priority";
import type { Disposition, Energy, ViewKey } from "./types";
import { type NextCategory, isNextCategory } from "./statusCategory";

/** What a quick-added task must carry to belong to its group. */
export interface QuickAddPrefill {
  /** The Next Actions group (D73.9): a status category, never a lane name. */
  statusCategory?: NextCategory;
  context?: string;
  energy?: Energy;
  deepWork?: boolean;
  /** The GTD bucket the add lands in. Absent = the default NEXT. */
  disposition?: Disposition;
}

/** The axes a /tasks quick-add can sit inside: the status axis (`""`, the
 *  grouped list's default and the board's columns) or a toolbar lens. */
export type QuickAddAxis = GroupBy | "";

const ENERGIES: ReadonlySet<string> = new Set(["low", "medium", "high"]);

/**
 * (axis, group key) → the prefill, or `null` when the group cannot honestly
 * take a quick-add (a computed axis — see the header).
 *
 * The unset buckets ("No context", "No energy set", "Shallow") ask for
 * nothing: a bare next action is already context-less, energy-less and
 * shallow, so "create it in this group" is what a bare create does.
 */
export function quickAddPrefill(
  axis: QuickAddAxis,
  key: string,
): QuickAddPrefill | null {
  switch (axis) {
    case "":
      // The status axis: the board's columns and the grouped list's
      // category sections. The key IS the category (D73.9).
      return isNextCategory(key) ? { statusCategory: key } : {};
    case "context":
      return key === NO_CONTEXT_GROUP ? {} : { context: key };
    case "energy":
      return ENERGIES.has(key) ? { energy: key as Energy } : {};
    case "depth":
      return key === "deep" ? { deepWork: true } : {};
    case "none":
      return {};
    case "priority":
    case "mode":
      // Computed from flags + due date — a create cannot promise the landing.
      return null;
  }
}

/**
 * WS-27ad — the quick-add for a FLAT view, whose "group" is the view itself.
 *
 * The Done / Someday / Waiting / Archive lists had no capture box at all, so
 * the only way to put something on your Someday list was to capture it into the
 * Inbox and clarify it there — two steps for a thought that was already
 * classified when you had it.
 *
 * Same null grammar as the axes above: a view that cannot honestly take the add
 * offers no box, and each refusal has a reason rather than an omission.
 */
export function viewQuickAdd(
  view: ViewKey,
): { prefill: QuickAddPrefill; label: string } | null {
  switch (view) {
    case "someday":
      // The clean case: "incubate this" is a complete decision, and Someday is
      // exactly where an incubating thought belongs.
      return { prefill: { disposition: "SOMEDAY" }, label: "Add to Someday" };
    case "done":
      // A log entry, not a to-do — the same thing the board already means when
      // you quick-add into the last stage (see `quickAddNext`). Worth having:
      // recording work that was never a task is otherwise impossible here.
      return { prefill: { disposition: "DONE" }, label: "Log something done" };
    case "waiting":
      // NO box. Waiting-For is grouped by PERSON and a waiting-for's whole
      // content is who owes it and since when; a create can set the bucket but
      // not the delegation (that is the delegate path, which asks for the
      // person and stamps `delegatedAt`). An add here would visibly file itself
      // into "Unassigned" — a sibling group — which this module's header calls
      // what it is: a lie about where the task went.
      return null;
    case "archive":
      // Creating a task in order to immediately hide it is not a gesture.
      return null;
    default:
      // Inbox has its own always-open capture row; next/priority/engage are
      // grouped surfaces served by `quickAddPrefill`; calendar days get their
      // own per-day box.
      return null;
  }
}

// ── `#` — capture straight onto a project (S6g) ──────────────────────────────

/** One place a capture can land: an Area (mine) or a company project. */
export interface CaptureDestination {
  id: string;
  name: string;
  kind: "area" | "project";
}

/** What `parseProjectToken` read out of a capture line. */
export interface ProjectTokenParse {
  /** The line with the `#` token removed and the spaces tidied. */
  title: string;
  /** The destination the token named, when exactly one matched. */
  match?: CaptureDestination;
  /** The text after `#`, when a token was present, matched or not. */
  query?: string;
}

/**
 * The shortest start of a name that may name it (S6g repair P2-c). One letter
 * is too easy to type by accident, and "#a" filing a task onto the only
 * project starting with A publishes it. An exact name of any length still
 * matches.
 */
export const MIN_PREFIX = 2;

/** Where a `#` token starts: at the line's start, or after whitespace. */
const TOKEN_START = /(^|\s)#(\S)/;

/**
 * Read a `#Name` token out of a capture line (my_tasks_cutover.md §5 S6g).
 *
 * The name may hold spaces ("#Printer v3"), so the reader takes the LONGEST
 * run of words after `#` that names exactly one destination. A run matches
 * when it equals a name, ignoring case, or when it is the start of exactly one
 * name. An exact name wins over a prefix of a longer one. The words after the
 * run go back into the title, so "#print fix the jam" files "fix the jam".
 * A prefix needs at least `MIN_PREFIX` characters.
 *
 * Only the first `#` at a token start counts. A `#` inside a word ("C#") is
 * text. A token that names nothing, or names two things, leaves the line
 * alone and returns only its `query`, so the picker can ask.
 */
export function parseProjectToken(
  text: string,
  destinations: readonly CaptureDestination[],
): ProjectTokenParse {
  const found = TOKEN_START.exec(text);
  if (!found) return { title: text.trim() };
  const hashAt = found.index + found[1].length;
  const before = text.slice(0, hashAt);
  const after = text.slice(hashAt + 1);
  const words = after.split(/\s+/).filter(Boolean);
  const query = words.join(" ");
  const lower = (s: string) => s.trim().toLowerCase();

  for (let k = words.length; k >= 1; k--) {
    const phrase = lower(words.slice(0, k).join(" "));
    const exact = destinations.filter((d) => lower(d.name) === phrase);
    const hit =
      exact.length === 1
        ? exact[0]
        : exact.length === 0 && phrase.length >= MIN_PREFIX
          ? (() => {
              const starts = destinations.filter((d) => lower(d.name).startsWith(phrase));
              return starts.length === 1 ? starts[0] : undefined;
            })()
          : undefined;
    if (hit) {
      const rest = words.slice(k).join(" ");
      const title = `${before} ${rest}`.replace(/\s+/g, " ").trim();
      return { title, match: hit, query };
    }
  }
  return { title: text.trim(), query };
}

/**
 * The `#` fragment the member is typing right now, if the caret sits in one.
 * The capture box opens its picker while this is non-null, filtered by it.
 */
export function openHashQuery(text: string): string | null {
  const m = /(?:^|\s)#([^#]*)$/.exec(text);
  return m ? m[1] : null;
}

/** The line with the `#` fragment at its end removed, for a picker pick. */
export function stripOpenHash(text: string): string {
  return text.replace(/(^|\s)#[^#]*$/, "$1").replace(/\s+$/, "");
}
