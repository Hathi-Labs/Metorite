/**
 * waitingDock — the rules of the row of the waiting dock (WD-1, D10).
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §24.11, D10 (2026-11-28).
 * With `WAITING_DOCK_ORGS` on, the chat draws ONE row of fixed height above
 * the message box while an element waits on the member: "Waits for you:",
 * the label of the card that the thread shows, "and N more", and Review.
 * Every card draws in the thread, exactly as with the flag off. The row holds
 * no card and no commit control. Review only scrolls the thread to the card.
 *
 * This file decides WHAT the row names and WHEN it takes or gives back its
 * height. It reads the sources of `lib/askPin.ts` and keeps none of its own.
 *
 * Pure, so the node-env vitest holds it. Fence: `src/lib/waitingDock.test.ts`.
 */

import { HITL_TARGET, pendingAsk, type AskSources } from "@/lib/askPin";
import { tabLabel } from "@/lib/cardTabs";
import { nearestScrollTop, type Span } from "@/lib/scrollWithin";

/** What the row draws. */
export interface WaitingRow {
  /** The label of the card that the thread shows. */
  label: string;
  /** How many other elements wait. */
  more: number;
  /** The `data-chat-ask` value of the element in the thread. */
  ask: string;
  /** The card inside that element, or null for the element itself. */
  inner: string | null;
}

/** The lead words of the row. */
export const ROW_LEAD = "Waits for you:";

/** The words of the row after its last element left. It is no button. */
export const NOTHING_WAITS = "Nothing waits";

/** The words after the label when more wait: "and 2 more". */
export function rowMore(n: number): string {
  return n > 0 ? `and ${n} more` : "";
}

/**
 * The row of the elements that wait, or null when none waits. The sources
 * are the sources of `AskPin`: approvals with no redraft slot, the two
 * question cards while they wait, and the generative-UI ask of the newest
 * turn. Each card is one element. A card of many questions is one element.
 *
 * The row names the open tab of the approval queue in the thread
 * (`openKey`, from `ConfirmationQueue` `onShown`). Before the first report it
 * names the first approval, which the queue opens then too. With no approval
 * it names the element that `AskPin` names.
 */
export function waitingRow(src: AskSources, openKey: string | null): WaitingRow | null {
  const cards = src.confirmations.filter((c) => !c.redraft);
  const questions = src.elicitation && src.elicitation.questions.length > 0 ? 1 : 0;
  const user = src.userInput ? 1 : 0;
  const genUi = pendingAsk({ ...src, confirmations: [], elicitation: null, userInput: null }) ? 1 : 0;
  const n = cards.length + questions + user + genUi;
  if (n === 0) return null;
  const open = cards.find((c) => c.key === openKey) ?? cards[0];
  if (open) return { label: tabLabel(open), more: n - 1, ask: HITL_TARGET, inner: "[data-confirmation-card]" };
  const first = pendingAsk({ ...src, confirmations: [] });
  if (!first) return null;
  const inThread = first.target === HITL_TARGET;
  return { label: first.title, more: n - 1, ask: first.target, inner: inThread ? '[data-elicitation-card="open"]' : null };
}

/**
 * The `scrollTop` of the thread after Review. A card that fits shows whole,
 * with the least movement. A card taller than the room of the thread shows
 * its TOP, so the member reads it from the start.
 */
export function reviewScrollTop(scrollTop: number, port: Span, target: Span): number {
  const taller = target.bottom - target.top > port.bottom - port.top;
  if (taller) return scrollTop + (target.top - port.top);
  return nearestScrollTop(scrollTop, port, target);
}

// ── The height of the row (D10 rule 3) ─────────────────────────────────────

/**
 * The three states of the row. `hidden` takes no height. `row` names what
 * waits. `empty` shows the inert "Nothing waits" at the same height.
 */
export type RowPhase = "hidden" | "row" | "empty";

/** The row keeps its height this long after the last pointer activity, in ms. */
export const HOLD_MS = 800;

/** A pointer that went down this long ago with no release counts as up. */
export const STUCK_PRESS_MS = 10_000;

/**
 * The next state of the row. Nothing moves while a pointer is down.
 *
 * - An arrival takes the height only when no pointer is down. A key press
 *   follows the focus and not the layout, so a keyboard needs no wait.
 * - When the last element leaves, the row shows "Nothing waits" at once, in
 *   the same place and at the same height.
 * - The row collapses only after `HOLD_MS` with no pointer down. Each
 *   pointerdown starts the wait again. `quietMs` is the time since the later
 *   of the leave and the last pointer release.
 * - An element that arrives while "Nothing waits" shows takes the row again.
 */
export function nextPhase(st: { phase: RowPhase; waiting: boolean; pointerDown: boolean; quietMs: number }): RowPhase {
  if (st.waiting) return st.phase === "hidden" && st.pointerDown ? "hidden" : "row";
  if (st.phase === "row") return "empty";
  if (st.phase === "empty") return !st.pointerDown && st.quietMs >= HOLD_MS ? "hidden" : "empty";
  return "hidden";
}
