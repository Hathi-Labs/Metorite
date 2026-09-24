/**
 * My Tasks · the one inbox (my_tasks_cutover.md §5 S6g).
 *
 * Two kinds of task reach the Inbox, and until S6g two components drew them:
 *
 * - a **personal** row: a capture in my own tree (the personal root or one of
 *   my Areas), with the INBOX disposition;
 * - a **board** row: a task on a company board. Either a colleague assigned it
 *   to me and I have not triaged it yet (`fromProjectIds`, S6e), or it is an
 *   INBOX row that already lives on a board.
 *
 * This module is the one rule for "what the Inbox holds", "which kind a row
 * is", and "in what order". The sidebar badge, the header count, the list, the
 * keyboard walk and the filter pills all read it, so the badge and the header
 * cannot disagree again (they did: the badge counted board rows and the header
 * did not).
 *
 * Fences: `inbox.test.ts`.
 */

import { isPersonalTask } from "./clarify";
import { DELETE_LABEL, REMOVE_LABEL } from "./removal";
import type { MyTask } from "./types";
import { isTickled } from "./utils";

/** Which kind an Inbox row is. The origin marker and the actions follow it. */
export type InboxKind = "personal" | "board";

/** The Inbox's source filter. `all` shows both kinds. */
export type InboxSource = "all" | "personal" | "board";

/** What decides the kind of a row: my root and my Areas (`isPersonalTask`). */
export interface InboxScope {
  personalRootId: string | null;
  areaIds: readonly string[];
}

/** The kind of one row. `isPersonalTask` is the one rule (S6b). */
export function inboxKind(item: Pick<MyTask, "projectId">, scope: InboxScope): InboxKind {
  return isPersonalTask(item, scope.personalRootId, scope.areaIds) ? "personal" : "board";
}

/**
 * Every row the Inbox holds, before any filter: the untriaged board rows and
 * every INBOX row. A tickled row (a defer, or a start date not reached yet)
 * is in the Tickler, not here. An archived row is nowhere.
 */
export function inboxRows<T extends Pick<MyTask, "id" | "disposition" | "archivedAt" | "deferUntil" | "startDate">>(
  items: readonly T[],
  fromProjectIds: ReadonlySet<string>,
  nowMs = Date.now(),
): T[] {
  return items.filter(
    (i) =>
      !i.archivedAt &&
      !isTickled(i, nowMs) &&
      (i.disposition === "INBOX" || fromProjectIds.has(i.id)),
  );
}

/** The count the sidebar badge and the header "N to process" both show. */
export function inboxCount(
  items: readonly MyTask[],
  fromProjectIds: ReadonlySet<string>,
  nowMs = Date.now(),
): number {
  return inboxRows(items, fromProjectIds, nowMs).length;
}

/** Counts per kind, for the filter pills. */
export function inboxKindCounts(
  rows: readonly MyTask[],
  scope: InboxScope,
): Record<InboxSource, number> {
  let personal = 0;
  for (const r of rows) if (inboxKind(r, scope) === "personal") personal++;
  return { all: rows.length, personal, board: rows.length - personal };
}

/** Narrow the rows to one kind. `all` keeps both. */
export function filterBySource<T extends Pick<MyTask, "projectId">>(
  rows: readonly T[],
  source: InboxSource,
  scope: InboxScope,
): T[] {
  if (source === "all") return [...rows];
  return rows.filter((r) => inboxKind(r, scope) === source);
}

/**
 * The drawing order: board rows first, then captures. A board row is the one
 * kind somebody else put there, so it leads. Inside each block the member's
 * sort applies (newest or oldest capture first).
 */
export function orderInbox<T extends Pick<MyTask, "projectId" | "createdAt">>(
  rows: readonly T[],
  scope: InboxScope,
  sort: "newest" | "oldest",
): T[] {
  const time = (r: T) => new Date(r.createdAt).getTime();
  const within = (a: T, b: T) => (sort === "newest" ? time(b) - time(a) : time(a) - time(b));
  const board = rows.filter((r) => inboxKind(r, scope) === "board").sort(within);
  const mine = rows.filter((r) => inboxKind(r, scope) === "personal").sort(within);
  return [...board, ...mine];
}

/** The filter pills' labels, in the order they are drawn. */
export const SOURCE_PILLS: readonly { id: InboxSource; label: string }[] = [
  { id: "all", label: "All" },
  { id: "personal", label: "Mine" },
  { id: "board", label: "From Projects" },
];

/** "from Priya" off an assigner's address, or null when nobody is named. */
export function assignedByLabel(assignedBy?: string | null): string | null {
  const who = (assignedBy ?? "").trim();
  if (!who) return null;
  return `from ${who.split("@")[0]}`;
}

/** One always-visible row action (S6g). The table draws the same list. */
export interface InboxRowAction {
  id: "move" | "remove" | "notMine" | "openBoard";
  label: string;
  title: string;
  icon: string;
  run: () => void;
}

/**
 * The actions a row offers, by kind — ONE list, so the card, the table, the
 * context menu and the keyboard cannot drift (`inbox.test.ts`).
 *
 * - personal: **Move to project** (when `promoteAllowed`), **Delete**;
 * - board: **Remove from my lists** (my overlay says TRASH, the board task stays),
 *   **Open on board**. A board row has no Delete.
 */
export function inboxRowActions(input: {
  kind: InboxKind;
  canPromote: boolean;
  move: () => void;
  notMine: () => void;
  remove: () => void;
  openBoard: () => void;
}): InboxRowAction[] {
  if (input.kind === "personal") {
    return [
      ...(input.canPromote
        ? [
            {
              id: "move" as const,
              label: "Move to project",
              title: "Move to project — put it on a company board (m)",
              icon: "FolderInput",
              run: input.move,
            },
          ]
        : []),
      {
        id: "remove" as const,
        label: DELETE_LABEL,
        title: `${DELETE_LABEL} (t)`,
        icon: "Trash2",
        run: input.remove,
      },
    ];
  }
  return [
    {
      // One name per act (`removal.ts`). The id keeps its old spelling so the
      // card, the table and the menu stay unedited. The label is the act's name.
      id: "notMine",
      label: REMOVE_LABEL,
      title: `${REMOVE_LABEL}. The board keeps it (t)`,
      icon: "UserX",
      run: input.notMine,
    },
    {
      id: "openBoard",
      label: "Open on board",
      title: "Open on board (o)",
      icon: "ExternalLink",
      run: input.openBoard,
    },
  ];
}
