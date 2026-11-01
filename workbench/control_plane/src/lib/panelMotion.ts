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

/**
 * The form a panel draws in, for its motion: which token and which property.
 * `peek` is the phone's strip above the bottom nav (CF-1). It moves on the
 * sheet's short tokens.
 */
export type PanelForm = "dock" | "full" | "sheet" | "peek";

/** How long a form's open or close takes. */
export function formDurationMs(form: PanelForm): number {
  if (form === "full") return PANEL_MOTION.expandMs;
  if (form === "sheet" || form === "peek") return PANEL_MOTION.sheetMs;
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
  skipTransition?: () => void;
}
type StartViewTransition = (update: () => void | Promise<void>) => ViewTransitionLike;

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

// ── The Ask bar into the chat, and back ──────────────────────────────────────

/** The `view-transition-name` of the one box while it moves. */
export const ASK_MORPH_NAME = "ask-box";
/** The `view-transition-name` of the side panel while the box flies into it. */
export const ASK_MORPH_PANEL_NAME = "ask-panel";
/** The class on `<html>` that scopes the morph's CSS to this one transition. */
export const ASK_MORPH_CLASS = "ask-morph";
/** The attribute on `<html>` that says which way the box goes (`AskMorph`). */
export const ASK_MORPH_WAY_ATTR = "data-ask-morph";
/** The Ask bar's box (`ChatBar.tsx`). */
export const ASK_BOX_SELECTOR = "[data-ask-surface]";
/** The open assistant's composer (`AgentChat.tsx`). A phone's strip shows none. */
export const COMPOSER_SELECTOR =
  '.assistant-panel[data-assistant="dock"] [data-chat-composer], .assistant-panel[data-assistant="full"] [data-chat-composer]';

/** Which way the box goes: the bar into the chat's composer, or back. */
export type AskMorph = "into-chat" | "into-bar";

/** Tries for the arriving box, a frame apart. */
const MORPH_FIND_TRIES = 3;

let _morphSeq = 0;
/** The elements that carry a morph name after the change. */
let _morphNamed: HTMLElement[] = [];

function nameForMorph(el: HTMLElement, name: string): void {
  el.style.setProperty("view-transition-name", name);
  _morphNamed.push(el);
}

function clearMorphNames(): void {
  for (const el of _morphNamed) el.style.removeProperty("view-transition-name");
  _morphNamed = [];
}

/**
 * Open or close the assistant so the Ask bar and the chat's composer read as
 * ONE box (owner, 2026-11-01: "the chat box at the bottom morphs into the
 * chat box in the side bar, to make the user understand in fact it's the
 * same box"). The box that leaves carries the name before the change, and
 * the box that arrives carries it after, so the browser glides the one into
 * the other.
 *
 * ⚠️ Into the side panel, the panel takes a name too. The browser's glide
 * follows its target live, and a composer that rode the panel in from off
 * the edge carried the box past the window's edge first (measured
 * 2026-11-01). So the panel sits in its place from the change on, and its
 * picture slides in (`globals.css`). Out of the panel, the panel slides out
 * as it always does, and the bar holds still.
 *
 * The change is plain where no box can arrive (a page with no bar, a phone's
 * strip), where the browser has no view transitions, and with reduced
 * motion. Returns true when a transition started.
 */
export function morphAskBox(way: AskMorph, update: () => void): boolean {
  const doc = typeof document !== "undefined" ? document : null;
  const start = (doc as (Document & { startViewTransition?: StartViewTransition }) | null)?.startViewTransition;
  const [fromSel, toSel] = way === "into-chat" ? [ASK_BOX_SELECTOR, COMPOSER_SELECTOR] : [COMPOSER_SELECTOR, ASK_BOX_SELECTOR];
  const from = doc?.querySelector<HTMLElement>(fromSel) ?? null;
  if (!doc || !from || typeof start !== "function" || prefersReducedMotion()) {
    update();
    return false;
  }
  clearMorphNames();
  from.style.setProperty("view-transition-name", ASK_MORPH_NAME);
  doc.documentElement.classList.add(ASK_MORPH_CLASS);
  doc.documentElement.setAttribute(ASK_MORPH_WAY_ATTR, way);
  const mine = ++_morphSeq;
  const done = () => {
    // A newer morph owns the names and the class (`swapPanelForm`'s rule).
    if (mine !== _morphSeq) return;
    from.style.removeProperty("view-transition-name");
    clearMorphNames();
    doc.documentElement.classList.remove(ASK_MORPH_CLASS);
    doc.documentElement.removeAttribute(ASK_MORPH_WAY_ATTR);
  };
  let vt: ViewTransitionLike | null = null;
  const change = async () => {
    flushSync(update);
    // ⚠️ Two boxes with one name stop the transition. The composer stays
    // mounted when the panel closes, so the leaving box drops the name now.
    // The browser took its picture before the change.
    from.style.removeProperty("view-transition-name");
    // ⚠️ The panel mounts on its first open one render late, so its composer
    // can come a task after the change.
    let to = doc.querySelector<HTMLElement>(toSel);
    for (let i = 1; !to && i < MORPH_FIND_TRIES; i++) {
      await new Promise((r) => setTimeout(r, 16));
      to = doc.querySelector<HTMLElement>(toSel);
    }
    if (!to || mine !== _morphSeq) {
      vt?.skipTransition?.();
      return;
    }
    nameForMorph(to, ASK_MORPH_NAME);
    const panel = way === "into-chat" ? to.closest<HTMLElement>('.assistant-panel[data-panel-form="dock"]') : null;
    if (panel) nameForMorph(panel, ASK_MORPH_PANEL_NAME);
  };
  try {
    vt = start.call(doc, change);
    vt.finished.then(done, done);
    return true;
  } catch {
    done();
    update();
    return false;
  }
}

// ── A stack's card, to the next one or back ──────────────────────────────────

/** The `view-transition-name` of a stack's shown card while it changes. */
export const STACK_SWAP_NAME = "stack-card";
/** The class on `<html>` that scopes the card change's CSS. */
export const STACK_SWAP_CLASS = "card-swap";
/** The attribute on `<html>` that says which way the stack moves. */
export const STACK_SWAP_WAY_ATTR = "data-card-swap";

let _stackSeq = 0;

/**
 * Show another card of a stack (`components/ui/CardStack.tsx`, owner
 * 2026-11-01: "make the animation for the task cards better when I switch
 * between the cards"). Forward, the card the member leaves is thrown to the
 * side, as a swipe throws it, and the next one rises from the deck behind.
 * Back is the mirror: the card comes back from the side, and the one left
 * sinks into the deck. The CSS is `html.card-swap` in `globals.css`.
 *
 * The change is plain with reduced motion and where the browser has no view
 * transitions. Returns true when a transition started.
 */
export function swapStackCard(card: HTMLElement | null, way: "next" | "prev", update: () => void): boolean {
  const doc = typeof document !== "undefined" ? document : null;
  const start = (doc as (Document & { startViewTransition?: StartViewTransition }) | null)?.startViewTransition;
  if (!doc || !card || typeof start !== "function" || prefersReducedMotion()) {
    update();
    return false;
  }
  card.style.setProperty("view-transition-name", STACK_SWAP_NAME);
  doc.documentElement.classList.add(STACK_SWAP_CLASS);
  doc.documentElement.setAttribute(STACK_SWAP_WAY_ATTR, way);
  const mine = ++_stackSeq;
  const done = () => {
    if (mine !== _stackSeq) return;
    card.style.removeProperty("view-transition-name");
    doc.documentElement.classList.remove(STACK_SWAP_CLASS);
    doc.documentElement.removeAttribute(STACK_SWAP_WAY_ATTR);
  };
  try {
    // The shown card is one element that changes its content, so it keeps
    // its name across the change, as the panel does in `swapPanelForm`.
    const vt = start.call(doc, () => flushSync(update));
    vt.finished.then(done, done);
    return true;
  } catch {
    done();
    update();
    return false;
  }
}
