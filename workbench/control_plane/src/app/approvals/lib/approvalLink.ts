/**
 * The link to one proposal in the approval queue: `/approvals?action=<id>`
 * (WS-58 MD-3a, `my_day_feed.md` §5.8 item 9).
 *
 * The My Day feed builds the link for its approval card (`approval_href` in
 * `gateway/routes/shell/needs.py`). The needs row of the bell keeps a bare
 * `/approvals`, because nothing in the bell changes (§4.4). This file reads
 * the id back. The page scrolls to that row and marks it.
 *
 * The id must be a UUID, the key of `pending_actions`. Anything else is
 * ignored, and the page opens the queue as it does today.
 *
 * Fence: `approvalLink.test.ts`.
 */

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The query name of the link. The gateway writes it by this name. */
export const ACTION_PARAM = "action";

/** The proposal id a query string names, lower-case, or `null`. */
export function actionIdFromSearch(
  search: { get(name: string): string | null } | null,
): string | null {
  const raw = search?.get(ACTION_PARAM)?.trim() ?? "";
  return UUID.test(raw) ? raw.toLowerCase() : null;
}

/** The DOM id of one row, so the page can scroll to it. */
export function approvalRowDomId(id: string): string {
  return `approval-${id}`;
}

/**
 * Which row the link marks, and whether the proposal left the queue.
 *
 * `marked` is the id of the row as the queue spells it. `gone` is true only
 * after the queue has loaded and holds no row with that id: a person already
 * approved or rejected it. Until the first load, nothing is gone.
 */
export function markedRow(
  rows: readonly { id: string }[],
  actionId: string | null,
  fetched: boolean,
): { marked: string | null; gone: boolean } {
  if (!actionId) return { marked: null, gone: false };
  const row = rows.find((r) => r.id.toLowerCase() === actionId);
  return { marked: row ? row.id : null, gone: fetched && !row };
}
