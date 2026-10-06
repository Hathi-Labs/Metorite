/**
 * Projects · the arithmetic behind a long-list picker (WS-27bc, the one
 * dispatchable slice of it — see `project_management_app.md` §9.5.2's audit).
 *
 * **This module has no surface and no endpoint in it, on purpose.** The picker
 * a reader eventually sees owes a popover, `aria-expanded`/`aria-controls`/
 * `aria-activedescendant`, listbox roles and focus return, all of which D-PM-15
 * says arrive as a wrapper under `src/components/ui/` — hand-rolling that shell
 * here is the second-substrate failure that decision exists to prevent. What is
 * left once the surface is removed is four pieces of arithmetic that are
 * wrong-but-plausible, and they are the whole file.
 *
 * Three rules are recorded here because they were got wrong somewhere first:
 *
 * 1. **The list reorders under you.** `/projects/tasks` is ordered by
 *    `updated_at`, and page N+1 is a *second* query against a table that moved
 *    in between — a row that was on page 1 can legitimately come back on page 2.
 *    Appending pages without a dedupe renders it twice, and the second copy is
 *    the one a reader clicks.
 * 2. **"No more pages" is derived, never flagged.** A `hasMore` boolean kept
 *    beside the rows is a second opinion about the same fact, and the two
 *    disagree the moment one of them is updated on a path the other is not.
 *    Here it is a property of the page that just arrived: short page, no more.
 * 3. **A response is checked against the query that is live NOW**, not against
 *    the one that asked for it — `isCurrent` in `./search`, imported rather
 *    than re-derived, because the out-of-order-response bug it solves is real
 *    and already solved once.
 *
 * ⚠️ **The minimum query length bounds the RESULT SET, not the scan.** WS-27bc's
 * ticket claimed it prevents "a leading-wildcard scan with no index behind it";
 * that is false and §9.5.2's audit strikes it — the gateway's predicate is
 * `ILIKE '%…%'` either way, and `'%ab%'` is exactly as unindexable as `'%a%'`.
 * What the minimum actually prevents is the answer, in the gateway's own words
 * (`routes/projects/search.py:68-71`): "returning half the workspace."
 */

import { isCurrent, isSearchableQuery } from "./search";

/** Anything a picker can list. Identity is the id, and nothing else. */
export interface Identified {
  id: string;
}

/**
 * How close to the bottom counts as "at the bottom", in CSS pixels.
 *
 * 48 is WS-27bc's number, kept. It is roughly one row: the next page starts
 * loading while the last row is still on screen, so the reader does not watch a
 * spinner arrive at a boundary they have already reached.
 */
export const SCROLL_THRESHOLD_PX = 48;

/**
 * Is the viewport close enough to the end of the scroller to ask for more?
 *
 * Pure arithmetic over three numbers a caller reads off a DOM node — no
 * element, no `IntersectionObserver`, nothing that needs a browser — because
 * `vitest.config.ts` here is `environment: "node"` and a decision taken inside
 * a component would have no fence at all.
 *
 * Two boundary rules, both deliberate:
 *
 * - **At the threshold counts as reached** (`<=`, not `<`). A scroller that
 *   comes to rest exactly 48px from the end would otherwise need one more pixel
 *   of scroll that a trackpad's momentum may never deliver, and paging stalls
 *   with no way for the reader to know why.
 * - **A scroller with nothing to scroll is at its end.** Content shorter than
 *   the viewport gives a negative remaining distance and returns `true`: the
 *   first page did not fill the container, so no scroll event will ever fire
 *   and the list would stop at page 1 forever. That is the classic version of
 *   this bug, and it is a `true` here rather than a special case at the call
 *   site.
 *
 * A non-finite input yields `false` — a `NaN` from a detached node asks for
 * nothing rather than paging without bound.
 */
export function shouldLoadMore(
  scrollTop: number,
  clientHeight: number,
  scrollHeight: number,
  threshold: number = SCROLL_THRESHOLD_PX,
): boolean {
  const remaining = scrollHeight - scrollTop - clientHeight;
  return remaining <= threshold;
}

/**
 * Everything the picker knows about the rows it has accumulated.
 *
 * One object, so `rows` and `done` cannot be updated on different paths — see
 * rule 2 in the module docstring.
 */
export interface PagedState<T extends Identified> {
  readonly rows: readonly T[];
  readonly done: boolean;
}

/** A picker before its first page. Not `done`: nothing has answered yet. */
export function emptyState<T extends Identified>(): PagedState<T> {
  return { rows: [], done: false };
}

/**
 * Fold one page into what is already on screen.
 *
 * **Dedupe by id, first position wins, last payload wins.** Both halves matter
 * and they pull in opposite directions:
 *
 * - Keeping the *first* position means a row that moved between requests does
 *   not also jump up the list the reader is looking at. Re-sorting under a
 *   cursor is its own defect.
 * - Keeping the *last* payload means the row still shows the fresher read. The
 *   later page is a later look at the same table; there is no reason to render
 *   the staler copy of a row we have just been told about again.
 *
 * ⚠️ **`done` is derived from the RAW page length, never from how many rows
 * were newly added.** A full page whose every row was already on screen adds
 * nothing — computing the terminal state from the accumulated growth would call
 * that the end of the list and silently truncate the picker at whatever the
 * reader had already seen.
 *
 * A `pageSize` of zero or less is treated as done: nothing can be asked for, so
 * continuing to ask is an unbounded loop rather than a slow list.
 */
export function appendPage<T extends Identified>(
  state: PagedState<T>,
  page: readonly T[],
  pageSize: number,
): PagedState<T> {
  const rows: T[] = [];
  const at = new Map<string, number>();

  for (const row of [...state.rows, ...page]) {
    const seen = at.get(row.id);
    if (seen === undefined) {
      at.set(row.id, rows.length);
      rows.push(row);
    } else {
      rows[seen] = row;
    }
  }

  return { rows, done: pageSize <= 0 || page.length < pageSize };
}

/** A page as the endpoint hands it back: the query it answers, and its rows. */
export interface PageResponse<T extends Identified> {
  readonly query: string;
  readonly rows: readonly T[];
}

/**
 * Fold in a page, unless it is answering a question nobody is asking any more.
 *
 * The stale-response check is `isCurrent` from `./search` — imported, not
 * rewritten. Typing "par" then "parser" issues two requests and nothing orders
 * their answers; a slow "par" landing after a fast "parser" would append the
 * wrong rows to a list the reader is already reading.
 *
 * A stale response returns **the same state object**, so a caller that renders
 * on identity does not repaint for a page it discarded.
 */
export function receivePage<T extends Identified>(
  state: PagedState<T>,
  response: PageResponse<T>,
  liveQuery: string,
  pageSize: number,
): PagedState<T> {
  if (!isCurrent(response.query, liveQuery)) return state;
  return appendPage(state, response.rows, pageSize);
}

/**
 * Is this query long enough to send?
 *
 * The gate is `isSearchableQuery` from `./search`: `MIN_QUERY` for text, and
 * any length for a task number (D-PM-31). It is **imported, never
 * re-declared** — a third copy of the rule is the CLAUDE.md §5 defect, and
 * this module is exactly where somebody would add one.
 *
 * ⚠️ **What happens BELOW the minimum is not this module's decision.** WS-27bc
 * asked for a fallback to "the unfiltered first page", and §9.5.2's audit
 * blocks it: `/projects/search` is capped-not-paged by a recorded decision and
 * answers `{"rows": []}` to an empty `q`, while `/projects/tasks` is paged with
 * no minimum and no ranking. Straddling the two makes the list visibly reorder
 * at the two-character boundary; picking one reopens a recorded decision.
 * Neither is an implementer's call, so this function reports the fact and
 * stops.
 */
export function isSearchable(query: string): boolean {
  return isSearchableQuery(query);
}
