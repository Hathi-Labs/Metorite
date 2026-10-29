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

import type { AgentFocus } from "./agentRuns";
import { type Filters, type SubtaskMode, toQuery } from "./grouping";
import { statusLaneOf } from "./statusOrder";
import { subtaskReadParams } from "./subtaskView";
import { type TableSort, sortQuery } from "./table";

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

/**
 * The question the board, the list and the table ask `GET /projects/tasks`,
 * as a parameter bag. `loadProject` reads page 1 and `loadMoreTasks` reads
 * page N from this ONE builder, so the two cannot drift.
 *
 * ⚠️ D-PM-38: `subtasks` is the STORED Subtasks mode. Hidden adds
 * `top_level: true` (`subtaskView.subtaskReadParams`), so the server pages
 * over top-level tasks only. Fence: `paging.test.ts`.
 *
 * ⚠️ **The three agent filters travel to the server too** (task #986).
 * "Waiting on me", "Agent work" and the strip's focus used to filter the rows
 * of page 1 in the browser, so a strip that said "2 Working" drew one card
 * while the bar said "100 of 801". Paging happens in SQL, so each is a
 * parameter: `agent_focus` (the strip's focus, with "waiting" for "Waiting on
 * me") and `agent_worked`. Both are part of the params, so they are part of
 * the read key: a change of focus is a different question and starts a fresh
 * read, and "Load more" and the column reads carry them on.
 */
export function boardReadParams(input: {
  projectId: string;
  filters: Filters;
  sort: TableSort | null;
  /** The order-bearing view, or null while the views read is in flight. */
  viewId: string | null;
  subtasks: SubtaskMode | null;
  /** The "Waiting on me" toggle. It wins over `agentFocus`, as the page sets one at a time. */
  waitingOnMe: boolean;
  /** The strip's focus, or null. */
  agentFocus: AgentFocus | null;
  /** The "Agent work" toggle. */
  agentWork: boolean;
}): Record<string, string | number | boolean> {
  return {
    project_id: input.projectId,
    include_subtree: true,
    page_size: TASK_PAGE_SIZE,
    ...toQuery(input.filters),
    // WS-27x — the table's header sort; {} when none.
    ...sortQuery(input.sort),
    // H-64. The view whose hand-arranged order to read back. Omitted, never
    // `undefined`, while views are loading: `cacheKey` sorts the params into
    // the read key, and a present-but-undefined entry is a different key.
    ...(input.viewId ? { view_id: input.viewId } : {}),
    ...subtaskReadParams(input.subtasks),
    // Task #986. Omitted when off, so a board without them keeps its read key.
    ...agentReadParams(input.waitingOnMe, input.agentFocus, input.agentWork),
    // #918. The true size of each status lane, so a column counts the whole
    // filtered set and not only the rows of one page.
    with_status_counts: true,
  };
}

/**
 * The `agent_focus` the server is asked for: "waiting" while "Waiting on me"
 * is on, else the strip's focus, else none. The server takes ONE focus, so the
 * page clears the other when it sets one (`onWaitingOnMe`, `onAgentFocus`).
 */
export function agentFocusParam(waitingOnMe: boolean, agentFocus: AgentFocus | null): AgentFocus | null {
  return waitingOnMe ? "waiting" : agentFocus;
}

/**
 * The two agent parameters of `GET /projects/tasks` AND of the CSV export
 * (`export.ts`), from the three states of the page. ONE builder, so the file
 * and the board cannot disagree. Empty while all three are off.
 */
export function agentReadParams(
  waitingOnMe: boolean,
  agentFocus: AgentFocus | null,
  agentWork: boolean,
): Record<string, string | boolean> {
  const focus = agentFocusParam(waitingOnMe, agentFocus);
  return {
    ...(focus ? { agent_focus: focus } : {}),
    ...(agentWork ? { agent_worked: true } : {}),
  };
}

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


// ── #918: the true size of each column, and paging per column ────────────

/** The server's answer for the lane sizes (`with_status_counts`). */
export interface StatusCounts {
  counts: Readonly<Record<string, number>>;
  /** status id -> its stage, for a lane the board does not draw. */
  categories: Readonly<Record<string, string | null>>;
}

/**
 * The true size of each column on a board grouped by status or by stage.
 *
 * A status id the board draws counts in its own column. A subproject's own
 * lane counts in the column it draws in (`statusLaneOf`, #834). By stage, a
 * column is the sum of every lane of that stage. Null when the server sent no
 * counts or the board groups by any other axis: the column then counts the
 * rows it holds, as before.
 */
export function columnSizes(
  groupBy: string,
  lanes: readonly { id: string; category: string; position: number }[],
  sc: StatusCounts | null,
): Map<string, number> | null {
  if (!sc || (groupBy !== "status" && groupBy !== "category")) return null;
  const laneOf = statusLaneOf(lanes);
  const byId = new Map(lanes.map((l) => [l.id, l]));
  const out = new Map<string, number>();
  for (const [statusId, n] of Object.entries(sc.counts)) {
    const category = byId.get(statusId)?.category ?? sc.categories[statusId] ?? null;
    const key = groupBy === "status"
      ? laneOf({ status_id: statusId, status_category: category })
      : category;
    if (!key) continue;
    out.set(key, (out.get(key) ?? 0) + n);
  }
  return out;
}

/**
 * The filter that reads more rows of ONE column, or null when one filter
 * cannot name the whole column. A status column that also holds a
 * subproject's own lane needs two status ids, and the read takes one, so it
 * falls back to the board's own "Load more".
 */
export function columnReadFilter(
  groupBy: string,
  columnKey: string,
  lanes: readonly { id: string; category: string; position: number }[],
  sc: StatusCounts | null,
): Record<string, string> | null {
  if (groupBy === "category") return { status_category: columnKey };
  if (groupBy !== "status" || !sc) return null;
  const laneOf = statusLaneOf(lanes);
  const byId = new Map(lanes.map((l) => [l.id, l]));
  const members = Object.keys(sc.counts).filter((id) =>
    laneOf({ status_id: id, status_category: byId.get(id)?.category ?? sc.categories[id] ?? null })
      === columnKey);
  return members.every((id) => id === columnKey) ? { status_id: columnKey } : null;
}

/**
 * The page of the board's own read to ask for next.
 *
 * ⚠️ A column read adds rows that the board's pages have not reached, so
 * `tasks.length` is no longer a prefix of the board's order, and a page
 * counted from it would skip rows. The board's pages hold `held - laneOnly`
 * rows: every row except the ones only a column read brought.
 */
export function nextBoardPage(held: number, laneOnly: number, pageSize: number = TASK_PAGE_SIZE): number {
  return nextTaskPage(Math.max(0, held - laneOnly), pageSize);
}

/**
 * How many rows the "Load N more" at a column's foot names: what one read of
 * that column can bring, or 0 when the column holds its whole lane.
 */
export function columnMoreCount(held: number, total: number | undefined, pageSize: number = TASK_PAGE_SIZE): number {
  if (total === undefined || total <= held) return 0;
  return Math.min(total - held, Math.max(pageSize, 1));
}
