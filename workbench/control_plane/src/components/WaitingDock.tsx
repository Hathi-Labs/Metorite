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
 * - a slim row with the Fold control and, when the card is over the cap, the
 *   Expand control. No control drops an item unanswered (WD-D2);
 * - the ONE scroll box of the dock: the body of the open item, under the cap
 *   (`lib/waitingDock.ts` `dockCap`). That is the one amendment to C3;
 * - one polite live region: "N items wait for you" when the count goes up.
 *   The arrival of an item never takes the focus (`AgentChat` holds K1).
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
import { dockCap, dockFolded, isTextField, keyboardUp, liveAnnouncement } from "@/lib/waitingDock";

export interface WaitingDockProps {
  /** How many items wait on the member (`dockCount`). */
  count: number;
  /** The phone: the phone chrome, or a device with a soft keyboard. */
  phone: boolean;
  /** The cards. `folded` draws them as one row, and `unfold` opens them. */
  children: (folded: boolean, unfold: () => void) => ReactNode;
  /** Tests and the visual rig: start folded. */
  initialFolded?: boolean;
  /** Tests: what the live region said last. */
  initialLive?: string;
}

/**
 * The heights the cap reads, from the form. `chat` is the room that the
 * thread and the form share (the thread is the form's previous sibling), so a
 * header row above the thread is not counted. `composer` is all of the form
 * but the dock's body: the box, its rows, and the dock's own control row.
 */
function measure(root: HTMLElement | null, body: HTMLElement | null): { chat: number; composer: number } | null {
  const form = root?.closest("form");
  const thread = form?.previousElementSibling as HTMLElement | null | undefined;
  if (!root || !body || !form || !thread) return null;
  return {
    chat: thread.clientHeight + form.offsetHeight,
    composer: Math.max(0, form.offsetHeight - body.offsetHeight),
  };
}

export default function WaitingDock({ count, phone, children, initialFolded = false, initialLive = "" }: WaitingDockProps) {
  const rootRef = useRef<HTMLElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [manual, setManual] = useState(initialFolded);
  const [expanded, setExpanded] = useState(false);
  const [fieldFocus, setFieldFocus] = useState(false);
  const [size, setSize] = useState<{ chat: number; composer: number } | null>(null);
  const [layout, setLayout] = useState(0);
  const [over, setOver] = useState(false);
  const vp = useVisualViewport();

  // The live region speaks when the count goes up, and only then. React's
  // adjust-state-during-render pattern, so a render that runs twice agrees.
  const [prev, setPrev] = useState(count);
  const [live, setLive] = useState(initialLive);
  if (prev !== count) {
    setPrev(count);
    const said = liveAnnouncement(prev, count);
    if (said) setLive(said);
  }

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
    }
  }, []);
  // Each render: the form, the column and the body may have changed. Each
  // `set` runs only when a value changed, so it settles.
  useLayoutEffect(() => {
    read();
  });
  useEffect(() => {
    const root = rootRef.current;
    const form = root?.closest("form");
    const column = form?.parentElement;
    const thread = form?.previousElementSibling;
    if (!root || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => read());
    ro.observe(root);
    if (form) ro.observe(form);
    if (column) ro.observe(column);
    if (thread) ro.observe(thread);
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
    const next = e.relatedTarget as HTMLElement | null;
    if (next && rootRef.current?.contains(next) && isTextField(next)) return;
    setFieldFocus(false);
  };
  // K7: the fold row of the keyboard opens the card only when the member
  // asks. A tap on its button opens the card and drops the keyboard.
  const unfold = () => {
    setManual(false);
    if (kbUp && typeof document !== "undefined") (document.activeElement as HTMLElement | null)?.blur?.();
  };

  return (
    <section
      ref={rootRef}
      data-waiting-dock={folded ? "folded" : "open"}
      aria-label="Waiting for you"
      onFocus={onFocusIn}
      onBlur={onFocusOut}
      className="max-w-3xl mx-auto mb-2"
    >
      <p role="status" aria-live="polite" className="sr-only" data-dock-live="">
        {live}
      </p>
      {!folded && (
        <div className="flex items-center justify-end gap-1 pb-1" data-dock-controls="">
          {(over || expanded) && (
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              icon={expanded ? "Minimize2" : "Maximize2"}
              onClick={() => setExpanded((v) => !v)}
              aria-label={expanded ? "Make the card smaller" : "Expand the card"}
              title={expanded ? "Make the card smaller" : "Expand the card"}
              data-dock-expand={expanded ? "on" : "off"}
            />
          )}
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
        </div>
      )}
      {/* The one scroll box of the dock: the open item over the cap (C3). */}
      <div
        ref={bodyRef}
        data-dock-body=""
        className="overflow-y-auto overscroll-contain scrollbar-thin"
        style={folded ? undefined : { maxHeight: cap === null ? "50vh" : `${cap}px` }}
      >
        {children(folded, unfold)}
      </div>
    </section>
  );
}
