/**
 * My Tasks · the one inbox (my_tasks_cutover.md §5 S6g).
 *
 * Changed 2026-11-03 by D170 (INB-1): the inbox holds only what I captured.
 * A capture is an INBOX row. It can sit in my own tree or, after a "#project"
 * capture, on a company board with its INBOX overlay kept. Work a colleague
 * assigned to me and I have not opened is NOT here. My tasks shows it as New
 * (`lib/newWork.ts`).
 *
 * This module is the one rule for "what the Inbox holds", "which kind a row
 * is", and "in what order". The sidebar badge, the header count, the list and
 * the keyboard walk all read it, so the badge and the header cannot disagree.
 * The chat tools read the same rule: the `disposition=INBOX` filter of
 * `/projects/my/inbox`.
 *
 * Fences: `inbox.test.ts`.
 */

import { isPersonalTask } from "./clarify";
import { DELETE_LABEL, REMOVE_LABEL } from "./removal";
import type { MyTask } from "./types";
import { isTickled } from "./utils";

/** Which kind an Inbox row is. The origin marker and the actions follow it. */
export type InboxKind = "personal" | "board";

/** The Inbox's source filter. The pills are gone (D170), the type stays in the store. */
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
 * Every row the Inbox holds, before any filter: the INBOX rows, which are my
 * captures. A tickled row (a defer, or a start date not reached yet) is in
 * the Tickler, not here. An archived row is nowhere.
 */
export function inboxRows<T extends Pick<MyTask, "id" | "disposition" | "archivedAt" | "deferUntil" | "startDate">>(
  items: readonly T[],
  nowMs = Date.now(),
): T[] {
  return items.filter(
    (i) => !i.archivedAt && !isTickled(i, nowMs) && i.disposition === "INBOX",
  );
}

/** The count the sidebar badge and the header "N to process" both show. */
export function inboxCount(items: readonly MyTask[], nowMs = Date.now()): number {
  return inboxRows(items, nowMs).length;
}

/**
 * The drawing order: the member's sort, newest or oldest capture first. A
 * capture filed to a company board has the same place as the rest.
 */
export function orderInbox<T extends Pick<MyTask, "createdAt">>(
  rows: readonly T[],
  sort: "newest" | "oldest",
): T[] {
  const time = (r: T) => new Date(r.createdAt).getTime();
  return [...rows].sort((a, b) => (sort === "newest" ? time(b) - time(a) : time(a) - time(b)));
}

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
              title: "Move to project — put it on a company board",
              icon: "FolderInput",
              run: input.move,
            },
          ]
        : []),
      {
        id: "remove" as const,
        label: DELETE_LABEL,
        title: DELETE_LABEL,
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
      title: `${REMOVE_LABEL}. The board keeps it`,
      icon: "UserX",
      run: input.notMine,
    },
    {
      id: "openBoard",
      label: "Open on board",
      title: "Open on board",
      icon: "ExternalLink",
      run: input.openBoard,
    },
  ];
}
