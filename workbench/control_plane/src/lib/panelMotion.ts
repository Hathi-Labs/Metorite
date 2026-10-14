"use client";

/**
 * THE one motion of a side panel: how a panel at the edge of the page opens,
 * closes, and grows to the full view (owner, 2026-10-14: "Animate the opening
 * and closing of the AI chat sidebar similar to how the left-hand side panel
 * opens and closes").
 *
 * **The reference is the left sidebar.** `.sidebar-rail` in `globals.css`
 * eases its width over 320 ms on `cubic-bezier(0.22, 1, 0.36, 1)`, and the
 * page beside it reflows. Those two values were written into that one rule.
 * They now live in `:root` as tokens, and both panels read them:
 *
 *   --panel-duration         320ms  open and close, both panels
 *   --panel-easing           cubic-bezier(0.22, 1, 0.36, 1)
 *   --panel-expand-duration  260ms  the side panel to the full view and back
 *   --sheet-duration         150ms  a phone sheet (the shell drawer's motion)
 *   --sheet-easing           ease-out
 *
 * This file holds the same numbers for the script half (a settle timer, a
 * view transition), and `panelMotion.test.ts` fails when the two differ, or
 * when either panel stops reading the tokens.
 *
 * **The phases.** A panel that animates has four: `closed`, `opening`,
 * `open` and `closing`. The transition runs only while it moves, so a drag
 * of the panel's edge never lags behind the pointer. A closed panel stays
 * MOUNTED, hidden and inert, so a live reply, the draft and the scroll
 * survive a close (WS-51 continuity).
 *
 * **Reduced motion** skips the two moving phases. The panel opens and closes
 * at once, and the full-view switch is instant.
 */

import { useCallback, useEffect, useState } from "react";
import { flushSync } from "react-dom";

// ── The tokens ───────────────────────────────────────────────────────────────

/** The script half of the tokens. `globals.css` is the source. */
export const PANEL_MOTION = {
  /** `--panel-duration`: the left sidebar's fold, and the assistant's open and close. */
  durationMs: 320,
  /** `--panel-easing`: one curve for every panel move. It eases out, with no bounce. */
  easing: "cubic-bezier(0.22, 1, 0.36, 1)",
  /** `--panel-expand-duration`: the side panel to the full view, and back. */
  expandMs: 260,
  /** `--sheet-duration`: a phone sheet, as the shell drawer moves (`.chat-fade-in`). */
  sheetMs: 150,
  /** `--sheet-easing`. */
  sheetEasing: "ease-out",
} as const;

/** The CSS name of each token, and its value, as `globals.css` must declare it. */
export const PANEL_TOKENS: Readonly<Record<string, string>> = {
  "--panel-duration": `${PANEL_MOTION.durationMs}ms`,
  "--panel-easing": PANEL_MOTION.easing,
  "--panel-expand-duration": `${PANEL_MOTION.expandMs}ms`,
  "--sheet-duration": `${PANEL_MOTION.sheetMs}ms`,
  "--sheet-easing": PANEL_MOTION.sheetEasing,
};

/**
 * Time after the transition's own length before the timer settles the
 * phase. `transitionend` usually comes first. The timer is there for the
 * cases where it never comes: a hidden tab, or a transition that a style
 * change cancelled.
 */
export const SETTLE_SLACK_MS = 80;

// ── The phases ───────────────────────────────────────────────────────────────

export type PanelPhase = "closed" | "opening" | "open" | "closing";

/**
 * What can happen to a panel. `open-now` and `close-now` are the reduced
 * motion forms, with no moving phase. `settled` is the end of a transition.
 */
export type PanelEvent = "open" | "close" | "open-now" | "close-now" | "settled";

/**
 * The next phase. A reversal mid-way goes straight to the other moving
 * phase, and the browser runs the transition back from where it is.
 */
export function nextPhase(phase: PanelPhase, event: PanelEvent): PanelPhase {
  switch (event) {
    case "open":
      return phase === "open" ? "open" : "opening";
    case "close":
      return phase === "closed" ? "closed" : "closing";
    case "open-now":
      return "open";
    case "close-now":
      return "closed";
    case "settled":
      if (phase === "opening") return "open";
      if (phase === "closing") return "closed";
      return phase;
  }
}

/** The panel is on screen, or on its way there. */
export function isShown(phase: PanelPhase): boolean {
  return phase === "opening" || phase === "open";
}

/** A transition is running, so the motion class is on. */
export function isMoving(phase: PanelPhase): boolean {
  return phase === "opening" || phase === "closing";
}

/** The form a panel draws in, for its motion: which token and which property. */
export type PanelForm = "dock" | "full" | "sheet";

/** How long a form's open or close takes. */
export function formDurationMs(form: PanelForm): number {
  if (form === "full") return PANEL_MOTION.expandMs;
  if (form === "sheet") return PANEL_MOTION.sheetMs;
  return PANEL_MOTION.durationMs;
}

/** The member asked the OS for less motion. False on the server. */
export function prefersReducedMotion(): boolean {
  try {
    return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/**
 * The phase of a panel that follows `open`. `settle` ends a moving phase
 * early, from the panel's `transitionend`.
 *
 * ⚠️ An open waits TWO frames before it moves. The panel mounts on its first
 * open, and a transition needs one painted frame of the closed shape to
 * start from. With one frame, the first open of a session could snap.
 */
export function usePanelPhase(open: boolean, durationMs: number): { phase: PanelPhase; settle: () => void } {
  const [phase, setPhase] = useState<PanelPhase>("closed");

  useEffect(() => {
    const reduced = prefersReducedMotion();
    if (open) {
      if (reduced) {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- follows `open`
        setPhase((p) => nextPhase(p, "open-now"));
        return;
      }
      let second = 0;
      const first = requestAnimationFrame(() => {
        second = requestAnimationFrame(() => setPhase((p) => nextPhase(p, "open")));
      });
      return () => {
        cancelAnimationFrame(first);
        cancelAnimationFrame(second);
      };
    }
    setPhase((p) => nextPhase(p, reduced ? "close-now" : "close"));
  }, [open]);

  useEffect(() => {
    if (!isMoving(phase)) return;
    const t = setTimeout(() => setPhase((p) => nextPhase(p, "settled")), durationMs + SETTLE_SLACK_MS);
    return () => clearTimeout(t);
  }, [phase, durationMs]);

  const settle = useCallback(() => setPhase((p) => nextPhase(p, "settled")), []);
  return { phase, settle };
}

// ── Focus ────────────────────────────────────────────────────────────────────

/** The part of an element this file reads, so a test can pass a plain object. */
export interface FocusCandidate {
  isConnected: boolean;
  getClientRects(): { length: number };
  focus(options?: FocusOptions): void;
}

/**
 * Where focus goes when a panel closes: the first candidate that is still in
 * the document, takes space on screen, and is not inside the panel. The
 * caller passes the opener first, then the panel's own toggle.
 */
export function pickFocusReturn<T extends FocusCandidate>(
  candidates: readonly (T | null | undefined)[],
  inside: (el: T) => boolean,
): T | null {
  for (const el of candidates) {
    if (!el || !el.isConnected || inside(el)) continue;
    if (el.getClientRects().length === 0) continue;
    return el;
  }
  return null;
}

// ── The full-view switch ─────────────────────────────────────────────────────

/** The `view-transition-name` of the panel while it changes form. */
export const PANEL_SWAP_NAME = "assistant-panel";
/** The class on `<html>` that scopes the swap's CSS to this one transition. */
export const PANEL_SWAP_CLASS = "panel-swap";

/** Grows with each swap, so only the newest one cleans up. */
let _swapSeq = 0;

interface ViewTransitionLike {
  finished: Promise<unknown>;
}
type StartViewTransition = (update: () => void) => ViewTransitionLike;

/**
 * Change the panel's form inside a view transition, where the browser has
 * one and the member has not asked for less motion. The browser keeps a
 * picture of each form, so the panel's edge glides from the side to the full
 * view while the two pictures cross-fade. The chat stays the same element,
 * so a live reply, the draft and the scroll are untouched.
 *
 * Without the API, or with reduced motion, the change is instant. Returns
 * true when a transition ran.
 */
export function swapPanelForm(panel: HTMLElement | null, update: () => void): boolean {
  const doc = typeof document !== "undefined" ? document : null;
  const start = (doc as (Document & { startViewTransition?: StartViewTransition }) | null)?.startViewTransition;
  if (!doc || !panel || typeof start !== "function" || prefersReducedMotion()) {
    update();
    return false;
  }
  panel.style.setProperty("view-transition-name", PANEL_SWAP_NAME);
  doc.documentElement.classList.add(PANEL_SWAP_CLASS);
  // ⚠️ A second press during a swap starts a new transition, and the browser
  // skips the first. The first one's end then comes DURING the second. Only
  // the newest swap may clean up, or the second loses its name mid-way.
  const mine = ++_swapSeq;
  const done = () => {
    if (mine !== _swapSeq) return;
    panel.style.removeProperty("view-transition-name");
    doc.documentElement.classList.remove(PANEL_SWAP_CLASS);
  };
  try {
    // `flushSync`: the browser takes its "after" picture when this returns,
    // so React must have drawn the new form by then.
    const vt = start.call(doc, () => flushSync(update));
    vt.finished.then(done, done);
    return true;
  } catch {
    done();
    update();
    return false;
  }
}
