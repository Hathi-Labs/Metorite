/**
 * Projects · reading a board that is bigger than one page.
 *
 * ## The defect this exists to remove
 *
 * `GET /projects/tasks` caps a page at 100 rows (`Page` in
 * `routes/projects/core.py`, a hard `le=MAX_PAGE_SIZE`), answers `total` for
 * the whole filtered set, and returns **no cursor**. `loadProject` asked for
 * one page and drew whatever came back.
 *
 * So a project with 150 tasks drew 100 of them and said nothing. Measured
 * 2026-09-22 against a real gateway: the board and the list rendered tasks
 * #51–#150, the lane header read "To do 100", and scrolling to the bottom
 * issued no further request. Tasks #1–#50 could not be reached from the board,
 * the list or the table at all.
 *
 * ⚠️ **The same screen contradicted itself.** The sidebar row and the overview
 * both read the true 150 — they come from `nodes/{id}/summary`, which counts in
 * SQL — and the timeline read 150 too, because it loads from `/calendar`, which
 * is a different endpoint with a different cap. One store, three lenses
 * (D52/D53/D54), and the lenses disagreed about how much work exists.
 *
 * ## Why this module exists at all, rather than a bigger cap
 *
 * Raising `MAX_PAGE_SIZE` moves the cliff, it does not remove it. The cap is
 * the server refusing to hand back an unbounded result set, which is correct.
 * What was missing is the client admitting that it holds a page.
 *
 * The repo already had the answer in two places and this is the third:
 *
 * * `projectsApi.calendar` takes a `truncated` flag from the server precisely
 *   so the month "can say so rather than present a plausible-looking short
 *   month".
 * * `export/tasks.csv` REFUSES a filter wider than its row cap, 422 with the
 *   matched count, rather than writing a partial file.
 * * H-76 fixed the same shape in the Customer Console: it returns `total` and
 *   the page says "100 of 563". **The truncation is never silent.**
 *
 * ## Why the functions are here and not in the component
 *
 * `vitest.config.ts` is `environment: "node"` and does not collect `.tsx`
 * (D-PM-21), so a rule written inside a component has no fence. Every decision
 * this feature makes therefore lives in this file, and `MoreTasksBar` only
 * draws what these return.
 */

/**
 * Rows one read of `GET /projects/tasks` can return.
 *
 * ⚠️ **This mirrors the server's `MAX_PAGE_SIZE` and must not drift from it.**
 * `paging.test.ts` pins the two together by reading the gateway's own source,
 * for the reason today already cost us once: a client that asks for more than
 * the server's `le=` allows gets a 422 on EVERY read, and the panel that did
 * that shipped green because the live harness calls handlers directly and the
 * visual rig stubs the API.
 */
export const TASK_PAGE_SIZE = 100;

/** Is the server holding rows this client has not asked for yet? */
export function hasMoreTasks(loaded: number, total: number | null): boolean {
  if (total === null) return false;
  return loaded < total;
}

/**
 * The 1-based page to ask for next.
 *
 * ⚠️ Derived from how many rows are HELD, not from a page counter. A counter
 * and a row list are two facts about one thing, and they come apart the first
 * time a read fails half way — then "page 3" asks past a gap nobody filled.
 */
export function nextTaskPage(loaded: number, pageSize: number = TASK_PAGE_SIZE): number {
  if (pageSize <= 0) return 1;
  return Math.floor(loaded / pageSize) + 1;
}

/**
 * What the board says about holding a page, or `null` when it holds it all.
 *
 * Plain counts, because the reader's question is "is anything missing" and the
 * answer is two numbers. `null` when nothing is missing — the bar must not
 * occupy a row on the ordinary board, which is nearly every board.
 */
export function truncationNote(loaded: number, total: number | null): string | null {
  if (!hasMoreTasks(loaded, total)) return null;
  return `Showing ${loaded} of ${total} tasks.`;
}

/**
 * How many rows the next read will actually bring back.
 *
 * Named on the button ("Load 50 more") rather than left as a bare "Load more",
 * because the member's next question after "showing 100 of 150" is how much of
 * the gap one press closes. It is `min(remaining, one page)` — a 400-task
 * board closes 100 of the gap per press, not 300.
 */
export function nextBatchSize(
  loaded: number,
  total: number | null,
  pageSize: number = TASK_PAGE_SIZE
): number {
  if (!hasMoreTasks(loaded, total)) return 0;
  return Math.min((total as number) - loaded, Math.max(pageSize, 1));
}

/**
 * Fold a freshly read page onto the rows already held.
 *
 * ⚠️ **De-duplicated by id, and the HELD row wins.** Two reasons, and the
 * second is the one that bites:
 *
 * 1. Offset paging is racy by construction. Somebody adding a task between
 *    page 1 and page 2 shifts every later row down by one, so a row from page 1
 *    legitimately arrives again on page 2. Appending blindly would draw it
 *    twice, and React would warn about the duplicate key.
 * 2. A held row may carry an **optimistic edit** that the server has not
 *    answered for yet. `setTasks((current) => …)` is how every write on this
 *    page repaints, so taking the incoming copy would silently undo an edit
 *    the member can see on screen.
 *
 * ⚠️ What this CANNOT fix: that same shift can push a row from page 2 up into
 * page 1's range after page 1 was read, so it appears on neither page. Only
 * keyset paging removes that, and the endpoint has no cursor. It is a missed
 * row on a racing board, not a silent 50-row hole, and the note still reports
 * the true `total`.
 */
export function appendTasks<T extends { id: string }>(
  current: readonly T[],
  incoming: readonly T[]
): T[] {
  const held = new Set(current.map((row) => row.id));
  return [...current, ...incoming.filter((row) => !held.has(row.id))];
}
