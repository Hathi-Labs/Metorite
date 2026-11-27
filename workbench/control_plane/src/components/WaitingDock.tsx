"use client";

/**
 * WaitingDock — the one row above the message box that names what waits on
 * the member (WD-1, `projects_ai_chat.md` §24.11, D10).
 *
 * With `WAITING_DOCK_ORGS` on, this row takes the place of `AskPin`. It reads
 * "Waits for you: <label> and N more" and holds one button, Review. Every
 * card stays in the thread, exactly as with the flag off. The row holds no
 * card and no commit control.
 *
 * - It never takes the focus and never blurs the message box. A press on
 *   Review keeps the focus where it was (`preventDefault` on `mousedown`).
 * - Review scrolls the thread, so the card shows whole above the row, or its
 *   top when it is taller than the room. It moves the focus to the card's
 *   heading only when the message box does not have the focus.
 * - Its height is fixed. An arrival takes the height only when no pointer is
 *   down. When the last element leaves, an inert "Nothing waits" holds the
 *   place, and the row collapses only after 800 ms with no pointer down
 *   (`lib/waitingDock.ts` `nextPhase`).
 *
 * Fence: `src/lib/waitingDock.test.ts` and `e2e/chat-waiting-dock.spec.ts`.
 */

import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { boxHadFocus } from "@/lib/composerFocus";
import {
  HOLD_MS,
  NOTHING_WAITS,
  ROW_LEAD,
  STUCK_PRESS_MS,
  nextPhase,
  reviewScrollTop,
  rowMore,
  type RowPhase,
  type WaitingRow,
} from "@/lib/waitingDock";

/** The element of the row's card in the thread, or null when it is not mounted. */
function cardIn(thread: HTMLElement, row: WaitingRow): HTMLElement | null {
  const ask = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(row.ask) : row.ask;
  const all = thread.querySelectorAll<HTMLElement>(`[data-chat-ask="${ask}"]`);
  const el = all.length > 0 ? all[all.length - 1] : null;
  return el && row.inner ? el.querySelector<HTMLElement>(row.inner) : el;
}

/** The row's frame, the same in each state, so its height never changes. A
 *  touch screen in the glass look gives Review 40 px, so the row grows to fit. */
const FRAME =
  "max-w-3xl mx-auto mb-2 flex h-8 min-w-0 items-center gap-2 rounded-lg border px-3 text-[11px] glass:rounded-[16px] glass:border-transparent glass:px-4 glass:pointer-coarse:h-11";

export default function WaitingDock({
  row,
  threadRef,
  boxRef,
}: {
  /** What waits, or null when nothing does (`waitingRow`). */
  row: WaitingRow | null;
  threadRef: RefObject<HTMLElement | null>;
  /** The message box. Review never moves the focus away from it. */
  boxRef: RefObject<HTMLElement | null>;
}) {
  const [st, setSt] = useState<{ phase: RowPhase; leftAt: number }>({ phase: "hidden", leftAt: 0 });
  // A pointer release, or a timer, draws the row again so it can move on.
  const [tick, setTick] = useState(0);
  const downRef = useRef(new Map<number, number>());
  const upAtRef = useRef(Number.NEGATIVE_INFINITY);
  const waiting = !!row;

  useEffect(() => {
    const down = (e: PointerEvent) => {
      downRef.current.set(e.pointerId, performance.now());
    };
    const up = (e: PointerEvent) => {
      downRef.current.delete(e.pointerId);
      upAtRef.current = performance.now();
      setTick((t) => t + 1);
    };
    // A lost focus loses its releases too.
    const clear = () => {
      downRef.current.clear();
      setTick((t) => t + 1);
    };
    window.addEventListener("pointerdown", down, true);
    window.addEventListener("pointerup", up, true);
    window.addEventListener("pointercancel", up, true);
    window.addEventListener("blur", clear);
    return () => {
      window.removeEventListener("pointerdown", down, true);
      window.removeEventListener("pointerup", up, true);
      window.removeEventListener("pointercancel", up, true);
      window.removeEventListener("blur", clear);
    };
  }, []);

  const pointerDown = (now: number) => {
    for (const at of downRef.current.values()) if (now - at < STUCK_PRESS_MS) return true;
    return false;
  };

  // Before the paint, so no frame draws a height that the rule refuses.
  useLayoutEffect(() => {
    const now = performance.now();
    const next = nextPhase({
      phase: st.phase,
      waiting,
      pointerDown: pointerDown(now),
      quietMs: now - Math.max(st.leftAt, upAtRef.current),
    });
    if (next !== st.phase) setSt({ phase: next, leftAt: next === "empty" && st.phase === "row" ? now : st.leftAt });
  });

  // The waits that only time ends: the collapse after `HOLD_MS`, and an
  // arrival that waits on a pointer that never came up (`STUCK_PRESS_MS`).
  useEffect(() => {
    const now = performance.now();
    let wait: number | null = null;
    if (st.phase === "empty") wait = Math.max(0, HOLD_MS - (now - Math.max(st.leftAt, upAtRef.current)));
    else if (st.phase === "hidden" && waiting) wait = 250;
    if (wait === null) return;
    const t = setTimeout(() => setTick((x) => x + 1), wait + 10);
    return () => clearTimeout(t);
  }, [st, waiting, tick]);

  if (st.phase === "hidden") return null;

  if (st.phase === "empty" || !row) {
    return (
      <div data-dock-row="empty" role="status" aria-live="polite" className={`${FRAME} border-border bg-card text-muted-foreground`}>
        <Icon name="Check" size={13} className="shrink-0" />
        <span className="truncate">{NOTHING_WAITS}</span>
      </div>
    );
  }

  const review = () => {
    const thread = threadRef.current;
    const card = thread ? cardIn(thread, row) : null;
    if (!thread || !card) return;
    const top = thread.getBoundingClientRect().top + thread.clientTop;
    const to = reviewScrollTop(thread.scrollTop, { top, bottom: top + thread.clientHeight }, card.getBoundingClientRect());
    if (to !== thread.scrollTop) thread.scrollTo({ top: to });
    // With the message box in focus (a phone keyboard up), only the scroll.
    if (boxHadFocus(boxRef.current)) return;
    const head = card.querySelector<HTMLElement>("[data-rollup-title]") ?? card;
    if (!head.hasAttribute("tabindex")) head.setAttribute("tabindex", "-1");
    head.focus({ preventScroll: true });
  };

  return (
    <div
      data-dock-row="row"
      role="status"
      aria-live="polite"
      className={`${FRAME} border-primary/30 bg-primary/5 glass:bg-primary/12 glass:text-footnote`}
    >
      <Icon name="BellRing" size={13} className="shrink-0 text-primary" />
      <span className="shrink-0 font-medium text-foreground">{ROW_LEAD}</span>
      <span className="min-w-0 flex-1 truncate text-muted-foreground" title={row.label} data-dock-label="">
        {row.label}
        {row.more > 0 && <span className="tabular-nums"> {rowMore(row.more)}</span>}
      </span>
      <Button
        type="button"
        size="sm"
        variant="secondary"
        // A press keeps the focus where it is: the box keeps it, and a phone
        // keeps its keyboard.
        onMouseDown={(e) => e.preventDefault()}
        onClick={review}
        data-dock-review=""
        className="shrink-0"
      >
        Review
      </Button>
    </div>
  );
}
