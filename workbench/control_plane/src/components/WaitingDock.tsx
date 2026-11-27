"use client";

/**
 * WaitingDock — the dock above the message box for each card that waits on
 * the member (WD-1, `projects_ai_chat.md` §24.11, D234).
 *
 * With `WAITING_DOCK_ORGS` on, the approvals and the question cards draw here,
 * as the tabs of ONE card (`ConfirmationQueue` with `questions`), and not in
 * the thread. The dock sits in the form, outside the scrolling thread, so it
 * never scrolls away. It holds:
 *
 * - the Fold control and, when the card is over the cap, the Expand control.
 *   They sit in the tab row, or on the title row of the one card, so they
 *   take no row of their own. No control drops an item unanswered (WD-D2);
 * - the ONE scroll box of the dock: the body of the open item, under the cap
 *   (`lib/waitingDock.ts` `dockCap`). That is the one amendment to C3;
 * - beside it, `WaitingDockLive`: one polite live region, "N items wait for
 *   you" when the count goes up. The chat mounts it while the flag is on, so
 *   the region exists before the first item arrives. The arrival of an item
 *   never takes the focus (`AgentChat` holds K1).
 *
 * The fold is the dock's own, beside `RollupCard`, which never folds a card
 * that waits (audit rule 8). With the phone's keyboard up and no field of the
 * card in focus, the dock folds (K7). A field of the card in focus keeps it
 * open (K3). The cards stay mounted while folded, so no edit is lost.
 *
 * Fences: `src/lib/waitingDock.test.ts` (the rules) and
 * `src/components/waitingDock.test.ts` (the markup).
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { useVisualViewport } from "@/app/tasks/lib/useVisualViewport";
import Button from "@/components/ui/Button";
import { revealWithin } from "@/lib/scrollWithin";
import { PINNED_ACTIONS, type DockMode, arrivalUnfolds, dockCap, dockFolded, isTextField, keyboardUp, liveAfter } from "@/lib/waitingDock";

export interface WaitingDockProps {
  /** How many items wait on the member (`dockCount`). */
  count: number;
  /** The phone: the phone chrome, or a device with a soft keyboard. */
  phone: boolean;
  /** The cards. `folded` draws them as one row, and `unfold` opens them. */
  children: (dock: DockRender) => ReactNode;
  /** Tests and the visual rig: start folded. */
  initialFolded?: boolean;
}

/**
 * The three boxes around the dock. The dock sits in its host, OUTSIDE the
 * form: the thread is the host's previous sibling and the form its next one.
 */
function around(root: HTMLElement | null): { host: HTMLElement; thread: HTMLElement; form: HTMLElement } | null {
  const host = root?.closest<HTMLElement>("[data-dock-host]");
  const thread = host?.previousElementSibling as HTMLElement | null | undefined;
  const form = host?.nextElementSibling as HTMLElement | null | undefined;
  return host && thread && form ? { host, thread, form } : null;
}

/**
 * The heights the cap reads. `chat` is the room that the thread, the dock and
 * the form share, so a header row above the thread is not counted.
 * `composer` is all of that room but the thread and the dock's body: the
 * form, and the dock's own padding.
 */
function measure(root: HTMLElement | null, body: HTMLElement | null): { chat: number; composer: number } | null {
  const a = around(root);
  if (!a || !body) return null;
  return {
    chat: a.thread.clientHeight + a.host.offsetHeight + a.form.offsetHeight,
    composer: Math.max(0, a.form.offsetHeight + a.host.offsetHeight - body.offsetHeight),
  };
}

/** How long after a press ends the dock waits before it may fold (K3). */
const PRESS_GRACE_MS = 500;

/** What the dock hands the queue on each render. */
export interface DockRender {
  /** Folded to one row (WD-D2). */
  folded: boolean;
  /** Open the folded dock. */
  unfold: () => void;
  /** The header's Expand and Fold controls (D1). */
  controls: ReactNode;
  /** The view of the open card (D2). */
  mode: DockMode;
  /** Open the Expanded view (D3: a card's Review). */
  expand: () => void;
}

export default function WaitingDock({ count, phone, children, initialFolded = false }: WaitingDockProps) {
  const rootRef = useRef<HTMLElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [manual, setManual] = useState(initialFolded);
  const [expanded, setExpanded] = useState(false);
  const [fieldFocus, setFieldFocus] = useState(false);
  const [size, setSize] = useState<{ chat: number; composer: number } | null>(null);
  const [layout, setLayout] = useState(0);
  const [over, setOver] = useState(false);
  // Rule 9: the fade at the foot of the body, above the pinned action row.
  // It shows while more of the body is below, and goes at the end.
  const [atEnd, setAtEnd] = useState(true);
  const [pinH, setPinH] = useState(0);
  const vp = useVisualViewport();
  // K2: a new item opens the dock to its cap, also after a fold by hand.
  const [seenCount, setSeenCount] = useState(count);
  if (seenCount !== count) {
    setSeenCount(count);
    if (arrivalUnfolds(seenCount, count)) setManual(false);
    // An item left (answered or gone): the next one opens Compact again.
    else if (count < seenCount) setExpanded(false);
  }
  // K3: a press inside the dock. iOS gives a tapped button no focus, so the
  // field's blur has no related target. While a press runs, and a moment
  // after, the dock keeps the item open, so Submit is under the finger.
  const pressingRef = useRef(false);
  const graceRef = useRef<ReturnType<typeof setTimeout> | null>(null);


  const kbUp = keyboardUp({ phone, viewport: vp?.height ?? null, layout });
  const folded = dockFolded({ manual, keyboardUp: kbUp, cardFieldFocus: fieldFocus });
  // Round 7: every fold leaves Expanded, by any cause (Fold, K7, the
  // keyboard). An unfold then shows Compact, which holds no commit.
  if (folded && expanded) setExpanded(false);
  const cap = size
    ? dockCap({
        phone,
        chat: size.chat,
        viewport: vp?.height ?? null,
        composer: size.composer,
        keyboardUp: kbUp,
        cardFieldFocus: fieldFocus,
        expanded,
      })
    : null;

  const read = useCallback(() => {
    const m = measure(rootRef.current, bodyRef.current);
    if (m) setSize((s) => (s && s.chat === m.chat && s.composer === m.composer ? s : m));
    if (typeof window !== "undefined") setLayout((l) => (l === window.innerHeight ? l : window.innerHeight));
    const body = bodyRef.current;
    if (body) {
      const o = body.scrollHeight > body.clientHeight + 1;
      setOver((x) => (x === o ? x : o));
      const end = body.scrollTop + body.clientHeight >= body.scrollHeight - 2;
      setAtEnd((x) => (x === end ? x : end));
      const pin = Array.from(body.querySelectorAll<HTMLElement>(PINNED_ACTIONS)).find((el) => el.offsetParent !== null);
      const h = pin ? pin.offsetHeight : 0;
      setPinH((x) => (x === h ? x : h));
    }
  }, []);
  // Each render: the form, the column and the body may have changed. Each
  // `set` runs only when a value changed, so it settles.
  useLayoutEffect(() => {
    read();
  });
  useEffect(() => {
    const root = rootRef.current;
    const a = around(root);
    if (!root || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => read());
    ro.observe(root);
    if (a) {
      ro.observe(a.form);
      ro.observe(a.thread);
      if (a.host.parentElement) ro.observe(a.host.parentElement);
    }
    if (bodyRef.current) ro.observe(bodyRef.current);
    window.addEventListener("resize", read);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", read);
    };
  }, [read]);

  // K3: a tap on a field of the card keeps the item open, and the field
  // stays in view inside the dock's body. Only the body scrolls.
  const onFocusIn = (e: React.FocusEvent<HTMLElement>) => {
    if (!isTextField(e.target)) return;
    setFieldFocus(true);
    const target = e.target;
    requestAnimationFrame(() => revealWithin(bodyRef.current, target));
  };
  const onFocusOut = (e: React.FocusEvent<HTMLElement>) => {
    // A press inside the dock runs: iOS gives a tapped button no focus, so
    // the field's blur names no target. Stay open until the press settles.
    if (pressingRef.current) return;
    // Else the focus decides: a text field of the dock keeps it open, any
    // other place (a button, the box, nowhere) lets the K7 fold act.
    const next = e.relatedTarget as HTMLElement | null;
    setFieldFocus(!!next && !!rootRef.current?.contains(next) && isTextField(next));
  };
  // After a press, fold back only when no field of the dock has the focus.
  // The wait lets the click of the press land first.
  const settleAfterPress = () => {
    if (graceRef.current) clearTimeout(graceRef.current);
    graceRef.current = setTimeout(() => {
      pressingRef.current = false;
      const at = typeof document === "undefined" ? null : (document.activeElement as HTMLElement | null);
      if (!(at && rootRef.current?.contains(at) && isTextField(at))) setFieldFocus(false);
    }, PRESS_GRACE_MS);
  };
  const onPointerDown = () => {
    if (graceRef.current) clearTimeout(graceRef.current);
    pressingRef.current = true;
    const end = () => {
      window.removeEventListener("pointerup", end, true);
      window.removeEventListener("pointercancel", end, true);
      settleAfterPress();
    };
    window.addEventListener("pointerup", end, true);
    window.addEventListener("pointercancel", end, true);
  };
  useEffect(() => () => {
    if (graceRef.current) clearTimeout(graceRef.current);
  }, []);
  // D2: Review opens the Expanded view, and the focus moves into the dock's
  // content, never onto a button (24.11, the fresh press).
  const expand = () => {
    setExpanded(true);
    requestAnimationFrame(() => bodyRef.current?.focus({ preventScroll: true }));
  };
  // Esc closes the Expanded view, however it was opened: the first Esc
  // anywhere in this chat, the message box too. It does nothing else, so it
  // never also stops the run. A second Esc keeps its old meaning. A listener
  // on the window in the capture phase runs before any handler of the page.
  // Round 7: after a press on blank thread the focus is on the page body, and
  // that Esc is this chat's too. An Esc of a modal, or of another surface,
  // stays theirs. Esc in the earlier-draft question is that question's Back.
  useEffect(() => {
    if (!expanded) return;
    const chatOf = () => rootRef.current?.closest("[data-dock-host]")?.parentElement ?? null;
    let pressedHere = true;
    const onPress = (e: PointerEvent) => {
      const chat = chatOf();
      pressedHere = !!chat && e.target instanceof Node && chat.contains(e.target);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const chat = chatOf();
      const target = e.target as Node | null;
      if (!chat || !target) return;
      if (target instanceof Element && target.closest("[data-earlier-question]")) return;
      const fromPage = target === document.body || target === document.documentElement;
      const modal = !!document.querySelector('[aria-modal="true"], dialog[open]');
      if (!chat.contains(target) && !(fromPage && pressedHere && !modal)) return;
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      setExpanded(false);
    };
    window.addEventListener("pointerdown", onPress, true);
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("pointerdown", onPress, true);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [expanded]);
  // K7: the fold row of the keyboard opens the card only when the member
  // asks. A tap on its button opens the card and drops the keyboard.
  const unfold = () => {
    setManual(false);
    if (kbUp && typeof document !== "undefined") (document.activeElement as HTMLElement | null)?.blur?.();
  };

  // The Fold and Expand controls. They take no row of their own: the queue
  // puts them in the tab row, or on the title row of the one card.
  const controls = folded ? null : (
    <span className="inline-flex shrink-0 items-center gap-0.5" data-dock-controls="">
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        icon={expanded ? "Minimize2" : "Maximize2"}
        onClick={() => (expanded ? setExpanded(false) : expand())}
        aria-label={expanded ? "Collapse the card" : "Expand the card"}
        title={expanded ? "Collapse the card (Esc)" : "Expand the card"}
        aria-expanded={expanded}
        data-dock-expand={expanded ? "on" : "off"}
      />
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        icon="ChevronDown"
        onClick={() => setManual(true)}
        aria-label="Fold the card to one row"
        title="Fold the card to one row"
        aria-expanded
        data-dock-fold=""
      />
    </span>
  );

  return (
    <section
      ref={rootRef}
      data-waiting-dock={folded ? "folded" : "open"}
      aria-label="Waiting for you"
      onFocus={onFocusIn}
      onBlur={onFocusOut}
      onPointerDown={onPointerDown}

      className="relative max-w-3xl mx-auto mb-2"
    >
      {/* The one scroll box of the dock: the open item over the cap (C3). */}
      <div
        ref={bodyRef}
        data-dock-body=""
        // The focus lands here after Review (not on a button), so a held key
        // or a second press on Review reaches no commit. The card's radius
        // clips what scrolls under the pinned header's corners.
        tabIndex={-1}
        className="overflow-y-auto overscroll-contain scrollbar-thin rounded-lg glass:rounded-[16px]"
        style={folded ? undefined : { maxHeight: cap === null ? "50vh" : `${cap}px` }}
        onScroll={read}
      >
        {children({ folded, unfold, controls, mode: expanded ? "expanded" : "compact", expand })}
      </div>
      {/* The fade: theme tokens only, and it takes no click. */}
      {!folded && over && !atEnd && (
        <div
          aria-hidden
          data-dock-fade=""
          className="pointer-events-none absolute inset-x-px z-[2] h-8 bg-gradient-to-t from-card to-transparent"
          style={{ bottom: pinH }}
        />
      )}
    </section>
  );
}

/**
 * The one polite live region of the dock (the a11y line of §24.11). It says
 * "N items wait for you" when the count goes up, and nothing when it goes
 * down. The chat mounts it while the flag is on, before any item waits, so a
 * screen reader hears the first arrival too. It starts from 0.
 */
export function WaitingDockLive({ count }: { count: number }) {
  const [prev, setPrev] = useState(0);
  const [live, setLive] = useState("");
  // React's adjust-state-during-render pattern, so a render that runs twice
  // reaches the same words.
  if (prev !== count) {
    setPrev(count);
    // Empty again clears the words, so the next arrival is read (`liveAfter`).
    const next = liveAfter(prev, count, live);
    if (next !== live) setLive(next);
  }
  return (
    <p role="status" aria-live="polite" className="sr-only" data-dock-live="">
      {live}
    </p>
  );
}
