"use client";

/**
 * The whole text of a truncated label, in a small dark tip on hover.
 *
 * Owner, 2026-10-10: *"when I hover over the text, even if it's truncated, I
 * can read the entire line as a pop-up."* A rail cuts a long name with an
 * ellipsis, and before this nothing in the app showed the rest of it.
 *
 * The rules, each one a choice:
 *
 * - **Only when the label IS truncated.** The tip measures the label when the
 *   delay ends (`scrollWidth > clientWidth`). A tip that repeats a name the
 *   member can already read is noise on every row.
 * - **After a delay** (`OVERFLOW_TIP_DELAY_MS`), so a pointer that crosses
 *   the rail does not light a trail of tips.
 * - **It hides** on leave, on a press, on a scroll and on Escape.
 * - **Keyboard focus shows it too**, but only `:focus-visible`. A mouse click
 *   that lands focus on a row must not leave a tip behind.
 * - **No native `title`.** The browser's own tip would show the same text a
 *   second time, in the OS's style, after its own delay.
 * - **The label stays the accessible name.** The tip is `role="tooltip"` and
 *   nothing points at it with `aria-describedby`, so a screen reader does not
 *   read the name twice.
 * - **It hangs from `AnchoredPanel`** (`variant="tip"`), which portals it to
 *   `document.body`. A rail is `overflow: hidden` and would clip it.
 * - **No motion.** The tip appears as a state change, so there is nothing for
 *   `prefers-reduced-motion` to turn off.
 *
 * ⚠️ The vitest setup has no DOM, so the behaviour is a pure reducer
 * (`overflowTipReducer`), as in `InfoTip.tsx`. `e2e/rail-rows.spec.ts`
 * measures the real tip in a browser.
 */
import { useCallback, useEffect, useId, useReducer, useRef, useState } from "react";

import AnchoredPanel from "@/components/ui/AnchoredPanel";

/** How long the pointer rests on a label before its tip shows, in ms. */
export const OVERFLOW_TIP_DELAY_MS = 400;

/** The widest the tip draws, in px. A longer name wraps inside it. */
export const OVERFLOW_TIP_MAX_WIDTH = 320;

export type OverflowTipPhase = "idle" | "waiting" | "open";

export interface OverflowTipState {
  phase: OverflowTipPhase;
}

export type OverflowTipAction =
  /** The pointer came onto the label, or keyboard focus reached its row. */
  | { type: "enter" }
  /** The delay ended. `truncated` is the measurement taken at that moment. */
  | { type: "elapsed"; truncated: boolean }
  /** The pointer or the focus went away. */
  | { type: "leave" }
  /** Escape, a press or a scroll. */
  | { type: "dismiss" };

export const TIP_IDLE: OverflowTipState = { phase: "idle" };

export function overflowTipReducer(
  state: OverflowTipState,
  action: OverflowTipAction,
): OverflowTipState {
  switch (action.type) {
    case "enter":
      return state.phase === "idle" ? { phase: "waiting" } : state;
    case "elapsed":
      if (state.phase !== "waiting") return state;
      return action.truncated ? { phase: "open" } : TIP_IDLE;
    case "leave":
    case "dismiss":
      return TIP_IDLE;
  }
}

/** Is the text cut? True only when it is wider than the box that holds it. */
export function isTruncated(
  box: { scrollWidth: number; clientWidth: number } | null | undefined,
): boolean {
  if (!box) return false;
  return box.scrollWidth > box.clientWidth;
}

/**
 * How wide the tip may draw, so it stays inside the window at 390 px. It
 * hangs from the label's left edge, 8 px clear of the right edge of the
 * window, and never narrower than 120 px.
 */
export function overflowTipWidth(left: number, viewportWidth: number): number {
  return Math.max(120, Math.min(OVERFLOW_TIP_MAX_WIDTH, viewportWidth - left - 8));
}

/** Does this element hold keyboard focus, as the browser draws it? */
function hasVisibleFocus(element: Element): boolean {
  try {
    return element.matches(":focus-visible");
  } catch {
    return false;
  }
}

/**
 * The tip's state and wiring, for a caller that owns the label's markup
 * (`RailRow`). Put `labelRef`, `onPointerEnter` and `onPointerLeave` on the
 * truncated element, and `onFocus` and `onBlur` on the control that takes
 * focus. Render `tip` anywhere.
 */
export function useOverflowTip(text: string) {
  const [state, dispatch] = useReducer(overflowTipReducer, TIP_IDLE);
  const label = useRef<HTMLElement | null>(null);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [width, setWidth] = useState(OVERFLOW_TIP_MAX_WIDTH);
  const tipId = useId();

  // Stable, so React does not detach and attach it on each render.
  const labelRef = useCallback((node: HTMLElement | null) => {
    label.current = node;
    setAnchor(node);
  }, []);

  useEffect(() => {
    if (state.phase !== "waiting") return;
    const timer = window.setTimeout(() => {
      const node = label.current;
      // Measured when the delay ends, not when the pointer arrived: the row's
      // actions take their room on hover, and that can cut a name that fit.
      if (node) setWidth(overflowTipWidth(node.getBoundingClientRect().left, window.innerWidth));
      dispatch({ type: "elapsed", truncated: isTruncated(node) });
    }, OVERFLOW_TIP_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [state.phase]);

  useEffect(() => {
    if (state.phase !== "open") return;
    const dismiss = () => dispatch({ type: "dismiss" });
    // In the CAPTURE phase and stopped, so the first Escape closes the tip
    // and not the phone drawer the rail sits in. The second one closes that.
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      dismiss();
    };
    window.addEventListener("keydown", onKey, true);
    // Capture, because the box that scrolls is not an ancestor of the tip.
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("pointerdown", dismiss, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("pointerdown", dismiss, true);
    };
  }, [state.phase]);

  const onPointerEnter = useCallback(() => dispatch({ type: "enter" }), []);
  const onPointerLeave = useCallback(() => dispatch({ type: "leave" }), []);
  const onFocus = useCallback((event: React.FocusEvent<HTMLElement>) => {
    if (hasVisibleFocus(event.currentTarget)) dispatch({ type: "enter" });
  }, []);
  const onBlur = useCallback(() => dispatch({ type: "leave" }), []);

  const tip = (
    <AnchoredPanel
      anchor={anchor}
      open={state.phase === "open"}
      variant="tip"
      maxHeight={160}
      panelProps={{ id: tipId, role: "tooltip", "data-overflow-tip": "" }}
      className="text-xs"
    >
      <div style={{ maxWidth: width }} className="whitespace-normal break-words px-2 py-1">
        {text}
      </div>
    </AnchoredPanel>
  );

  return { phase: state.phase, labelRef, onPointerEnter, onPointerLeave, onFocus, onBlur, tip };
}

export interface OverflowTipProps {
  /** The whole text. The label draws it cut, and the tip draws it whole. */
  text: string;
  /** Layout and type for the label. `truncate` is already on it. */
  className?: string;
}

/** A one-line label that cuts with an ellipsis and shows its whole text on hover. */
export function OverflowTip({ text, className = "" }: OverflowTipProps) {
  const tip = useOverflowTip(text);
  return (
    <>
      <span
        ref={tip.labelRef}
        onPointerEnter={tip.onPointerEnter}
        onPointerLeave={tip.onPointerLeave}
        className={`truncate ${className}`}
      >
        {text}
      </span>
      {tip.tip}
    </>
  );
}

export default OverflowTip;
