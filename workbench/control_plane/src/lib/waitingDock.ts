/**
 * waitingDock — the rules of the waiting dock of the chat (WD-1).
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §24.11 (owner, 2026-11-27,
 * D234, D9). With `WAITING_DOCK_ORGS` on, each QUESTION card that waits on
 * the member draws in a dock above the message box, outside the scrolling
 * thread. The thread keeps one line at the old place. The dock never scrolls
 * away, it may fold to one row, and it has a height cap.
 *
 * D9 (2026-11-27): an approval card never draws in the dock. It draws in the
 * thread, exactly as with the flag off. While one waits, the dock holds ONE
 * pointer row that names it. Its Review only scrolls the thread to the card.
 *
 * This file decides WHAT the dock holds, HOW TALL it may be, WHEN it folds and
 * WHEN the focus may move (K1, K2, K3, K6, K7). It reads the state that
 * `AgentChat` already holds (the queue and the two question cards) and keeps
 * none of its own. The tabs are `lib/cardTabs.ts`, the one tab model.
 *
 * Pure, so the node-env vitest holds it. Fence: `src/lib/waitingDock.test.ts`.
 */

import { refocusQuietly } from "@/lib/composerFocus";
import type { PendingConfirmation } from "@/lib/confirmationQueue";
import { nearestScrollTop, type Span } from "@/lib/scrollWithin";

/** One question of a question card, as the dock reads it. */
export interface DockQuestionShape {
  options?: readonly unknown[] | null;
  allowFreeformInput?: boolean;
}

/** What the dock reads from `AgentChat`. Each field is the state of today. */
export interface DockSources {
  /** The queue, with the slots of a redraft (§18.11). */
  confirmations: readonly Pick<PendingConfirmation, "key" | "redraft">[];
  /** The `ask_questions` card, only while it waits (not answered or declined). */
  elicitation: { questions: readonly DockQuestionShape[] } | null;
  /** The `ask_user` card, only while it waits. */
  userInput: { choices: readonly string[]; allowFreeform: boolean } | null;
}

/**
 * How many items wait on the member: each approval card that is not a slot,
 * and each question card. A card of many questions is one item (one tab).
 * A slot of a redraft waits on the assistant, so it never counts. The count
 * holds the approvals in the thread too (D9): the thread line and the live
 * region read it.
 */
export function dockCount(src: DockSources): number {
  return waitingApprovals(src) + dockQuestionCount(src);
}

/** The approvals that wait on the member, in the thread (no slot). */
export function waitingApprovals(src: DockSources): number {
  return src.confirmations.filter((c) => !c.redraft).length;
}

/**
 * The question cards in the dock. Only these move the focus or the fold (K1,
 * K2, K6, K7). An approval waits in the thread, so it moves nothing there (D9).
 */
export function dockQuestionCount(src: DockSources): number {
  return (src.elicitation ? 1 : 0) + (src.userInput ? 1 : 0);
}

/**
 * The dock draws only while it holds something: a question card that waits,
 * or the pointer to an approval that waits (D9). A slot of a redraft waits on
 * the assistant, so alone it draws no pointer. With the flag off it never
 * draws.
 */
export function dockShown(on: boolean, src: DockSources): boolean {
  if (!on) return false;
  return waitingApprovals(src) > 0 || dockQuestionCount(src) > 0;
}

// ── The words ─────────────────────────────────────────────────────────────

/** "1 item" or "N items". */
function items(n: number): string {
  return n === 1 ? "1 item" : `${n} items`;
}

/** The one line in the thread at the old place of the cards (WD-D1). */
export function threadLine(n: number): string {
  return `Waiting for you: ${items(n)}, below`;
}

/** The words of the polite live region (the a11y line). */
export function liveWords(n: number): string {
  return n === 1 ? "1 item waits for you" : `${n} items wait for you`;
}

/**
 * What the live region says after the count moved from `prev` to `n`, or
 * null to say nothing new. It speaks only when the count goes up.
 */
export function liveAnnouncement(prev: number, n: number): string | null {
  return n > prev && n > 0 ? liveWords(n) : null;
}

/**
 * The words of the live region after the count moved from `prev` to `n`. A
 * rise says the new count. A fall clears the words, so each later rise is a
 * change that a screen reader reads, also when its words repeat (2, 1, 2 and
 * 1, 0, 1 each speak twice). No change keeps the words.
 */
export function liveAfter(prev: number, n: number, words: string): string {
  const said = liveAnnouncement(prev, n);
  if (said) return said;
  return n < prev ? "" : words;
}

/** The label of the fold row's button. It only opens the card (audit rule 2). */
export const UNFOLD_LABEL = "Answer";

/** The lead words of the pointer row (D9). */
export const POINTER_LEAD = "Waits for you:";

/** The words after the label when more approvals wait: "and 2 more". */
export function pointerMore(n: number): string {
  return n > 0 ? `and ${n} more` : "";
}

/** The whole line of the pointer row, as a screen reader and a test read it. */
export function pointerLine(label: string, more: number): string {
  return [POINTER_LEAD, label, pointerMore(more)].filter(Boolean).join(" ");
}

/**
 * The newest approval that waits, for the pointer row: the last card of the
 * queue that is not a slot, and how many others wait. Null when none waits.
 */
export function newestApproval<T extends Pick<PendingConfirmation, "redraft">>(
  cards: readonly T[],
): { card: T; more: number } | null {
  const live = cards.filter((c) => !c.redraft);
  const card = live[live.length - 1];
  return card ? { card, more: live.length - 1 } : null;
}

/**
 * The approval that the pointer row names (2026-11-28): the card that the
 * queue in the thread shows now, its OPEN tab, and how many others wait. The
 * queue reports the key of its open tab, so the row follows a change of tab.
 * Before the first report, or when the open tab is a slot of a redraft, the
 * row names the newest card that waits. Null when none waits.
 */
export function openApproval<T extends Pick<PendingConfirmation, "key" | "redraft">>(
  cards: readonly T[],
  openKey: string | null,
): { card: T; more: number } | null {
  const live = cards.filter((c) => !c.redraft);
  const card = live.find((c) => c.key === openKey);
  return card ? { card, more: live.length - 1 } : newestApproval(cards);
}

/**
 * The `scrollTop` of the thread after the pointer's Review (D9). A card that
 * fits shows whole, with the least movement. A card taller than the thread's
 * room shows its TOP, so the member reads it from the start.
 */
export function pointerScrollTop(scrollTop: number, port: Span, target: Span): number {
  const taller = target.bottom - target.top > port.bottom - port.top;
  if (taller) return scrollTop + (target.top - port.top);
  return nearestScrollTop(scrollTop, port, target);
}

// ── The kind of input an item needs (K2, audit rule 6) ────────────────────

/**
 * Picks only: the member answers with a press, and no keyboard is needed. A
 * question card is picks only when every question has options (audit rule
 * 6). Its "Other" field is optional, and opens a keyboard only on a tap (K3).
 * A card with any question that has no options is a text item. An approval
 * is in the thread (D9), so the dock never reads it here.
 */
export function picksOnly(questions: readonly DockQuestionShape[]): boolean {
  return questions.length > 0 && questions.every((q) => (q.options?.length ?? 0) > 0);
}

/** The questions of the `ask_user` card, in the shape `picksOnly` reads. */
export function userInputShape(u: { choices: readonly string[]; allowFreeform: boolean }): DockQuestionShape[] {
  return [{ options: u.choices, allowFreeformInput: u.allowFreeform }];
}

// ── The keyboard (K1, K3, K6, K7) ─────────────────────────────────────────

/** A key in the box this recently means that the member types (K1). */
export const TYPING_MS = 3000;

/** The soft keyboard is up when the visual viewport lost this much height. */
export const KEYBOARD_PX = 150;

/**
 * The phone's keyboard is up: the visual viewport is shorter than the layout
 * viewport by more than a bar's height. A desktop never has it up.
 */
export function keyboardUp(st: { phone: boolean; viewport: number | null; layout: number }): boolean {
  if (!st.phone || st.viewport === null || st.layout <= 0) return false;
  return st.layout - st.viewport > Math.max(KEYBOARD_PX, st.layout * 0.25);
}

/** The member types: the box holds text, or a key went down in the last 3 s. */
export function typing(st: { boxText: string; msSinceKey: number }): boolean {
  return st.boxText.trim() !== "" || st.msSinceKey < TYPING_MS;
}

/**
 * K1 and K2: on the arrival of an item, may the chat blur the message box?
 * Only on a phone, only when the box has the focus and the member does not
 * type, and only for a picks-only item. The blur drops the keyboard, so the
 * dock unfolds to its cap. It is the one move of the focus that an arrival
 * makes (audit rule 6). A text item never moves the focus (K3).
 */
export function arrivalBlursBox(st: {
  phone: boolean;
  boxFocused: boolean;
  boxText: string;
  msSinceKey: number;
  picksOnly: boolean;
}): boolean {
  if (!st.phone || !st.boxFocused || !st.picksOnly) return false;
  return !typing(st);
}

/**
 * K6: when the last item leaves, may the message box take the focus? A
 * desktop only. On a phone the keyboard stays down. This is the rule of
 * `lib/composerFocus.ts` for an act that no press in the composer started.
 */
export function focusBoxAfterLast(st: { touch: boolean }): boolean {
  return refocusQuietly(st);
}

/**
 * Is the dock folded to one row? The member's own Fold always wins. Else K7:
 * with the phone's keyboard up, the dock folds, except while a field of the
 * card has the focus (K3). A field in focus only stops the automatic fold.
 */
export function dockFolded(st: { manual: boolean; keyboardUp: boolean; cardFieldFocus: boolean }): boolean {
  if (st.manual) return true;
  return st.keyboardUp && !st.cardFieldFocus;
}

/** K2: a new item opens the dock to its cap, also after a fold by hand. */
export function arrivalUnfolds(prev: number, n: number): boolean {
  return n > prev;
}

/** True for an element that opens a keyboard: a text field (K3). */
export function isTextField(el: { tagName?: string; type?: string; isContentEditable?: boolean } | null): boolean {
  if (!el) return false;
  if (el.isContentEditable) return true;
  const tag = (el.tagName ?? "").toUpperCase();
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag !== "INPUT") return false;
  const t = (el.type ?? "text").toLowerCase();
  return !["button", "checkbox", "radio", "submit", "reset", "file", "range", "color", "image", "hidden"].includes(t);
}

// ── The height cap (audit rule 5) ─────────────────────────────────────────

/**
 * Rule 10 (owner, 2026-11-27): the Submit row of the open question never
 * scrolls in the dock. It is the card's own row, so the dock draws no copy of
 * a button. The same selector is the rule in `globals.css`.
 */
export const PINNED_ACTIONS = "div:has(> [data-elicitation-submit])";

/** The least height of the dock, so the Submit row and a part of the body
 *  always show, even on a very small screen. */
export const MIN_DOCK_PX = 160;

// ── The fresh press (24.11 rule 7, round 7) ───────────────────────────────

/**
 * A commit needs a FRESH press. A held key (`repeat`) never presses Submit or
 * Not now: its default action, the click, does not run. The card's arm does
 * the rest (`lib/freshPress.ts`): a commit waits `ARM_MS` after each change
 * of what the member sees of the card.
 */
export const FRESH_KEY_PROPS = {
  onKeyDown: (e: { repeat: boolean; preventDefault: () => void }) => {
    if (e.repeat) e.preventDefault();
  },
};

/**
 * How long a question card in the dock ignores a commit after it shows, in
 * ms (24.11 rule 7). It is the value of `ARM_MS` in `ConfirmationCard.tsx`,
 * which an approval keeps in the thread. A test holds the two equal.
 */
export const ARM_MS = 400;

/** The times of the last press of each kind, from `performance.now()`. */
export interface PressLog {
  pointerDownAt: number;
  pointerUpAt: number;
  /** The last keydown of Enter or Space that was not a repeat. */
  keyDownAt: number;
  /** The last keyup of Enter or Space. */
  keyUpAt: number;
  /** The last keydown that was a repeat (a held key). */
  repeatAt: number;
}

/** A click this soon after a press event is the click of that press. */
export const PRESS_LINK_MS = 100;

/**
 * Round 7, the fresh press: may this click commit, on a card armed at
 * `armedAt`? The press must begin after the arm. A pointer clicks as it
 * comes up, so its press began at the pointerdown. Space clicks at its keyup
 * and Enter at its keydown, so a key press began at the keydown. A held key
 * never presses. A click with no press near it (a screen reader) needs the
 * arm only.
 */
export function pressIsFresh(st: PressLog & { armedAt: number; now: number }): boolean {
  const near = (t: number) => st.now - t < PRESS_LINK_MS;
  if (near(st.repeatAt)) return false;
  if (near(st.pointerUpAt)) return st.pointerDownAt >= st.armedAt;
  if (near(st.keyUpAt) || near(st.keyDownAt)) return st.keyDownAt >= st.armedAt;
  return true;
}

/**
 * Round 7: does the message box drop this Enter? With the dock on, a held
 * Enter (`repeat`) never sends the box, and nor does an Enter within
 * `ARM_MS` of a commit in the dock. The key of a commit then never runs on
 * into the box. With the flag off, the box is the box of before.
 */
export function boxEnterBlocked(st: { dock: boolean; repeat: boolean; msSinceCommit: number }): boolean {
  return st.dock && (st.repeat || st.msSinceCommit < ARM_MS);
}

// ── The hold: nothing moves under the pointer (verifier F1, 2026-11-28) ───

/**
 * When an item leaves the dock, the dock keeps its height for at least this
 * long, in ms. In the place of the item it shows an inert line. A second
 * press of a double click then lands on that line, and never on the thread
 * that would grow into the gap.
 */
export const HOLD_MS = 800;

/** The dock collapses only this long after the last press in it, in ms. */
export const QUIET_MS = 600;

/** The collapse fades the inert line out over this time, in ms. Reduced
 *  motion: no fade. */
export const COLLAPSE_MS = 150;

/** Why an item left the dock. */
export type DockLeft = "sent" | "notnow" | "elsewhere";

/** The words of the inert line in the place of an item that left. */
export const LEFT_WORDS: Record<DockLeft, string> = {
  sent: "Answer sent",
  notnow: "Not now",
  elsewhere: "Answered elsewhere",
};

/**
 * Why a question card left the dock, from the state of the chat. The member
 * answered it (with Submit or with typed words), or declined it with Not
 * now. Any other end is "elsewhere": another tab answered it, the run ended,
 * or a new card took its place. The pointer row of the approvals always
 * leaves as "elsewhere", because the member never answers in that row.
 */
export function leftKind(card: { answered?: unknown; declined?: boolean } | null | undefined): DockLeft {
  if (card?.declined) return "notnow";
  return card && card.answered !== undefined ? "sent" : "elsewhere";
}

/**
 * Rule 2 of the hold: the time in ms until the dock may collapse, or draw
 * the next item. Zero when all three hold now: `HOLD_MS` passed since the
 * item left, no pointer is down, and `QUIET_MS` passed since the last
 * pointerdown or keydown in the dock. Null while a pointer is down, because
 * only its release can end the wait.
 */
export function collapseWait(st: { now: number; leftAt: number; pointerDown: boolean; lastPressAt: number }): number | null {
  if (st.pointerDown) return null;
  return Math.max(0, st.leftAt + HOLD_MS - st.now, st.lastPressAt + QUIET_MS - st.now);
}

/** A pointer that went down this long ago with no release counts as up. A
 *  lost `pointerup` never holds the dock for ever. */
export const STUCK_PRESS_MS = 10_000;

/** Two lines of the thread stay in view over the dock (F1): about 3rem. */
export const TWO_LINES_PX = 48;

export interface CapInput {
  /** A phone (the phone chrome, or a touch keyboard). */
  phone: boolean;
  /** The height of the chat column, in px. */
  chat: number;
  /** The height of the visual viewport, in px, or null before it is read. */
  viewport: number | null;
  /** The height of the form without the dock's body: the box, its rows and
   *  the pointer row. */
  composer: number;
  keyboardUp: boolean;
  /** A field of the card holds the focus (K3). */
  cardFieldFocus: boolean;
}

/**
 * The most height the dock may take, in px. The cap counts the dock only.
 * The dock is as tall as its content, up to the cap (rule 3).
 *
 * - A desktop: 40% of the chat column.
 * - A phone: 50% of the visual viewport.
 * - A phone with the keyboard up and a field of the card in focus: the
 *   visual viewport less the composer less two lines of the thread.
 * - No cap is over that room, so two lines of the thread always show (F1).
 */
export function dockCap(st: CapInput): number {
  const room = (h: number) => Math.max(0, Math.floor(h - st.composer - TWO_LINES_PX));
  const vp = st.viewport ?? st.chat;
  let base: number;
  if (st.phone && st.keyboardUp && st.cardFieldFocus) base = room(vp);
  else if (!st.phone) base = Math.floor(st.chat * 0.4);
  else base = Math.floor(vp * 0.5);
  // No cap ever covers two lines of the thread (F1). A very small screen
  // still keeps the Submit row and a part of the body (`MIN_DOCK_PX`).
  return Math.max(MIN_DOCK_PX, Math.min(base, room(st.chat)));
}

