"use client";

/**
 * freshPress — the arm of a commit in the waiting dock (WD-1, round 7,
 * `projects_ai_chat.md` §24.11 rule 7).
 *
 * The invariant: a commit is pressed only on a card that the member has seen,
 * whole and still, for `ARM_MS`, with a press that began after that. Each
 * event that makes a commit control show again arms the card again: a fold
 * and an unfold, a change of tab, Expanded, the earlier-draft question,
 * Collapse and Review. The card names that view in its arm key. A change of
 * the key drops the arm in the same render, and a new timer starts.
 *
 * The dock reads each click on a commit control in the capture phase
 * (`useArmed().guard`). It drops a click while the card is not armed, and a
 * click of a press that began before the arm. A held key never presses. A
 * click with no press (a screen reader) needs the arm only.
 *
 * The rules are pure, in `lib/waitingDock.ts` (`pressIsFresh`). This file
 * holds the window listeners and the hook. Fences:
 * `src/lib/waitingDock.test.ts` and `e2e/chat-waiting-dock.spec.ts`.
 */

import { useEffect, useState, type MouseEvent } from "react";

import { ARM_MS, pressIsFresh, type PressLog } from "@/lib/waitingDock";

export { ARM_MS };

/**
 * Every commit control of a card in the dock: a press that answers. The
 * dock's guard reads only these, so a tab, a tick, Review and Fold work at
 * once.
 */
export const COMMIT_CONTROLS = [
  "[data-confirmation-approve]",
  "[data-confirmation-reject]",
  "[data-reply-send]",
  "[data-reply-save]",
  "[data-reply-discard]",
  "[data-reply-close]",
  "[data-earlier-keep]",
  "[data-earlier-delete]",
  "[data-elicitation-submit]",
  "[data-not-now]",
].join(", ");

// ── The press log: one set of window listeners for the page ─────────────

const log: PressLog & { down: Set<string>; commitAt: number } = {
  pointerDownAt: Number.NEGATIVE_INFINITY,
  pointerUpAt: Number.NEGATIVE_INFINITY,
  keyDownAt: Number.NEGATIVE_INFINITY,
  keyUpAt: Number.NEGATIVE_INFINITY,
  repeatAt: Number.NEGATIVE_INFINITY,
  down: new Set<string>(),
  commitAt: Number.NEGATIVE_INFINITY,
};
const waiting = new Set<() => void>();
let installed = false;

function flushKeysUp() {
  if (log.down.size > 0) return;
  const fns = Array.from(waiting);
  waiting.clear();
  for (const fn of fns) fn();
}

function install() {
  if (installed || typeof window === "undefined") return;
  installed = true;
  const now = () => performance.now();
  window.addEventListener("pointerdown", () => { log.pointerDownAt = now(); }, true);
  window.addEventListener("pointerup", () => { log.pointerUpAt = now(); }, true);
  window.addEventListener("keydown", (e) => {
    log.down.add(e.key);
    if (e.repeat) log.repeatAt = now();
    else if (e.key === "Enter" || e.key === " ") log.keyDownAt = now();
  }, true);
  window.addEventListener("keyup", (e) => {
    log.down.delete(e.key);
    if (e.key === "Enter" || e.key === " ") log.keyUpAt = now();
    flushKeysUp();
  }, true);
  // A key that comes up in another window sends no keyup here.
  window.addEventListener("blur", () => {
    log.down.clear();
    flushKeysUp();
  });
}

/** Start the press log. The chat calls it while the dock is on. */
export function trackPresses(): void {
  install();
}

/**
 * Run `fn` when no key is down: at once, or at the next keyup that leaves
 * none down (K6, round 7). It returns a cancel.
 */
export function whenKeysUp(fn: () => void): () => void {
  install();
  if (log.down.size === 0) {
    fn();
    return () => {};
  }
  waiting.add(fn);
  return () => { waiting.delete(fn); };
}

/** Milliseconds since the dock last let a commit through. */
export function msSinceDockCommit(): number {
  return typeof performance === "undefined" ? Number.POSITIVE_INFINITY : performance.now() - log.commitAt;
}

/**
 * The arm of one card. `key` names what the member sees of it. `live` is
 * false while the card is not on the screen, and then it never arms. The
 * guard goes on the card's frame as `onClickCapture`.
 */
export function useArmed(key: string, live = true): { armed: boolean; guard: (e: MouseEvent<HTMLElement>) => void } {
  const [st, setSt] = useState<{ key: string; at: number | null }>({ key, at: null });
  // The key moved: the arm drops in this render, before any paint.
  if (st.key !== key) setSt({ key, at: null });
  useEffect(() => {
    install();
    if (!live) return;
    const t = setTimeout(() => setSt((s) => (s.key === key ? { key, at: performance.now() } : s)), ARM_MS);
    return () => clearTimeout(t);
  }, [key, live]);
  const at = live && st.key === key ? st.at : null;
  const guard = (e: MouseEvent<HTMLElement>) => {
    const control = (e.target as Element | null)?.closest?.(COMMIT_CONTROLS);
    if (!control || !e.currentTarget.contains(control)) return;
    if (at !== null && pressIsFresh({ ...log, armedAt: at, now: performance.now() })) {
      log.commitAt = performance.now();
      return;
    }
    e.preventDefault();
    e.stopPropagation();
  };
  return { armed: at !== null, guard };
}
