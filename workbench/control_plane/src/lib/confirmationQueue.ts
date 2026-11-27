/**
 * The queue of confirmation cards one chat is waiting on.
 *
 * A model can call card-gated tools in parallel. On 2026-10-06 one turn
 * made ten calls, and the server parked ten tools, each on its own card. The
 * chat had ONE card slot, so nine cards were never shown and the run never
 * ended. A reconnect replayed all ten events, the slot landed on a card the
 * member had already approved, and every Approve got a 409.
 *
 * The rules:
 *
 * 1. Every pending card is kept, keyed by its `request_id`, with no
 *    duplicates. Answering one card removes only that card.
 * 2. `confirmation_resolved` (published by `request_confirmation` in
 *    `acb_skills/ask_tools.py`) closes a card. The id is remembered, so a
 *    replay that sends the request again does not bring the card back.
 * 3. A card that is being answered is not shown again by a replay.
 * 4. A failed answer restores its card ONLY when the failure can pass on a
 *    retry: a network fault or a 5xx. A 4xx (a 409) removes the card, and a
 *    restored card would get a 409 on every click. The POST and that rule
 *    are `lib/respondInput.ts`. `settleAnswer` turns its outcome into the
 *    action for the card. A 400 or a 422 also restores it: the member can
 *    change the answer (#916).
 * 5. A 4xx does NOT remember the id. The gateway also answers 409 when the
 *    control bus is slow or has no listener (`dispatch_control`), and then
 *    the tool still waits. Only the server's `confirmation_resolved` closes
 *    a card for good, so a later replay can show a card that still waits.
 *
 * Pure, and in `lib/`, because `vitest.config.ts` runs in the node
 * environment. Fence: `src/lib/confirmationQueue.test.ts`.
 */

import { ANSWERED_ONCE_TEXT } from "@/lib/respondInput";
import { archiveUndoText, type ArchiveUndo, type ArchiveUndoState } from "@/lib/archiveUndo";
import { CARD_OUTCOME_MEMBER_LINES } from "@/lib/cardOutcomes";
import { unfenced } from "@/lib/fencedText";
import { sharesTo, tickedOfSent, type PeopleBefore, type SentVersion } from "@/lib/redraftCard";
import { DELETE_EARLIER_ANSWER, earlierFromEvent, isNewMail, isNewMailEdits, replyFromEvent, startTicks, type EarlierDraft, type NewMailEdits, type ReplyForm } from "@/lib/replyCard";
import { classOf, kindOf, sendCanGo, type PersonClass, type SendRowKind } from "@/lib/sendCard";
import { isTaskCardEdits, isUnchanged, taskEditFromEvent, type TaskCardEdits, type TaskEditForm } from "@/lib/taskEditCard";

/** The event a closed card arrives as. The server owns the name
 *  (`CONFIRMATION_RESOLVED` in `ask_tools.py`), and
 *  `tests/unit/test_chat_hardening.py` checks the two agree. */
export const CONFIRMATION_RESOLVED = "confirmation_resolved";

/** The event of an approved card that did nothing (task 3827). The server owns
 *  the name (`CONFIRMATION_NOT_DONE` in `ask_tools.py`). */
export const CONFIRMATION_NOT_DONE = "confirmation_not_done";

/** SBX-3b (`agent_sandbox.md` §12.5 decision 8): the result of each row of a
 *  held sandbox card. The server owns the name (`CONFIRMATION_ROW_RESULTS` in
 *  `ask_tools.py`), and `tests/unit/test_confirmation_rows.py` checks that the
 *  two agree. The event carries a status and a door code for each row, and no
 *  body. */
export const CONFIRMATION_ROW_RESULTS = "confirmation_row_results";

/** The result of one row of a held sandbox card. */
export interface RowResult {
  id: string;
  status: number;
  code?: string;
}

/** The most rows one card may carry (`MAX_CARD_ROWS` in `ask_tools.py`). */
const MAX_RESULT_ROWS = 100;

/** The rows of a `confirmation_row_results` value, or null when it is not one. */
export function rowResultsFromEvent(value: unknown): { requestId: string; rows: RowResult[] } | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  const rid = v.request_id;
  if (typeof rid !== "string" || !/^[0-9a-f]{32}$/.test(rid)) return null;
  if (!Array.isArray(v.rows) || v.rows.length === 0 || v.rows.length > MAX_RESULT_ROWS) return null;
  const rows: RowResult[] = [];
  for (const raw of v.rows) {
    if (!raw || typeof raw !== "object") return null;
    const r = raw as Record<string, unknown>;
    if (typeof r.id !== "string" || !/^row-[0-9]{1,2}$/.test(r.id)) return null;
    if (typeof r.status !== "number" || !Number.isInteger(r.status) || r.status < 100 || r.status > 599) return null;
    rows.push({
      id: r.id,
      status: r.status,
      ...(typeof r.code === "string" && /^sandbox_[a-z_]{1,48}$/.test(r.code) ? { code: r.code } : {}),
    });
  }
  return { requestId: rid, rows };
}

/** What happened to one row, in a few words. */
function rowOutcome(r: RowResult): string {
  if (r.status >= 200 && r.status < 300) return "done";
  if (r.code === "sandbox_write_refused") return "not done, not approved";
  if (r.code === "sandbox_approval_timeout") return "not done, the card timed out";
  if (r.code === "sandbox_not_run") return "not done, the run ended first";
  return `failed (${r.status})`;
}

/** The results of a held sandbox card, as one sentence: "Results: row 1 done,
 *  row 2 not done, not approved." The rows go in card order. */
export function rowResultsText(rows: readonly RowResult[]): string {
  return `Results: ${rows.map((r, i) => `row ${i + 1} ${rowOutcome(r)}`).join("; ")}.`;
}

/**
 * One checkbox of a card with rows (WS-46 P13 one-card). The tool offers the
 * rows, and the member's Approve names the ticked ids.
 */
export interface ConfirmationRow {
  id: string;
  label: string;
  hint?: string;
  checked: boolean;
  /** A held send card (§21, `sendCard.ts`): what the row is. */
  kind?: SendRowKind;
  /** A held send card: how the SERVER classed the person on the row. */
  cls?: PersonClass;
  /** A held send card: the server's warning (a lookalike domain). */
  warning?: string;
}

export interface PendingConfirmation {
  /** The dedupe key: the `request_id` of a blocking card, else the event id. */
  key: string;
  title: string;
  detail?: string;
  context?: string;
  /** Set when the tool is parked on a server Future (a blocking card). */
  requestId?: string;
  /** Set only for a card with rows. A card without rows is as before. */
  rows?: ConfirmationRow[];
  /** The card's text fences member values in «marks» (`ask_tools` `fenced`). */
  fenced?: boolean;
  /**
   * EM-CR: the reply form of a reply card (`lib/replyCard.ts`). `null` is a
   * form the card could not read: it then offers Discard only.
   */
  reply?: ReplyForm | null;
  /**
   * EAC-2 (`projects_ai_chat.md` §32): the edit form of a `create_task` card
   * (`lib/taskEditCard.ts`). Only a form the client can read is kept, so a card
   * with no `edit`, and one with an unknown shape, is the card of before.
   */
  edit?: TaskEditForm;
  /**
   * §18.11: the slot of a new-mail card that a chat message closed as
   * REVISED. "updating" waits for the new draft, and "unchanged" says that
   * the run ended with none. A slot has no buttons and answers nothing.
   */
  redraft?: "updating" | "unchanged";
  /**
   * P3: a new-mail card arrived after this slot, so the run did draw a draft
   * and "The draft did not change." would be false. The run's end closes
   * such a slot with no line.
   */
  newerCard?: true;
  /**
   * §18.11: the member's version that a chat message carried for this card
   * (`lib/redraftCard.ts`). The card that revises it reads it for its
   * "Changed:" line.
   */
  sent?: SentVersion;
  /**
   * §18.11: the member's last version of the card that this card revises:
   * the version a chat message carried, else that card's own text. The
   * client computes the "Changed:" line from it, never from the model.
   */
  before?: { subject: string; body: string; people?: PeopleBefore };
  /**
   * Task 3978 (`email_app_master_plan.md` §18.13.1), a plain card only (the
   * card of `edit_draft`): one line that names a draft that the chat saved.
   * Reject then asks `EARLIER_QUESTION` on the card. A new-mail card keeps
   * its line on its form (`ReplyForm.earlier`).
   */
  earlier?: string;
  /** Task 3978: what "Delete it" does at the mailbox's provider, if known. */
  earlierEffect?: string;
}

/**
 * Task 3978: the earlier draft that a card asks about, or null. A new-mail
 * card reads its form, and a plain card (no rows, no reply form, no edit form)
 * reads its own keys. Any other card asks nothing.
 */
export function earlierOf(card: Pick<PendingConfirmation, "reply" | "rows" | "edit" | "earlier" | "earlierEffect">): EarlierDraft | null {
  if (isNewMail(card.reply)) {
    return card.reply?.earlier ? { line: card.reply.earlier, ...(card.reply.earlierEffect ? { effect: card.reply.earlierEffect } : {}) } : null;
  }
  if (card.reply !== undefined || card.rows !== undefined || card.edit !== undefined || !card.earlier) return null;
  return { line: card.earlier, ...(card.earlierEffect ? { effect: card.earlierEffect } : {}) };
}

/** The answer of a card that a chat message closed. The server owns it
 *  (`REVISED_ANSWER` in `ask_tools.py`), and
 *  `tests/unit/test_email_redraft_snapshot.py` holds the two in step. */
export const REVISED_ANSWER = "REVISED";

/** The cards that wait on the member: every card but a slot of a redraft. */
export function liveCards<T extends Pick<PendingConfirmation, "redraft">>(cards: readonly T[]): T[] {
  return cards.filter((c) => c.redraft === undefined);
}

/**
 * The answer of a card with rows: `APPROVE {"rows": [...]}`. The server owns
 * the shape (`ROWS_ANSWER_PREFIX` and `ticked_rows` in `ask_tools.py`), and
 * `tests/unit/test_confirmation_rows.py` reads this file to hold the two in
 * step. A card without rows answers a plain `APPROVE`.
 */
export const ROWS_ANSWER_PREFIX = "APPROVE ";

export type RowsApproval = `APPROVE {${string}`;

export function approveRows(ids: string[]): RowsApproval {
  return `${ROWS_ANSWER_PREFIX}${JSON.stringify({ rows: ids })}` as RowsApproval;
}

/** The most rows the card draws. The server holds the same cap (`MAX_CARD_ROWS`). */
export const MAX_CARD_ROWS = 100;

/** The rows of an event, or undefined for a list the card cannot read. */
export function rowsFromEvent(value: unknown): ConfirmationRow[] | undefined {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_CARD_ROWS) return undefined;
  const rows: ConfirmationRow[] = [];
  const seen = new Set<string>();
  for (const raw of value) {
    if (!raw || typeof raw !== "object") return undefined;
    const r = raw as Record<string, unknown>;
    const id = typeof r.id === "string" ? r.id : "";
    if (!id || seen.has(id)) return undefined;
    seen.add(id);
    const kind = kindOf(r.kind);
    const cls = classOf(r.class);
    rows.push({
      id,
      label: String(r.label ?? id),
      hint: r.hint ? String(r.hint) : undefined,
      checked: r.checked !== false,
      // A row with none of the three keys of a held send card is as before.
      ...(kind !== undefined ? { kind } : {}),
      ...(cls !== undefined ? { cls } : {}),
      ...(typeof r.warning === "string" && r.warning ? { warning: r.warning } : {}),
    });
  }
  return rows;
}

/**
 * The summary of a card with rows, as the member ticks them. The tool's
 * title says the whole count ("Create 4 tasks in «Ops»?"), and the card says
 * how many of them the Approve covers ("Create 3 of 4 tasks in «Ops»?"). A
 * title with no count keeps its words, and the count follows them.
 */
export function rowSummary(title: string, ticked: number, total: number): string {
  if (ticked === total) return title;
  const count = new RegExp(`\\b${total}\\b`);
  if (count.test(title)) return title.replace(count, `${ticked} of ${total}`);
  // "· 1 of 4 ticked", and not "(1 of 4)": beside a stack's "1 of 2" pager,
  // a bare count read as a second card counter (owner report, 2026-10-24).
  return `${title} · ${ticked} of ${total} ticked`;
}

/**
 * The card as the member left it: each row ticked as the member ticked it.
 * The queue hands THIS card to the answer path, so a restore after a failed
 * POST (a 5xx, a network fault) shows the member's ticks, never the tool's
 * defaults again (review round 1). A card with no rows is the same card.
 */
export function withTicked(
  card: PendingConfirmation,
  ticked?: readonly string[],
): PendingConfirmation {
  if (card.rows === undefined || ticked === undefined) return card;
  const on = new Set(ticked);
  return { ...card, rows: card.rows.map((r) => ({ ...r, checked: on.has(r.id) })) };
}

// ── The ticks of a card with rows, as pure functions (PR #691 review) ───────
//
// The card and the queue hold no tick logic of their own. The queue keeps
// the member's ticks in `rowTicksReducer`, per card key, so a page away and
// back keeps them. `rowsView` turns a card and those ticks into what the card
// draws and what its Approve sends. Fence: `confirmationQueue.test.ts`
// ("the ticks are the member's"), whose mutations name these functions.

/** The member's ticks of every card with rows, by card key. */
export type RowTicks = Readonly<Record<string, readonly string[]>>;

/**
 * The member's edits of each form, by card key: a new-mail form (`NewMailEdits`)
 * or a task card (`TaskCardEdits`, EAC-2). The queue holds them as it holds
 * `RowTicks`, because a change of tab unmounts the form. Without them, an
 * untick came back ticked and Send went to that person.
 *
 * A reader of one kind asks its guard (`isNewMailEdits`, `isTaskCardEdits`),
 * and never casts. `redraftCard.ts` reads the `NewMailEdits` kind only.
 */
export type FormEdit = NewMailEdits | TaskCardEdits;
export type FormEdits = ReadonlyMap<string, FormEdit>;

export const NO_EDITS: FormEdits = new Map();

/** The keys of the cards that the queue holds: a card on screen, a slot, or a card whose answer is in flight. */
export function heldKeys(state: Pick<ConfirmationQueueState, "cards" | "answering">): ReadonlySet<string> {
  return new Set([...state.cards.map((c) => c.key), ...state.answering]);
}

/**
 * The kept edits without those of the cards that LEFT the queue between two states: an answered card,
 * a slot that a draft took or that ended, a `reset`. The same map comes back when no card left.
 *
 * Task 1893. This was `liveEdits`, which dropped each entry whose card was not in the queue NOW. A chat
 * switch resets the queue, so it dropped every edit, and the card then came back from the card log with
 * the server's form: an untick came back ticked. A card that was never in the queue before (the redraw of
 * a switch) has no `before` entry, so it never counts as having left. The CHAT must not call this for
 * the reset of a switch: it holds the edits of each thread in `chatStore`, and a switch only changes
 * which thread's map it reads.
 */
export function closedEdits(edits: FormEdits, before: ReadonlySet<string>, now: ReadonlySet<string>): FormEdits {
  let out: Map<string, FormEdit> | null = null;
  for (const key of edits.keys()) {
    if (!before.has(key) || now.has(key)) continue;
    out ??= new Map(edits);
    out.delete(key);
  }
  return out ?? edits;
}

/** Keep the edits of one form. The same values change nothing. */
export function formEditsReducer(edits: FormEdits, action: { key: string; value: FormEdit }): FormEdits {
  const was = edits.get(action.key);
  const now = action.value;
  if (was && sameEdits(was, now)) return edits;
  return new Map(edits).set(action.key, now);
}

/** True when two kept edits of one card are the same values. A new mail and a task card are never the same. */
function sameEdits(was: FormEdit, now: FormEdit): boolean {
  if (isNewMailEdits(was) && isNewMailEdits(now)) {
    return was.subject === now.subject && was.body === now.body && was.ticks === now.ticks && was.fileTicks === now.fileTicks;
  }
  if (isTaskCardEdits(was) && isTaskCardEdits(now)) return isUnchanged(was, now);
  return false;
}

/** The kept edits as new-mail edits, or undefined for none and for another kind. */
export function asNewMailEdits(e: FormEdit | undefined): NewMailEdits | undefined {
  return isNewMailEdits(e) ? e : undefined;
}

/** The kept edits as task-card edits, or undefined for none and for another kind. */
export function asTaskEdits(e: FormEdit | undefined): TaskCardEdits | undefined {
  return isTaskCardEdits(e) ? e : undefined;
}

export const NO_TICKS: RowTicks = {};

export type RowTickAction = {
  type: "toggle";
  card: Pick<PendingConfirmation, "key" | "rows">;
  id: string;
  on: boolean;
};

/** The ids the member has ticked on *card*: the kept ticks, else the tool's.
 *  Only ids the card offers, in the card's order. */
export function ticksOf(card: Pick<PendingConfirmation, "key" | "rows">, ticks: RowTicks): string[] {
  if (!card.rows) return [];
  const kept = ticks[card.key];
  const on = new Set(kept ?? card.rows.filter((r) => r.checked).map((r) => r.id));
  return card.rows.filter((r) => on.has(r.id)).map((r) => r.id);
}

/** One tick or untick, kept under the card's key. Other cards keep theirs. */
export function rowTicksReducer(ticks: RowTicks, action: RowTickAction): RowTicks {
  const { card } = action;
  if (!card.rows || !card.rows.some((r) => r.id === action.id)) return ticks;
  const next = new Set(ticksOf(card, ticks));
  if (action.on) next.add(action.id);
  else next.delete(action.id);
  return { ...ticks, [card.key]: card.rows.filter((r) => next.has(r.id)).map((r) => r.id) };
}

export interface RowsView {
  /** The rows as the card draws them: each ticked as the member left it. */
  rows?: ConfirmationRow[];
  /** What Approve sends, or null when it may not send (no row ticked). */
  approved: { card: PendingConfirmation; answer: "APPROVE" | RowsApproval } | null;
}

/** What the card draws, and what its Approve sends, for *card* and *ticks*. */
export function rowsView(card: PendingConfirmation, ticks: RowTicks): RowsView {
  if (card.rows === undefined) return { approved: approvedCard(card) };
  const ticked = ticksOf(card, ticks);
  return { rows: withTicked(card, ticked).rows, approved: approvedCard(card, ticked) };
}

/** The card's summary line, counting the rows ticked now. */
export function rowsSummary(summary: string, rows?: readonly ConfirmationRow[]): string {
  if (rows === undefined) return summary;
  return rowSummary(summary, rows.filter((r) => r.checked).length, rows.length);
}

/**
 * Approve may send: a card with no rows, or a card with a row ticked. A held
 * send card (§21) needs a ticked To row (`sendCard.ts`).
 */
export function canApprove(rows?: readonly ConfirmationRow[]): boolean {
  return sendCanGo(rows);
}

/**
 * What the queue sends for an Approve: the answer, and the card with the
 * member's ticks on it, which a failed POST restores. Null approves nothing.
 */
export function approvedCard(
  card: PendingConfirmation,
  ticked?: readonly string[],
): { card: PendingConfirmation; answer: "APPROVE" | RowsApproval } | null {
  const answer = approvalFor(card, ticked);
  return answer ? { card: withTicked(card, ticked), answer } : null;
}

/**
 * The Approve answer of a card: a plain `APPROVE` for a card with no rows,
 * and the ticked ids for a card with rows. It keeps only ids the card
 * offered, in the card's order, and gives null when none is left, because
 * an approval of no row is no approval (the server refuses it too).
 */
export function approvalFor(
  card: Pick<PendingConfirmation, "rows">,
  ticked?: readonly string[],
): "APPROVE" | RowsApproval | null {
  if (card.rows === undefined) return "APPROVE";
  const wanted = new Set(ticked ?? []);
  const ids = card.rows.filter((r) => wanted.has(r.id)).map((r) => r.id);
  // A held send card (§21) sends nothing with no ticked To row.
  if (!sendCanGo(card.rows.map((r) => ({ ...r, checked: wanted.has(r.id) })))) return null;
  return ids.length > 0 ? approveRows(ids) : null;
}

/**
 * What became of an answered card, as one line in the thread (UXD-3). A card
 * never leaves with no word: it reads "Applying", then "Approved" or
 * "Rejected", or "Not applied" with the reason of a refusal.
 */
export interface ConfirmationReceipt {
  key: string;
  /** The card's summary, or its title. */
  label: string;
  kind: "applying" | "approved" | "rejected" | "refused" | "closed" | "notDone";
  /** What the member answered. "applying" becomes this once the server took it. */
  intent: "approved" | "rejected";
  /** Why a refused answer did not land. */
  reason?: string;
  /** The message the line draws under: the last one when the member answered. */
  afterId?: string | null;
  /** A held Reject (`lib/rejectHold.ts`): the POST has not gone, and the line
   *  offers Undo. `released` clears it once the POST goes. */
  undo?: boolean;
  /** WT-6a: the archive that this approved card made, and where its Undo is
   *  (`lib/archiveUndo.ts`). Only the server's `confirmation_undo` sets it. */
  archive?: ArchiveUndo & { state: ArchiveUndoState };
  /** SBX-3b: the result of each row of a held sandbox card. Only the server's
   *  `confirmation_row_results` sets it. */
  rowResults?: readonly RowResult[];
}

export interface ConfirmationQueueState {
  cards: PendingConfirmation[];
  /** Ids the server closed, or that took an answer. A replay cannot reopen them. */
  resolved: ReadonlySet<string>;
  /** Ids with an answer in flight. A replay cannot reopen them either. */
  answering: ReadonlySet<string>;
  /** Why the last answer of a restored card failed, by key. The card shows
   *  it beside its buttons, and Approve tries again (UXD-3). */
  errors: ReadonlyMap<string, string>;
  /** One line for each answered card, oldest first (UXD-3). */
  receipts: readonly ConfirmationReceipt[];
  /**
   * §18.11 fix round 6: the new-mail cards that a chat message closed while
   * they had no slot here, as when a Send was in flight. The member did not
   * answer them in this tab. A `reopened` ask for one shows it again as a new
   * card, so the tool never waits on a card that nobody sees.
   */
  reopenable?: ReadonlySet<string>;
}

export const EMPTY_CONFIRMATIONS: ConfirmationQueueState = {
  cards: [],
  resolved: new Set(),
  answering: new Set(),
  errors: new Map(),
  receipts: [],
  reopenable: new Set(),
};

/** The line of a receipt, in plain words. */
/**
 * The words of a receipt: the card's detail, else its title. A fenced card
 * marks the member's own values with «marks», which the card draws as
 * values. The receipt line is plain text, so the marks go (owner report,
 * 2026-11-02: "Approved · «Video and Marketing»").
 */
export function receiptLabel(card: Pick<PendingConfirmation, "detail" | "title" | "fenced">): string {
  const text = card.detail || card.title;
  return card.fenced ? unfenced(text) : text;
}

/** Task 3978: what the receipt adds when the member answered "Delete it". */
export const EARLIER_DELETE_RECEIPT = "You asked to delete the draft. The reply says what became of it.";

/**
 * Task 3978 (§18.13.1): the words of the receipt of one answer. "Delete it" is
 * a decline, and the receipt says that the member asked for the delete. Only
 * the reply knows whether the draft went, so the receipt claims no delete.
 * Each other answer keeps `receiptLabel`.
 */
export function answerReceiptLabel(card: Pick<PendingConfirmation, "detail" | "title" | "fenced">, answer: unknown): string {
  const label = receiptLabel(card);
  if (answer !== DELETE_EARLIER_ANSWER) return label;
  return `${label.replace(/\.?\s*$/, ".")} ${EARLIER_DELETE_RECEIPT}`;
}

export function receiptText(r: ConfirmationReceipt): string {
  const head = receiptHead(r);
  // SBX-3b: a held sandbox card says what happened to each row.
  return r.rowResults?.length ? `${head} ${rowResultsText(r.rowResults)}` : head;
}

function receiptHead(r: ConfirmationReceipt): string {
  // WT-6a: an archive line says what its Undo did.
  const undone = r.archive ? archiveUndoText(r.archive.state, r.label) : null;
  if (undone) return undone;
  switch (r.kind) {
    case "applying":
      // A held Reject already reads as done, and offers Undo (#916).
      if (r.undo) return `Rejected · ${r.label}`;
      return r.intent === "rejected" ? `Rejecting… ${r.label}` : `Applying… ${r.label}`;
    case "approved":
      return `Approved · ${r.label}`;
    case "rejected":
      return `Rejected · ${r.label}`;
    case "refused":
      return `Not applied · ${r.label}${r.reason ? `. ${r.reason}` : ""}`;
    case "closed":
      return `Closed · ${r.label}. The assistant has moved on. Its reply says what happened.`;
    case "notDone":
      // Task 3827: the card was approved, and the tool refused the write.
      return `Not done · ${r.label}. ${CARD_OUTCOME_MEMBER_LINES["changed since"]}`;
  }
}

function withoutError(errors: ReadonlyMap<string, string>, key: string): ReadonlyMap<string, string> {
  if (!errors.has(key)) return errors;
  const next = new Map(errors);
  next.delete(key);
  return next;
}

function settleReceipt(
  receipts: readonly ConfirmationReceipt[],
  key: string,
  patch: (r: ConfirmationReceipt) => ConfirmationReceipt | null,
): readonly ConfirmationReceipt[] {
  if (!receipts.some((r) => r.key === key)) return receipts;
  return receipts.flatMap((r) => {
    if (r.key !== key) return [r];
    const next = patch(r);
    return next ? [next] : [];
  });
}

export type ConfirmationAction =
  | { type: "requested"; value: unknown }
  /** The server closed a card. `answer` is the closing answer of
   *  `confirmation_resolved`: a new-mail card closed as REVISED stays as a
   *  slot (§18.11). */
  | {
      type: "resolved"; requestId: string; answer?: string;
      /** RESUME-3a: the card waited parked here, so an answer from another
       *  tab or device can close it. An Approve then leaves "Approved". */
      elsewhere?: boolean;
      /** The message the line of that Approve draws under. */
      afterId?: string | null;
    }
  /** §18.11: a chat message carried this version of the card. */
  | { type: "memberVersion"; key: string; version: SentVersion }
  /** §18.11: the slots that read "The draft did not change." close. */
  | { type: "closeUnchanged" }
  /** §18.11: the run rests, by any end. Only a slot moves, and a card stays. */
  | { type: "runRested" }
  /** An answer is in flight. With `label`, a receipt line says so (UXD-3). */
  | {
      type: "answering";
      key: string;
      label?: string;
      intent?: "approved" | "rejected";
      afterId?: string | null;
      /** A Reject held for Undo (`lib/rejectHold.ts`). */
      undo?: boolean;
    }
  /** The hold ended and the REJECT POST goes: the line stops offering Undo. */
  | { type: "released"; key: string }
  /** WT-6a: the server's `confirmation_undo` for an approved archive card. */
  | { type: "undoOffered"; requestId: string; undo: ArchiveUndo }
  /** WT-6a: where the Undo of an archive line is now. */
  | { type: "undoState"; key: string; state: ArchiveUndoState }
  | { type: "answered"; key: string }
  /** Task 3827: the tool refused the approved card because the object changed
   *  after it. `confirmation_not_done` carries the card's `request_id`, so the
   *  line of that card alone reads "Not done". No receipt of that key: no change. */
  | { type: "notDone"; requestId: string }
  /** SBX-3b: the server's `confirmation_row_results` for a held sandbox card.
   *  Only the line of that card takes it. No line of that key: no change. */
  | { type: "rowResults"; requestId: string; rows: readonly RowResult[] }
  /** The POST got a 4xx. The card stays gone until a replay shows it again.
   *  `reason` turns its receipt into "Not applied"; none (a resend) drops it. */
  | { type: "dropped"; key: string; reason?: string }
  /** The POST may pass on a retry. `reason` shows on the card (UXD-3). */
  | { type: "restore"; card: PendingConfirmation; reason?: string }
  /** A non-blocking card, answered by a chat message. Its key can come again. */
  | { type: "dismiss"; key: string }
  | { type: "runFinalized" }
  | { type: "reset" };

/** A `confirmation_requested` value as a card, or null when it is not one. */
export function cardFromEvent(value: unknown): PendingConfirmation | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  const requestId = v.request_id ? String(v.request_id) : undefined;
  return {
    // A non-blocking card has no request id. Its own id, or one fixed key,
    // keeps a re-send of it from drawing twice.
    key: requestId ?? String(v.id ?? "confirmation"),
    title: String(v.title ?? "Confirm action"),
    detail: v.detail ? String(v.detail) : undefined,
    context: v.context ? String(v.context) : undefined,
    requestId,
    // A rows list the card cannot read gives NO rows, so nothing can be
    // ticked and Approve stays off. A plain APPROVE would be refused anyway.
    ...(v.rows !== undefined ? { rows: rowsFromEvent(v.rows) ?? [] } : {}),
    ...(v.fenced === true ? { fenced: true } : {}),
    ...(v.reply !== undefined ? { reply: replyFromEvent(v.reply, requestId) } : {}),
    // EAC-2: a form the client cannot read is no form, and the card is the card of before.
    ...(taskEditOf(v.edit) ? { edit: taskEditOf(v.edit) } : {}),
    // Task 3978: a plain card that names a draft of the chat. Reject asks about it.
    ...(v.rows === undefined && v.reply === undefined && v.edit === undefined ? earlierFromEvent(v) : {}),
  };
}

/** An answer that approves: a plain `APPROVE`, or an Approve of rows. */
function isApproveAnswer(answer: string | undefined): boolean {
  return answer === "APPROVE" || (!!answer && answer.startsWith(ROWS_ANSWER_PREFIX));
}

function taskEditOf(raw: unknown): TaskEditForm | undefined {
  return (raw === undefined ? null : taskEditFromEvent(raw)) ?? undefined;
}

function withAdded(set: ReadonlySet<string>, key: string): Set<string> {
  const next = new Set(set);
  next.add(key);
  return next;
}

/**
 * The new card that takes the slot of the card it revises (§18.11 step 7).
 * `before` is the client's own copy of the member's last version, its text
 * and the people it left ticked, for the "Changed:" line.
 *
 * The client never changes the new card's ticks (fix round 2). The GATEWAY
 * starts each person the member unticked still unticked (rule 7), so the
 * ticks are the same with a slot, with no slot, after a remount and on a
 * replay.
 */
function redrafted(card: PendingConfirmation, old: PendingConfirmation): PendingConfirmation {
  const oldForm = old.reply && isNewMail(old.reply) ? old.reply : null;
  const text = old.sent ?? (old.reply ? { subject: old.reply.subject, body: old.reply.body } : undefined);
  let people: PeopleBefore | undefined;
  if (oldForm) {
    const named = !!old.sent && !!(old.sent.to || old.sent.cc || old.sent.bcc);
    people = { ticked: named && old.sent ? tickedOfSent(old.sent) : startTicks(oldForm).reply_all };
  }
  return {
    ...card,
    ...(text ? { before: { subject: text.subject, body: text.body, ...(people ? { people } : {}) } } : {}),
  };
}

/**
 * P3: every other slot that waits sees a new-mail card to its own people
 * arrive after it. Fix round 4: a card to OTHER people is no new draft of
 * the slot's card. That slot must read its end line, or the member's card
 * goes with no word (`endSlot`).
 */
function noteNewer(cards: PendingConfirmation[], at: number, card: PendingConfirmation): PendingConfirmation[] {
  if (!card.reply || !isNewMail(card.reply)) return cards;
  return cards.map((c, i) => (i !== at && c.redraft === "updating" && !c.newerCard && sharesTo(c.reply, card.reply) ? { ...c, newerCard: true as const } : c));
}

/**
 * What the end of a run does to one card that is a slot (§18.11 step 7). A
 * slot with a newer draft closes with no line, and any other slot says that
 * the draft did not change. Any other card stays.
 */
function endSlot(c: PendingConfirmation): PendingConfirmation[] {
  if (c.redraft !== "updating") return [c];
  return c.newerCard ? [] : [{ ...c, redraft: "unchanged" as const }];
}

function withRemoved(set: ReadonlySet<string>, key: string): Set<string> {
  const next = new Set(set);
  next.delete(key);
  return next;
}

export function confirmationReducer(
  state: ConfirmationQueueState,
  action: ConfirmationAction,
): ConfirmationQueueState {
  switch (action.type) {
    case "requested": {
      const card = cardFromEvent(action.value);
      if (!card) return state;
      // §18.11 fix round 5: the SAME card, shown again because the revise of a
      // chat message did not run. It takes its slot back, in place, and the
      // chat keeps the member's edits by its key. Only a slot reopens. A live
      // card of that key reads it as a replay, below, and keeps its `before`.
      const reopened = !!(card.reply && isNewMail(card.reply) && card.reply.reopened);
      const was = state.cards.findIndex((c) => c.key === card.key && c.redraft !== undefined);
      if (was >= 0 && reopened) {
        const slot = state.cards[was];
        const cards = state.cards.slice();
        cards[was] = { ...card, ...(slot.sent ? { sent: slot.sent } : {}) };
        return { ...state, cards, resolved: withRemoved(state.resolved, card.key) };
      }
      // Fix round 6: no card of that key is here, because the card closed
      // while a Send was in flight. The member did not answer it in this tab,
      // so it shows again as a new card. A card that the member answered
      // stays closed.
      if (reopened && state.reopenable?.has(card.key) && !state.cards.some((c) => c.key === card.key)) {
        return {
          ...state,
          cards: [...state.cards, card],
          resolved: withRemoved(state.resolved, card.key),
          answering: withRemoved(state.answering, card.key),
          reopenable: withRemoved(state.reopenable, card.key),
          receipts: settleReceipt(state.receipts, card.key, (r) =>
            r.kind === "applying" || r.kind === "closed" ? null : r),
        };
      }
      if (state.resolved.has(card.key) || state.answering.has(card.key)) return state;
      const at = state.cards.findIndex((c) => c.key === card.key);
      if (at >= 0) {
        // The same card again (a replay). Keep its place in the queue, and
        // what the client knows of it (§18.11).
        const prev = state.cards[at];
        const cards = state.cards.slice();
        cards[at] = { ...card, ...(prev.before ? { before: prev.before } : {}), ...(prev.sent ? { sent: prev.sent } : {}) };
        return { ...state, cards };
      }
      // §18.11 step 7: the new draft takes the slot of the card it revises,
      // in the same tab. The server names that card (`revises`), and the
      // client's own copy of the member's last version is its `before`.
      const revises = card.reply?.revises;
      const slot = revises ? state.cards.findIndex((c) => c.key === revises && c.redraft !== undefined) : -1;
      if (slot >= 0) {
        const old = state.cards[slot];
        const cards = state.cards.slice();
        cards[slot] = redrafted(card, old);
        return { ...state, cards: noteNewer(cards, slot, card) };
      }
      // Fix round 2: a new mail that names no slot while a slot waits (one
      // message revised two cards, so the gateway linked none) takes a slot
      // that waits, in its tab. Fix round 3: only the slot of a card to the
      // same people (`sharesTo`, the gateway's own rule). So a new mail to
      // another person, in the same message, shows as its own card. The
      // client cannot tell which of two such cards it replaces, so it shows
      // no "Changed:" line. The ticks are the server's in each case.
      const free = !revises && card.reply && isNewMail(card.reply)
        ? state.cards.findIndex((c) => c.redraft === "updating" && sharesTo(c.reply, card.reply))
        : -1;
      if (free >= 0) {
        const cards = state.cards.slice();
        cards[free] = card;
        return { ...state, cards: noteNewer(cards, free, card) };
      }
      return { ...state, cards: noteNewer([...state.cards, card], state.cards.length, card) };
    }
    case "resolved": {
      const key = action.requestId;
      if (!key) return state;
      const shown = state.cards.find((c) => c.key === key);
      if (action.answer === REVISED_ANSWER && shown?.requestId && isNewMail(shown.reply)) {
        // §18.11 step 7: a chat message closed a new-mail card. Its slot
        // stays, with no buttons, until the new draft takes it. A card of
        // any other kind closes as before.
        return {
          ...state,
          cards: state.cards.map((c) => (c.key === key ? { ...c, redraft: "updating" as const } : c)),
          resolved: withAdded(state.resolved, key),
          answering: withRemoved(state.answering, key),
          errors: withoutError(state.errors, key),
        };
      }
      // RESUME-3a: another tab or device approved a parked card that shows
      // here, and this tab did not answer it. The card leaves with the same
      // line as an Approve of this tab (§4.12.4 item 5).
      const approvedElsewhere = !!shown && action.elsewhere === true && isApproveAnswer(action.answer) &&
        !state.answering.has(key) && !state.receipts.some((r) => r.key === key);
      return {
        ...state,
        cards: state.cards.filter((c) => c.key !== key),
        resolved: withAdded(state.resolved, key),
        answering: withRemoved(state.answering, key),
        errors: withoutError(state.errors, key),
        ...(approvedElsewhere && shown
          ? {
              receipts: [...state.receipts, {
                key, label: receiptLabel(shown), kind: "approved" as const, intent: "approved" as const,
                afterId: action.afterId ?? null,
              }],
            }
          : {}),
        // The server closed it. An answer in flight keeps "Applying" until
        // its own POST says what became of it: the close can be a timeout.
        // Fix round 6: a chat message closed a card with no slot here, so a
        // `reopened` ask for it may follow.
        ...(action.answer === REVISED_ANSWER && !shown
          ? { reopenable: withAdded(state.reopenable ?? new Set(), key) }
          : {}),
      };
    }
    case "answering": {
      const receipt: ConfirmationReceipt | null = action.label
        ? {
            key: action.key,
            label: action.label,
            kind: "applying",
            intent: action.intent ?? "approved",
            afterId: action.afterId ?? null,
            ...(action.undo ? { undo: true } : {}),
          }
        : null;
      return {
        ...state,
        cards: state.cards.filter((c) => c.key !== action.key),
        answering: withAdded(state.answering, action.key),
        errors: withoutError(state.errors, action.key),
        receipts: receipt
          ? [...state.receipts.filter((r) => r.key !== action.key), receipt]
          : state.receipts,
      };
    }
    case "released":
      return {
        ...state,
        receipts: settleReceipt(state.receipts, action.key, (r) => ({ ...r, undo: undefined })),
      };
    case "undoOffered":
      // Only a line of THIS chat that the member approved, and only once. A
      // request id that no line here holds (another chat) attaches nothing.
      // A "Not done" line (task 3827) wrote nothing, so it offers no Undo.
      return {
        ...state,
        receipts: settleReceipt(state.receipts, action.requestId, (r) =>
          r.intent !== "approved" || r.kind === "refused" || r.kind === "notDone" || r.archive
            ? r
            : { ...r, archive: { ...action.undo, state: "ready" } }),
      };
    case "undoState":
      return {
        ...state,
        receipts: settleReceipt(state.receipts, action.key, (r) =>
          r.archive ? { ...r, archive: { ...r.archive, state: action.state } } : r),
      };
    case "answered":
      // The server took the answer. Its `confirmation_resolved` follows, and
      // this keeps a replay that arrives first from reopening the card.
      return {
        ...state,
        resolved: withAdded(state.resolved, action.key),
        answering: withRemoved(state.answering, action.key),
        // A "Not done" line stays: the stream can beat this POST's answer.
        receipts: settleReceipt(state.receipts, action.key, (r) =>
          (r.kind === "notDone" ? r : { ...r, kind: r.intent, undo: undefined })),
        // The member answered it, so a `reopened` ask never shows it again.
        ...(state.reopenable?.has(action.key) ? { reopenable: withRemoved(state.reopenable, action.key) } : {}),
      };
    case "notDone":
      return {
        ...state,
        receipts: settleReceipt(state.receipts, action.requestId, (r) =>
          r.intent === "approved" && (r.kind === "applying" || r.kind === "approved")
            ? { ...r, kind: "notDone", undo: undefined }
            : r),
      };
    case "rowResults":
      return {
        ...state,
        receipts: settleReceipt(state.receipts, action.requestId, (r) => ({ ...r, rowResults: action.rows })),
      };
    case "dropped":
      return {
        ...state,
        answering: withRemoved(state.answering, action.key),
        receipts: settleReceipt(state.receipts, action.key, (r) =>
          action.reason ? { ...r, kind: "refused", reason: action.reason } : null),
      };
    case "restore": {
      const key = action.card.key;
      const answering = withRemoved(state.answering, key);
      if (state.resolved.has(key)) {
        // The server closed the card, so it cannot come back. The line says
        // so, and a line that already settled stays as it is.
        const receipts = settleReceipt(state.receipts, key, (r) =>
          r.kind === "applying" ? { ...r, kind: "closed" } : r);
        return { ...state, answering, receipts };
      }
      // The card comes back, so its "Applying" line goes.
      const receipts = settleReceipt(state.receipts, key, (r) => (r.kind === "applying" ? null : r));
      const errors = action.reason ? new Map(state.errors).set(key, action.reason) : state.errors;
      if (state.cards.some((c) => c.key === key)) return { ...state, answering, receipts, errors };
      // First, so the member sees the card they tried to answer.
      return { ...state, cards: [action.card, ...state.cards], answering, errors, receipts };
    }
    case "dismiss":
      return { ...state, cards: state.cards.filter((c) => c.key !== action.key) };
    case "memberVersion":
      return {
        ...state,
        cards: state.cards.map((c) => (c.key === action.key ? { ...c, sent: action.version } : c)),
      };
    case "runFinalized":
      // A finished run waits on nothing. A non-blocking card stays: its
      // answer is a new chat message. A slot of a redraft that no new card
      // took reads "The draft did not change." until `closeUnchanged`.
      return {
        ...state,
        cards: state.cards.flatMap((c) => (c.redraft || !c.requestId ? endSlot(c) : [])),
      };
    case "runRested":
      // A Stop, a failure or an end on the attach stream: the slot branch of
      // `runFinalized` alone, so a blocking card stays as it is.
      return state.cards.some((c) => c.redraft === "updating")
        ? { ...state, cards: state.cards.flatMap(endSlot) }
        : state;
    case "closeUnchanged":
      return { ...state, cards: state.cards.filter((c) => c.redraft !== "unchanged") };
    case "reset":
      return EMPTY_CONFIRMATIONS;
  }
}

/** The action for a card once its POST settled (`lib/respondInput.ts`).
 *  `answered` (HRD-16): someone answered the card first. The card leaves,
 *  and its line says so. Nothing sends again. */
export function settleAnswer(
  card: PendingConfirmation,
  outcome: "ok" | "drop" | "retry" | "answered",
  reason?: string,
): ConfirmationAction {
  if (outcome === "retry") return { type: "restore", card, ...(reason ? { reason } : {}) };
  if (outcome === "answered") return { type: "dropped", key: card.key, reason: reason || ANSWERED_ONCE_TEXT };
  if (outcome === "drop") return { type: "dropped", key: card.key, ...(reason ? { reason } : {}) };
  return { type: "answered", key: card.key };
}
