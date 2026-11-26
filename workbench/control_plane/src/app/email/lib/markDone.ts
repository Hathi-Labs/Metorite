/**
 * Mark done (task 3995, `email_app_master_plan.md` §6.6): the rules, with no
 * React and no network.
 *
 * One act marks a whole CONVERSATION done, through
 * `POST /email/reply-zero/resolve`. A bulk act sends one call for each pair of
 * mailbox and thread. The gateway writes the status and the labels. These
 * functions only draw the same change on the loaded rows at once.
 *
 * Fence: `markDone.test.ts`.
 */
import type { UndoEntry } from "@/lib/undo";

import type { SearchFilter } from "./searchFilters";

/** The key that marks the open mail done. `e` stays Archive. */
export const MARK_DONE_KEY = "d";

/** The words of the toast button when a conversation of the act had "Follow-up". */
export const MOVE_BACK_LABEL = "Move back to Follow-up";

/** How long the toast with its button stays, in ms. */
export const MARK_DONE_TOAST_MS = 10_000;

/** The states that a move back may restore. The gateway checks them again. */
export type RestoreStatus = "NEEDS_REPLY" | "AWAITING" | "FYI";

export interface PreviousState {
  status: RestoreStatus;
  followUp: boolean;
}

export interface ThreadRef {
  accountId: string;
  threadId: string;
}

/** A conversation that the act marked done, and what it was before. */
export interface MarkedThread extends ThreadRef {
  previous: PreviousState | null;
}

/** What a row needs here. */
export interface DoneRow {
  id: string;
  accountId: string;
  threadId?: string;
  categories: string[];
  folder: string;
  receivedAt: string;
}

const STATUS_LABEL: Record<RestoreStatus, string> = {
  NEEDS_REPLY: "Needs Reply",
  AWAITING: "Awaiting Reply",
  FYI: "FYI",
};

/** The labels that a Done conversation does not wear (the gateway's list). */
const NOT_DONE_LABELS = new Set([
  "Needs Reply", "Awaiting Reply", "Follow-up", "FYI", "Reply", "To Reply", "Actioned",
]);

export function threadKey(ref: ThreadRef): string {
  return `${ref.accountId}\n${ref.threadId}`;
}

/**
 * The distinct conversations of `rows`, in their order. A row with no thread
 * is no conversation, so it is left out. `fallbackAccountId` serves a row that
 * names no mailbox.
 */
export function threadsOf(
  rows: ReadonlyArray<{ accountId?: string; threadId?: string }>,
  fallbackAccountId: string | null = null,
): ThreadRef[] {
  const seen = new Map<string, ThreadRef>();
  for (const row of rows) {
    const accountId = row.accountId || fallbackAccountId;
    if (!accountId || !row.threadId) continue;
    const ref = { accountId, threadId: row.threadId };
    seen.set(threadKey(ref), ref);
  }
  return [...seen.values()];
}

export function inThread(row: { accountId?: string; threadId?: string }, ref: ThreadRef): boolean {
  // A conversation never spans two mailboxes (§11.6 case 13).
  return row.threadId === ref.threadId && (!row.accountId || row.accountId === ref.accountId);
}

/** The newest row of `rows`, and the newest one that is not in Sent. */
function newest(rows: ReadonlyArray<DoneRow>): { latest?: string; inbound?: string } {
  const order = [...rows].sort((a, b) => (a.receivedAt < b.receivedAt ? -1 : a.receivedAt > b.receivedAt ? 1 : 0));
  const inbound = order.filter((r) => (r.folder || "").toLowerCase() !== "sent");
  return {
    latest: order.at(-1)?.id,
    inbound: (inbound.at(-1) ?? order.at(-1))?.id,
  };
}

/** The labels of each loaded row of one conversation after Done. */
export function doneCategories(rows: ReadonlyArray<DoneRow>): Map<string, string[]> {
  const { inbound } = newest(rows);
  return new Map(
    rows.map((row) => {
      const kept = row.categories.filter((c) => !NOT_DONE_LABELS.has(c));
      if (row.id === inbound && !kept.includes("Done")) kept.push("Done");
      return [row.id, kept];
    }),
  );
}

/** The labels of each loaded row of one conversation after a move back. */
export function restoredCategories(
  rows: ReadonlyArray<DoneRow>,
  previous: PreviousState,
): Map<string, string[]> {
  const { latest, inbound } = newest(rows);
  const label = STATUS_LABEL[previous.status];
  return new Map(
    rows.map((row) => {
      const next = row.categories.filter((c) => c !== "Done");
      if (row.id === inbound && !next.includes(label)) next.push(label);
      if (previous.followUp && row.id === latest && !next.includes("Follow-up")) next.push("Follow-up");
      return [row.id, next];
    }),
  );
}

/** The labels that the view requires: the label view and each tag pill. */
export function requiredLabels(view: {
  selectedLabel: string | null;
  searchFilters: ReadonlyArray<SearchFilter>;
}): string[] {
  const tags = view.searchFilters.filter((f) => f.kind === "tag").map((f) => f.value);
  return (view.selectedLabel ? [view.selectedLabel, ...tags] : tags).map((l) => l.toLowerCase());
}

/** True when a row had a label that the view requires, and lost it. */
export function leavesView(required: ReadonlyArray<string>, before: string[], after: string[]): boolean {
  const has = (list: string[], label: string) => list.some((c) => c.toLowerCase() === label);
  return required.some((label) => has(before, label) && !has(after, label));
}

/** The mail to open after `gone` left the list: the one at its place. */
export function nextSelection(
  before: ReadonlyArray<{ id: string }>,
  gone: ReadonlySet<string>,
  selectedId: string,
): string | null {
  const at = before.findIndex((r) => r.id === selectedId);
  if (at < 0) return null;
  const after = before.slice(at + 1).find((r) => !gone.has(r.id));
  const earlier = before.slice(0, at).reverse().find((r) => !gone.has(r.id));
  return (after ?? earlier)?.id ?? null;
}

export function doneTitle(count: number): string {
  return count === 1 ? "Marked done" : `Marked ${count} conversations done`;
}

/**
 * The toast button for the conversations that can move back, or null when none
 * can. It names Follow-up only when one of them had that label.
 */
export function moveBackLabel(marked: ReadonlyArray<MarkedThread>): string | null {
  const back = marked.filter((m) => m.previous);
  if (back.length === 0) return null;
  return back.some((m) => m.previous?.followUp) ? MOVE_BACK_LABEL : "Undo";
}

/** The two writes that an undo step runs. The store gives them. */
export interface MarkDoneIo<M extends MarkedThread> {
  markDone: (threads: ReadonlyArray<M>) => Promise<{ marked: M[]; failed: number }>;
  moveBack: (marked: ReadonlyArray<M>) => Promise<number>;
}

/**
 * The step of one act on the app's undo stack (`lib/undo.ts`), so the toast
 * button and Ctrl+Z run the same thing. A redo marks the same conversations
 * done again, and a later undo restores what that redo found. A failed write
 * rejects, so the stack keeps the step.
 */
export function markDoneEntry<M extends MarkedThread>(
  marked: ReadonlyArray<M>,
  io: MarkDoneIo<M>,
): UndoEntry {
  let current = marked.filter((m) => m.previous);
  return {
    label: current.length === 1 ? "marked done" : `marked ${current.length} conversations done`,
    undo: async () => {
      if ((await io.moveBack(current)) > 0) throw new Error("Could not move the conversation back");
    },
    redo: async () => {
      const again = await io.markDone(current);
      if (again.failed > 0) throw new Error("Could not mark the conversation done");
      current = again.marked.filter((m) => m.previous);
    },
  };
}

/** The menu and bar words of the act for `count` conversations. */
export function markDoneItemLabel(bulk: boolean, count: number): string {
  return bulk ? `Mark ${count} done` : "Mark done";
}
