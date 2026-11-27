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
export function unfoldLabel(kind: DockKind): string {
  return kind === "approval" ? "Review" : "Answer";
}

// ── The kind of input an item needs (K2, audit rule 6) ────────────────────

/**
 * Picks only: the member answers with a press, and no keyboard is needed. An
 * approval of any kind is picks only (its fields are optional edits). A
 * question card is picks only when every question has options (audit rule
 * 6). Its "Other" field is optional, and opens a keyboard only on a tap (K3).
 * A card with any question that has no options is a text item.
 */
export function picksOnly(item: { kind: DockKind; questions?: readonly DockQuestionShape[] }): boolean {
  if (item.kind === "approval") return true;
  const qs = item.questions ?? [];
  return qs.length > 0 && qs.every((q) => (q.options?.length ?? 0) > 0);
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
 * Rule 9 (owner, 2026-11-27): the action rows that never scroll in the dock.
 * Each is the card's own row, so the dock draws no copy of a button. The same
 * list is the rule in `globals.css`.
 */
export const PINNED_ACTIONS =
  "div:has(> [data-confirmation-approve]), div:has(> [data-reply-send]), div:has(> [data-dock-review]), div:has(> [data-elicitation-submit]), [data-earlier-question]";

/** The least height of the dock, so the action row and a part of the body
 *  always show, even on a very small screen. */
export const MIN_DOCK_PX = 160;

/**
 * The one line of the people of a send card in the dock (rule 9): the To
 * names that go, the count of each other row that goes, and the count of the
 * people left out. "To Kim, Ravi Kumar, Lena · Cc 2", "To Kim · 1 left out".
 */
export function peopleLine(
  rows: { to: readonly string[]; cc: readonly string[]; bcc?: readonly string[] },
  ticked: ReadonlySet<string>,
  name: (addr: string) => string,
): string {
  const on = (a: string) => ticked.has(a) || ticked.has(a.toLowerCase());
  const to = rows.to.filter(on).map(name);
  const cc = rows.cc.filter(on).length;
  const bcc = (rows.bcc ?? []).filter(on).length;
  const all = rows.to.length + rows.cc.length + (rows.bcc ?? []).length;
  const goes = rows.to.filter(on).length + cc + bcc;
  const parts = [to.length > 0 ? `To ${to.join(", ")}` : "To nobody"];
  if (cc > 0) parts.push(`Cc ${cc}`);
  if (bcc > 0) parts.push(`Bcc ${bcc}`);
  if (all > goes) parts.push(`${all - goes} left out`);
  return parts.join(" · ");
}

// ── Compact and Expanded (D2, D3, owner delegation of 2026-11-27) ─────────

/** The mode of the open card in the dock: the same card, drawn two ways. */
export type DockMode = "compact" | "expanded";

/** The body lines that the compact view of an email shows. */
export const PREVIEW_LINES = 3;

/** The first `PREVIEW_LINES` lines of a text, and whether more follow. */
export function previewLines(text: string): { lines: string[]; more: boolean } {
  const all = text.replace(/\s+$/, "").split(/\r?\n/);
  return { lines: all.slice(0, PREVIEW_LINES), more: all.length > PREVIEW_LINES };
}

/**
 * The consent rule (projects_ai_chat.md 24.11, 2026-11-27): a commit needs a
 * FRESH press. A held key (`repeat`) never presses a commit button: its
 * default action, the click, does not run. The card's arming does the rest:
 * a click in the first 400 ms after the Expanded view opens does nothing.
 */
export const FRESH_KEY_PROPS = {
  onKeyDown: (e: { repeat: boolean; preventDefault: () => void }) => {
    if (e.repeat) e.preventDefault();
  },
};

/** D3: the Send of an Expanded email names how many people get it. */
export function sendLabel(people: number): string {
  return `Send to ${people}`;
}

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
 * The dock is as tall as its content, up to the cap (D2, 2026-11-27).
 *
 * - Compact, a desktop: 40% of the chat column.
 * - Compact, a phone: 50% of the visual viewport.
 * - A phone with the keyboard up and a field of the card in focus: the
 *   visual viewport less the composer less two lines of the thread.
 * - Expanded: the chat column less the composer less two lines of the
 *   thread. It never covers the composer.
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
  // still keeps the action row and a part of the body (`MIN_DOCK_PX`).
  const most = room(st.chat);
  return Math.max(MIN_DOCK_PX, st.expanded ? most : Math.min(base, most));
}
