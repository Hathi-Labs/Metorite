/**
 * waitingDock — the rules of the waiting dock of the chat (WD-1).
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §24.11 (owner, 2026-11-27,
 * D234). With `WAITING_DOCK_ORGS` on, every card that waits on the member
 * draws in a dock above the message box, outside the scrolling thread. The
 * thread keeps one line at the old place. The dock never scrolls away, it may
 * fold to one row, and it has a height cap.
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

/** The kind of a dock item: a write to approve, or a question card. */
export type DockKind = "approval" | "question";

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
 * A slot of a redraft waits on the assistant, so it never counts.
 */
export function dockCount(src: DockSources): number {
  const approvals = src.confirmations.filter((c) => !c.redraft).length;
  return approvals + (src.elicitation ? 1 : 0) + (src.userInput ? 1 : 0);
}

/**
 * The dock draws only while it holds a tab: an approval, a slot of a redraft
 * (which keeps its tab, as in the queue today), or a question card that
 * waits. With the flag off it never draws.
 */
export function dockShown(on: boolean, src: DockSources): boolean {
  if (!on) return false;
  return src.confirmations.length > 0 || !!src.elicitation || !!src.userInput;
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

/** The label of the fold row's button. It only opens the card (audit rule 2). */
export function unfoldLabel(kind: DockKind): string {
  return kind === "approval" ? "Review" : "Answer";
}

// ── The kind of input an item needs (K2, audit rule 6) ────────────────────

/**
 * Picks only: the member answers with a press, and no keyboard is needed. An
 * approval of any kind is picks only (its fields are optional edits). A
 * question card is picks only when every question has options and no free
 * text. Any other card is a text item.
 */
export function picksOnly(item: { kind: DockKind; questions?: readonly DockQuestionShape[] }): boolean {
  if (item.kind === "approval") return true;
  const qs = item.questions ?? [];
  return qs.length > 0 && qs.every((q) => (q.options?.length ?? 0) > 0 && q.allowFreeformInput === false);
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
 * Is the dock folded to one row? K3 first: a field of the card that holds
 * the focus keeps the item open. K7 next: with the phone's keyboard up and no
 * field of the card in focus, the dock folds. Else the member's own choice.
 */
export function dockFolded(st: { manual: boolean; keyboardUp: boolean; cardFieldFocus: boolean }): boolean {
  if (st.cardFieldFocus) return false;
  if (st.keyboardUp) return true;
  return st.manual;
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

/** Two lines of the thread stay in view over the dock (F1): about 3rem. */
export const TWO_LINES_PX = 48;

export interface CapInput {
  /** A phone (the phone chrome, or a touch keyboard). */
  phone: boolean;
  /** The height of the chat column, in px. */
  chat: number;
  /** The height of the visual viewport, in px, or null before it is read. */
  viewport: number | null;
  /** The height of the form without the dock: the box and its rows. */
  composer: number;
  keyboardUp: boolean;
  /** A field of the card holds the focus (K3). */
  cardFieldFocus: boolean;
  /** The member pressed Expand. */
  expanded: boolean;
}

/**
 * The most height the dock may take, in px. The cap counts the dock only.
 *
 * - A desktop: 50% of the chat height.
 * - A phone with the keyboard down: 60% of the visual viewport.
 * - A phone with the keyboard up and a field of the card in focus: the
 *   visual viewport less the composer less two lines of the thread.
 * - Expand: the chat height less the composer less two lines of the thread.
 *   It never covers the composer.
 * - No cap is over that room, so two lines of the thread always show (F1).
 */
export function dockCap(st: CapInput): number {
  const room = (h: number) => Math.max(0, Math.floor(h - st.composer - TWO_LINES_PX));
  const vp = st.viewport ?? st.chat;
  let base: number;
  if (!st.phone) base = Math.floor(st.chat * 0.5);
  else if (st.keyboardUp && st.cardFieldFocus) base = room(vp);
  else base = Math.floor(vp * 0.6);
  // No cap ever covers two lines of the thread (F1).
  const most = room(st.chat);
  return st.expanded ? most : Math.min(base, most);
}
