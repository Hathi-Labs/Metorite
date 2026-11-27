"use client";

/**
 * WaitingDock — the dock above the message box for each QUESTION card that
 * waits on the member (WD-1, `projects_ai_chat.md` §24.11, D234 and D9).
 *
 * With `WAITING_DOCK_ORGS` on, the `ask_questions` and the `ask_user` cards
 * draw here, and not in the thread. Two cards that wait are two tabs. The
 * dock sits OUTSIDE the form and outside the scrolling thread, so it never
 * scrolls away. It holds:
 *
 * - ONE pointer row while an approval waits (D9). An approval card always
 *   draws in the thread, as with the flag off. The row names the newest one,
 *   and its Review only scrolls the thread to that card. It commits nothing;
 * - the Fold control, on the title row of the open question. No control
 *   drops an item unanswered (WD-D2);
 * - the ONE scroll box of the dock: the body of the open question, under the
 *   cap (`lib/waitingDock.ts` `dockCap`). That is the one amendment to C3. A
 *   change of tab sets its scroll back to the top;
 * - beside it, `WaitingDockLive`: one polite live region, "N items wait for
 *   you" when the count goes up. The chat mounts it while the flag is on, so
 *   the region exists before the first item arrives. The arrival of an item
 *   never takes the focus (`AgentChat` holds K1).
 *
 * The fold is the dock's own, beside `RollupCard`, which never folds a card
 * that waits (audit rule 8). With the phone's keyboard up and no field of the
 * card in focus, the dock folds (K7). A field of the card in focus keeps it
 * open (K3). The cards stay mounted while folded, so no pick is lost.
 *
 * Fences: `src/lib/waitingDock.test.ts` (the rules) and
 * `src/components/waitingDock.test.ts` (the markup).
 */

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { useVisualViewport } from "@/app/tasks/lib/useVisualViewport";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import CardTabs, { cardPanelId, cardTabId } from "@/components/ui/CardTabs";
import { KIND_ICON } from "@/lib/cardTabs";
import { revealWithin } from "@/lib/scrollWithin";
import {
  PINNED_ACTIONS,
  arrivalUnfolds,
  dockCap,
  dockFolded,
  isTextField,
  keyboardUp,
  liveAfter,
  POINTER_LEAD,
  pointerMore,
  UNFOLD_LABEL,
} from "@/lib/waitingDock";

/** What the dock hands a question card when it draws it. */
export interface DockSlot {
  /** The tab row, inside the card's frame, when two cards wait. */
  tabs?: ReactNode;
  /** The tab panel: the card is the panel the tabs control. */
  tabPanel?: { id: string; labelledBy: string };
  /** The dock's Fold control, on the card's title row. */
  headerAside?: ReactNode;
  /** A question card in the dock draws no X (WD-D2). Its Not now stays. */
  inDock?: boolean;
  /** The card is on the screen: its tab open, the dock not folded (round 7). */
  dockShown?: boolean;
}

/** A question card as a tab of the dock (§24.11). */
export interface DockQuestion {
  /** Stable for the card's life: its request id. */
  key: string;
  /** The tab's words (`questionTabLabel`). */
  label: string;
  /** The whole question, for the tooltip. */
  title: string;
  /** Draw the card, with the tab row and the panel when it is the open tab. */
  render: (slot: DockSlot) => ReactNode;
}

/** The pointer row: the newest approval that waits in the thread (D9). */
export interface DockPointer {
  /** The tab label of the newest approval that waits (`tabLabel`). */
  label: string;
  /** How many other approvals wait. */
  more: number;
  /** Scroll the thread to the card, and move the focus to its heading. */
  onReview: () => void;
}

export interface WaitingDockProps {
  /** The question cards that wait, in tab order. */
  questions: readonly DockQuestion[];
  /** The pointer to the approvals in the thread, or null when none waits. */
  pointer: DockPointer | null;
  /** The phone: the phone chrome, or a device with a soft keyboard. */
  phone: boolean;
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
 * form, the pointer row and the dock's own padding.
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

export default function WaitingDock({ questions, pointer, phone, initialFolded = false }: WaitingDockProps) {
  const rootRef = useRef<HTMLElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const uid = useId();
  const [manual, setManual] = useState(initialFolded);
  const [fieldFocus, setFieldFocus] = useState(false);
  const [size, setSize] = useState<{ chat: number; composer: number } | null>(null);
  const [layout, setLayout] = useState(0);
  const [over, setOver] = useState(false);
  // Rule 10: the fade at the foot of the body, above the pinned Submit row.
  // It shows while more of the body is below, and goes at the end.
  const [atEnd, setAtEnd] = useState(true);
  const [pinH, setPinH] = useState(0);
  const vp = useVisualViewport();
  const count = questions.length;

  // The open tab, by key. Only the first paint picks it (the first tab).
  // After it, only the member or the answer of the open card moves it, so a
  // new question never takes the open tab (rule 8). When the open card
  // leaves, the card now at its place opens.
  const [shown, setShown] = useState<{ key: string | null; index: number }>({ key: null, index: 0 });
  const found = shown.key === null ? -1 : questions.findIndex((q) => q.key === shown.key);
  const at = found >= 0 ? found : Math.min(shown.index, Math.max(0, count - 1));
  const open = questions[at] ?? null;
  if (open && (shown.key !== open.key || shown.index !== at)) setShown({ key: open.key, index: at });
  // The tabs the member has opened: a tab not in it wears a dot.
  const [seen, setSeen] = useState<ReadonlySet<string>>(() => new Set());
  if (open && !seen.has(open.key)) setSeen(new Set([...seen, open.key]));

  // K2: a new question opens the dock to its cap, also after a fold by hand.
  const [seenCount, setSeenCount] = useState(count);
  if (seenCount !== count) {
    setSeenCount(count);
    if (arrivalUnfolds(seenCount, count)) setManual(false);
  }
  // K3: a press inside the dock. iOS gives a tapped button no focus, so the
  // field's blur has no related target. While a press runs, and a moment
  // after, the dock keeps the item open, so Submit is under the finger.
  const pressingRef = useRef(false);
  const graceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const kbUp = keyboardUp({ phone, viewport: vp?.height ?? null, layout });
  const folded = dockFolded({ manual, keyboardUp: kbUp, cardFieldFocus: fieldFocus });
  const cap = size
    ? dockCap({
        phone,
        chat: size.chat,
        viewport: vp?.height ?? null,
        composer: size.composer,
        keyboardUp: kbUp,
        cardFieldFocus: fieldFocus,
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
  // Requirement 4 of D9: a change of tab starts the new card at its top, so a
  // long question never opens in the middle.
  const openKey = open?.key ?? null;
  useLayoutEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = 0;
  }, [openKey]);
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
  // K7: the fold row of the keyboard opens the card only when the member
  // asks. A tap on its button opens the card and drops the keyboard.
  const unfold = () => {
    setManual(false);
    if (kbUp && typeof document !== "undefined") (document.activeElement as HTMLElement | null)?.blur?.();
  };
  const go = (to: number) => {
    const q = questions[to];
    if (q) setShown({ key: q.key, index: to });
  };

  // The Fold control. It takes no row of its own: it sits on the title row
  // of the open question.
  const fold = (
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
  );
  // Two questions are two tabs of one card (WD-D3). The count reads
  // "N waiting", as a question is not approved.
  const tabs =
    count > 1 ? (
      <CardTabs
        uid={uid}
        index={at}
        onGo={go}
        words="waiting"
        tabs={questions.map((q) => ({
          key: q.key,
          label: q.label,
          title: q.title,
          icon: KIND_ICON.question,
          unseen: !seen.has(q.key),
        }))}
      />
    ) : null;

  return (
    <section
      ref={rootRef}
      data-waiting-dock={count === 0 ? "pointer" : folded ? "folded" : "open"}
      aria-label="Waiting for you"
      onFocus={onFocusIn}
      onBlur={onFocusOut}
      onPointerDown={onPointerDown}
      className="relative max-w-3xl mx-auto mb-2 space-y-2"
    >
      {pointer && (
        // D9: one row for the approvals, which wait in the thread. It names
        // the newest, and Review only scrolls to it. It commits nothing.
        <div
          data-dock-pointer=""
          className="flex min-w-0 items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-1 text-[11px] glass:rounded-[16px] glass:border-transparent glass:bg-primary/12 glass:px-4 glass:text-footnote"
        >
          <Icon name="BellRing" size={13} className="shrink-0 text-primary" />
          <span className="shrink-0 font-medium text-foreground">{POINTER_LEAD}</span>
          <span className="min-w-0 flex-1 truncate text-muted-foreground" title={pointer.label} data-dock-pointer-label="">
            {pointer.label}
            {pointer.more > 0 && <span className="tabular-nums"> {pointerMore(pointer.more)}</span>}
          </span>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={pointer.onReview}
            data-dock-pointer-review=""
            className="shrink-0"
          >
            Review
          </Button>
        </div>
      )}
      {count > 0 && (
        <>
          {/* The one scroll box of the dock: the open question over the cap (C3). */}
          <div
            ref={bodyRef}
            data-dock-body=""
            className="overflow-y-auto overscroll-contain scrollbar-thin rounded-lg glass:rounded-[16px]"
            style={folded ? undefined : { maxHeight: cap === null ? "50vh" : `${cap}px` }}
            onScroll={read}
          >
            {folded && open && (
              // The fold row: the open question and one button that ONLY
              // opens the card (audit rule 2).
              <div
                data-dock-fold-row=""
                className="flex min-w-0 items-center gap-2 rounded-lg border border-border bg-card px-3 py-1.5 text-xs glass:rounded-[16px] glass:border-primary/35"
              >
                <Icon name={KIND_ICON.question} size={13} className="shrink-0 text-primary" />
                <span className="min-w-0 flex-1 truncate text-foreground" title={open.title}>
                  {open.label}
                </span>
                {count > 1 && <span className="shrink-0 tabular-nums text-muted-foreground">{count} waiting</span>}
                <Button type="button" size="sm" variant="secondary" onClick={unfold} data-dock-unfold="question" className="shrink-0">
                  {UNFOLD_LABEL}
                </Button>
              </div>
            )}
            <div hidden={folded}>
              {questions.map((q, i) => {
                const isOpen = at === i;
                // EVERY card stays mounted, hidden and inert while its tab is
                // closed, so its picks and typed text live through a change
                // of tab and a fold (round 7).
                return (
                  <div key={q.key} hidden={!isOpen} inert={!isOpen} data-dock-question={q.key}>
                    {q.render({
                      ...(isOpen
                        ? {
                            headerAside: fold,
                            ...(tabs ? { tabs, tabPanel: { id: cardPanelId(uid), labelledBy: cardTabId(uid, at) } } : {}),
                          }
                        : {}),
                      inDock: true,
                      dockShown: isOpen && !folded,
                    })}
                  </div>
                );
              })}
            </div>
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
        </>
      )}
    </section>
  );
}

/**
 * The one polite live region of the dock (the a11y line of §24.11). It says
 * "N items wait for you" when the count goes up, and nothing when it goes
 * down. The count holds the approvals in the thread too (D9). The chat mounts
 * it while the flag is on, before any item waits, so a screen reader hears
 * the first arrival too. It starts from 0.
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
