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
 *   draws in the thread, as with the flag off. The row names the card that
 *   the queue in the thread shows now (its open tab), and its Review only
 *   scrolls the thread to that card. It commits nothing;
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
 * NOTHING MOVES UNDER THE POINTER (verifier F1, 2026-11-28). When an item
 * leaves (Submit, Not now, an answer from elsewhere, a run end) or the pointer
 * row leaves, the dock keeps its height. In the place of the item it shows an
 * inert line ("Answer sent", "Not now", "Answered elsewhere"). The line is no
 * button. A fold by hand keeps the height too, with an empty gap. The dock
 * collapses, or draws the next item, only under rule 2 (`collapseWait`):
 * 800 ms since the item left, no pointer down, and 600 ms since the last
 * press in the dock. The collapse fades the line out over 150 ms, then the
 * height goes in one step (no motion of a layout property, UI gate G3).
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
import { prefersReducedMotion } from "@/lib/panelMotion";
import { revealWithin } from "@/lib/scrollWithin";
import {
  COLLAPSE_MS,
  LEFT_WORDS,
  PINNED_ACTIONS,
  STUCK_PRESS_MS,
  arrivalUnfolds,
  collapseWait,
  dockCap,
  dockFolded,
  isTextField,
  keyboardUp,
  liveAfter,
  POINTER_LEAD,
  pointerMore,
  UNFOLD_LABEL,
  type DockLeft,
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

/** The pointer row: the approval that the thread's queue shows now (D9). */
export interface DockPointer {
  /** The tab label of the approval that the queue shows (`tabLabel`). */
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
  /**
   * Why each question card that waits no more left, by its key: the member
   * answered it ("sent") or declined it ("notnow"). A key that is not here
   * left "elsewhere" (`leftKind`). The inert line reads it.
   */
  left?: Readonly<Record<string, DockLeft>>;
}

/** A place of the dock that holds its height after its item left. */
interface HeldLine {
  h: number;
  words: string;
  icon: string;
}

/**
 * The hold (F1): while it is set, the dock never shrinks. `since` is the time
 * the last item left. `body` and `pointer` are the inert lines in the places
 * of what left. `bodyKept` and `pointerKept` say which part still draws as it
 * did when the hold began. An item that arrives in the hold waits for rule 2.
 */
interface Hold {
  since: number;
  rootH: number;
  body: HeldLine | null;
  pointer: HeldLine | null;
  bodyKept: boolean;
  pointerKept: boolean;
  collapsing: boolean;
}

/** The facts of the last commit, which a removal reads. */
interface LastDraw {
  keys: readonly string[];
  openKey: string | null;
  pointerRow: boolean;
  bodyShown: boolean;
  folded: boolean;
  rootH: number;
  bodyH: number;
  pointerH: number;
}

const LEFT_ICON: Record<DockLeft, string> = { sent: "Check", notnow: "Clock", elsewhere: "CheckCheck" };

/** The inert line in the place of an item that left. It is no button. */
function Held({ line, collapsing, part }: { line: HeldLine; collapsing: boolean; part: "body" | "pointer" }) {
  return (
    <div
      data-dock-held={part}
      style={{ height: line.h }}
      className={`flex min-w-0 shrink-0 items-center justify-center gap-1.5 rounded-lg border border-border bg-card px-3 text-xs text-muted-foreground glass:rounded-[16px] motion-safe:transition-opacity motion-safe:duration-150 ${collapsing ? "opacity-0" : "opacity-100"}`}
    >
      <Icon name={line.icon} size={13} className="shrink-0" />
      <span className="truncate">{line.words}</span>
    </div>
  );
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

export default function WaitingDock({ questions, pointer, phone, initialFolded = false, left }: WaitingDockProps) {
  const rootRef = useRef<HTMLElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const pointerRef = useRef<HTMLDivElement>(null);
  const uid = useId();
  const [manual, setManual] = useState(initialFolded);
  const [fieldFocus, setFieldFocus] = useState(false);
  const [size, setSize] = useState<{ chat: number; composer: number } | null>(null);
  const [layout, setLayout] = useState(0);
  const [over, setOver] = useState(false);
  // Rule 10: the fade at the foot of the body, above the pinned Submit row.
  // It shows while more of the body is below, and goes at the end.
  const [atEnd, setAtEnd] = useState(true);
  // The fade's distance from the foot of the dock: the top of the pinned
  // Submit row, read from the boxes. The fade then never covers the row.
  const [fadeBottom, setFadeBottom] = useState(0);
  // F1: the hold. Null while nothing has left.
  const [hold, setHold] = useState<Hold | null>(null);
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
      const root = rootRef.current;
      const h = pin && root ? Math.max(0, Math.ceil(root.getBoundingClientRect().bottom - pin.getBoundingClientRect().top)) : 0;
      setFadeBottom((x) => (x === h ? x : h));
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
  // F1: the facts of the last commit, which a removal reads, because the
  // commit that removes an item has already drawn the dock without it.
  const lastRef = useRef<LastDraw>({
    keys: [], openKey: null, pointerRow: false, bodyShown: false, folded: false, rootH: 0, bodyH: 0, pointerH: 0,
  });
  const holdRef = useRef<Hold | null>(null);
  // The heights of what draws now. Outside a hold only: in a hold the frame
  // is held, and the body may be hidden. A pick or typed text can change the
  // card's height with no render of the dock, so the resize observer calls
  // it too.
  const remember = useCallback(() => {
    if (holdRef.current) return;
    const last = lastRef.current;
    const root = rootRef.current;
    const body = bodyRef.current;
    const row = pointerRef.current;
    if (root && !root.hidden) last.rootH = root.offsetHeight;
    if (body && !body.hidden) last.bodyH = body.offsetHeight;
    if (row) last.pointerH = row.offsetHeight;
  }, []);
  useEffect(() => {
    const root = rootRef.current;
    const a = around(root);
    if (!root || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      read();
      remember();
    });
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
  }, [read, remember]);

  // ── F1: the hold. Nothing moves under the pointer. ──────────────────────
  // What draws now. In a hold, an item that arrived after the hold began
  // waits for rule 2, so it never draws under a press of the item before it.
  const pointerRow = !!pointer && (hold ? !hold.pointer && hold.pointerKept : true);
  const bodyShown = count > 0 && (hold ? !hold.body && hold.bodyKept : true);
  const empty = !hold && count === 0 && !pointer;
  // The presses that the collapse rule reads: every pointer that is down,
  // and the last pointerdown or keydown inside the dock.
  const downRef = useRef(new Map<number, number>());
  const lastPressRef = useRef(Number.NEGATIVE_INFINITY);
  useEffect(() => {
    const down = (e: PointerEvent) => { downRef.current.set(e.pointerId, performance.now()); };
    const up = (e: PointerEvent) => { downRef.current.delete(e.pointerId); };
    // A touch scroll ends in `pointercancel`, not `pointerup`.
    const clear = () => downRef.current.clear();
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

  // Each commit: find what left since the last one, and start or extend the
  // hold BEFORE the paint. A set state in a layout effect draws again at
  // once, so no frame shows the dock shorter.
  useLayoutEffect(() => {
    const last = lastRef.current;
    const keys = questions.map((q) => q.key);
    const gone = last.keys.filter((k) => !keys.includes(k));
    const pointerGone = last.pointerRow && !pointer;
    // The member's own fold, or K7 with the keyboard down. With the keyboard
    // up, the keyboard itself moved everything, and a held height would
    // cover the two lines of the thread (F1 of 2026-10-23).
    const foldNow = folded && !last.folded && last.bodyShown && !kbUp;
    const starts = gone.length > 0 || pointerGone || foldNow;
    if (starts) {
      const now = performance.now();
      const goneKey = gone.includes(last.openKey ?? "") ? last.openKey! : gone[0];
      const why: DockLeft = (goneKey && left?.[goneKey]) || "elsewhere";
      setHold((h) => ({
        since: now,
        rootH: h ? h.rootH : last.rootH,
        body: h?.body ?? (gone.length > 0 && last.bodyShown ? { h: last.bodyH, words: LEFT_WORDS[why], icon: LEFT_ICON[why] } : null),
        pointer: h?.pointer ?? (pointerGone ? { h: last.pointerH, words: LEFT_WORDS.elsewhere, icon: LEFT_ICON.elsewhere } : null),
        bodyKept: h ? h.bodyKept : last.bodyShown,
        pointerKept: h ? h.pointerKept : last.pointerRow,
        collapsing: false,
      }));
    }
    lastRef.current = { ...last, keys, openKey: open?.key ?? null, pointerRow, bodyShown, folded };
    holdRef.current = hold;
    // The heights of the commit that removes an item are already the
    // shorter ones, so that commit keeps the old heights.
    if (!starts) remember();
  });

  // Rule 2: collapse, or draw the next item, only when all three hold. The
  // collapse fades the inert lines over `COLLAPSE_MS`, then the height goes
  // in one step. A press in the fade puts the hold back.
  useEffect(() => {
    if (!hold) return;
    let t: ReturnType<typeof setTimeout> | undefined;
    const check = () => {
      const now = performance.now();
      const wait = collapseWait({ now, leftAt: hold.since, pointerDown: pointerDown(now), lastPressAt: lastPressRef.current });
      if (wait === 0) {
        setHold((h) => (h !== hold ? h : h.collapsing ? null : { ...h, collapsing: true }));
        return;
      }
      if (hold.collapsing) {
        setHold((h) => (h !== hold ? h : { ...h, collapsing: false }));
        return;
      }
      t = setTimeout(check, wait ?? 50);
    };
    t = setTimeout(check, hold.collapsing && !prefersReducedMotion() ? COLLAPSE_MS : 0);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `pointerDown` reads refs only
  }, [hold]);

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
    // Rule 2 of the hold reads the last press in the dock.
    lastPressRef.current = performance.now();
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
  const onKeyDown = () => {
    lastPressRef.current = performance.now();
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

  // The place under the pointer row, when a body follows it.
  const pointerPlace = !!hold?.pointer || pointerRow;
  const bodyPlace = !!hold?.body || bodyShown;
  const gap = pointerPlace && bodyPlace ? "mt-2" : "";
  return (
    <section
      ref={rootRef}
      hidden={empty}
      data-waiting-dock={empty ? undefined : hold?.body ? "held" : count === 0 ? "pointer" : folded ? "folded" : "open"}
      {...(hold ? { "data-dock-hold": hold.collapsing ? "collapsing" : "held" } : {})}
      aria-label="Waiting for you"
      onFocus={onFocusIn}
      onBlur={onFocusOut}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      className="relative mx-auto mb-2 flex max-w-3xl flex-col pt-2"
      // F1: in a hold the dock never shrinks. The gap goes between the
      // pointer row and the body, so each keeps its place.
      style={hold ? { minHeight: hold.rootH } : undefined}
    >
      {hold?.pointer ? (
        <Held line={hold.pointer} collapsing={hold.collapsing} part="pointer" />
      ) : pointerRow && pointer ? (
        // D9: one row for the approvals, which wait in the thread. It names
        // the card that the queue shows now, and Review only scrolls to it.
        // It commits nothing.
        <div
          ref={pointerRef}
          data-dock-pointer=""
          className="flex min-w-0 shrink-0 items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-1 text-[11px] glass:rounded-[16px] glass:border-transparent glass:bg-primary/12 glass:px-4 glass:text-footnote"
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
      ) : null}
      {/* The gap of a hold: empty and inert. It takes the height that left. */}
      {hold && <div aria-hidden className="min-h-0 flex-1" data-dock-gap="" />}
      {hold?.body && (
        <div className={gap}>
          <Held line={hold.body} collapsing={hold.collapsing} part="body" />
        </div>
      )}
      {count > 0 && (
        <>
          {/* The one scroll box of the dock: the open question over the cap (C3).
              In a hold it stays mounted, hidden and inert, so no pick is lost. */}
          <div
            ref={bodyRef}
            data-dock-body=""
            hidden={!bodyShown}
            inert={!bodyShown}
            className={`overflow-y-auto overscroll-contain scrollbar-thin shrink-0 rounded-lg glass:rounded-[16px] ${hold?.body ? "" : gap}`}
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
                      // Not on the screen in a hold: the arm drops, and it
                      // starts again when the card draws (round 7, rule 3).
                      dockShown: isOpen && !folded && bodyShown,
                    })}
                  </div>
                );
              })}
            </div>
          </div>
          {/* The fade: theme tokens only, and it takes no click. Its foot is
              the top of the pinned Submit row, so it covers no control there. */}
          {bodyShown && !folded && over && !atEnd && (
            <div
              aria-hidden
              data-dock-fade=""
              className="pointer-events-none absolute inset-x-px z-[2] h-8 bg-gradient-to-t from-card to-transparent"
              style={{ bottom: fadeBottom }}
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
