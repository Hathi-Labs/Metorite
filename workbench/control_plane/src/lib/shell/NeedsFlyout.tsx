"use client";

/**
 * Needs you, beside its sidebar row (owner, 2026-11-01, prototype): "since
 * needs you and the bell were the same thing ... have it on the sidebar only.
 * Approvals should also fold into this, where multiple AI sessions running in
 * the background can ask for inputs and HITL nudges."
 *
 * ONE list of what waits on the member:
 * - **AI waiting on you.** The runs that wait on an answer or a confirmation
 *   (`needs_input`), from the one run store. A row opens that chat.
 * - **The feed.** `NeedsList`, the bell's own list: overdue and today's
 *   tasks, mail, and the approvals group (outward writes that wait for
 *   review), for a member who may see it.
 *
 * It slides out of the rail's edge at the row's height, over the page. There
 * is no scrim, so a Done keeps it open with its Undo toast in reach. Esc, a
 * press outside, the close button, a link, or the row again shut it.
 *
 * The open state is the bell's store (`openBell`). `panelHold.ts` keeps the
 * bell's Modal shut while the sidebar holds the list.
 */

import Link from "next/link";
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";

import { useAccess } from "@/components/AccessProvider";
import Icon from "@/components/Icon";
import { useActivityRows } from "@/hooks/useActiveSessions";
import { shouldPollWorkspace } from "@/lib/access";
import { wantLiveSteps } from "@/lib/liveRuns";
import { ActivityList, useHeldHrefs } from "./ActivityControl";
import { openAssistant } from "./assistant";
import { shellChatOn } from "./chatFlag";
import { NeedsList, useNeedsYou } from "./NeedsYouCard";
import { holdPanel } from "./panelHold";
import { closeBell, shownOfTotal, useBellOpen, useNeedsAllowed } from "./ShellBell";
import { homePane } from "./shellNav";

/** The folded rail's width, where the list docks while the rail peeks. */
const RAIL_PX = 56;
/** The gap between the rail and the list, the frame's own gap. */
const GAP_PX = 8;
/** How often the relative times move while the list is open. */
const TICK_MS = 30_000;

interface Place {
  left: number;
  top: number;
  maxHeight: number;
}

export function NeedsFlyout({ railRef, folded }: { railRef: RefObject<HTMLElement | null>; folded: boolean }) {
  const open = useBellOpen();
  const [mounted, setMounted] = useState(open);
  if (open && !mounted) setMounted(true);
  const [shown, setShown] = useState(false);
  const [place, setPlace] = useState<Place | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  // The sidebar holds the list while it is mounted, and shuts it on leaving.
  useEffect(() => {
    const release = holdPanel("needs");
    return () => {
      release();
      closeBell();
    };
  }, []);

  // Dock beside the rail at the row's height. A folded rail docks at its
  // folded edge, so a peek that ends under the list leaves it in place.
  useLayoutEffect(() => {
    if (!open) return;
    const measure = () => {
      const rail = railRef.current;
      if (!rail) return;
      const r = rail.getBoundingClientRect();
      const row = rail.querySelector("[data-nav-needs-you]")?.getBoundingClientRect();
      const vh = window.innerHeight;
      const want = Math.min(560, vh - 32);
      let top = row ? row.top - 6 : r.top + 48;
      if (top + want > vh - 16) top = Math.max(16, vh - 16 - want);
      setPlace({ left: r.left + (folded ? RAIL_PX : r.width) + GAP_PX, top, maxHeight: Math.min(want, vh - top - 16) });
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [open, folded, railRef]);

  // Slide in on the frame after mount, and out before unmount.
  useEffect(() => {
    if (!open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- the exit starts here
      setShown(false);
      return;
    }
    const id = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(id);
  }, [open]);

  // Esc, and a press outside the list and its row, shut it.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Element | null;
      if (!t || boxRef.current?.contains(t) || t.closest("[data-nav-needs-you]")) return;
      closeBell();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) closeBell();
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!mounted || !place || typeof document === "undefined") return null;
  return createPortal(
    <div
      ref={boxRef}
      role="dialog"
      aria-label="Needs you"
      data-needs-flyout={shown ? "open" : "closed"}
      style={{ left: place.left, top: place.top, maxHeight: place.maxHeight }}
      onTransitionEnd={(e) => {
        if (e.target === e.currentTarget && !open) setMounted(false);
      }}
      className={`fixed z-50 flex w-[23rem] origin-top-left flex-col overflow-hidden rounded-2xl border border-border bg-popover shadow-2xl transition-[opacity,transform] duration-200 ease-[cubic-bezier(0.32,0.72,0,1)] motion-reduce:transition-none glass:border-transparent glass:glass-menu ${
        shown ? "translate-x-0 scale-100 opacity-100" : "-translate-x-2 scale-[0.98] opacity-0"
      }`}
    >
      <NeedsBody open={open} />
    </div>,
    document.body,
  );
}

function NeedsBody({ open }: { open: boolean }) {
  const { access, loading } = useAccess();
  const workspace = shouldPollWorkspace(access, loading);
  const waiting = useActivityRows(workspace).filter((r) => r.state === "needs_input");
  const heldHrefs = useHeldHrefs();
  const [chatOn] = useState(() => shellChatOn());
  const { allowed, approvals } = useNeedsAllowed();
  const needs = useNeedsYou(allowed, approvals);
  const closeRef = useRef<HTMLButtonElement>(null);

  // The clock, and the run steps of the waiting runs, only while open.
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    if (!open) return;
    const release = wantLiveSteps();
    const tick = () => setNow(new Date());
    const first = setTimeout(tick, 0);
    const id = setInterval(tick, TICK_MS);
    return () => {
      release();
      clearTimeout(first);
      clearInterval(id);
    };
  }, [open]);

  // Focus moves in, so Esc and Tab work from the keyboard.
  useEffect(() => {
    if (open) closeRef.current?.focus({ preventScroll: true });
  }, [open]);

  const total = (needs.total ?? 0) + waiting.length;
  const cut = shownOfTotal(needs.items?.length ?? 0, needs.total);
  const home = homePane();
  // With AI rows above, an empty feed says nothing, not "all clear".
  const feedEmpty = needs.items !== undefined && needs.items.length === 0;

  return (
    <>
      <div className="flex shrink-0 items-center gap-2 pl-4 pr-2 pt-3 pb-2">
        <Icon name="Bell" size={15} className="text-muted-foreground" />
        <span className="text-sm font-semibold text-foreground glass:text-subhead">Needs you</span>
        {total > 0 ? (
          <span className="rounded-full bg-primary/15 px-1.5 text-[11px] font-semibold leading-4 tabular-nums text-primary glass:text-caption">
            {total}
          </span>
        ) : null}
        <button
          ref={closeRef}
          type="button"
          aria-label="Close the needs list"
          title="Close (Esc)"
          onClick={closeBell}
          className="ml-auto flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground tech-transition hover:bg-secondary hover:text-foreground"
        >
          <Icon name="X" size={14} />
        </button>
      </div>
      <div
        data-needs-panel=""
        className="min-h-0 flex-1 overflow-y-auto px-1 pb-1"
        onClick={(e) => {
          // A row is a link to its app. The list goes with the member.
          if ((e.target as Element).closest("a[href]")) closeBell();
        }}
      >
        {waiting.length > 0 ? (
          <section aria-label="AI waiting on you" className="pb-1">
            <div className="flex items-center gap-1.5 px-3 pt-1 pb-0.5 text-[11px] font-medium text-warning glass:text-caption">
              <Icon name="Sparkles" size={11} />
              AI waiting on you
            </div>
            <ActivityList
              rows={waiting}
              now={now?.getTime() ?? 0}
              visibleHrefs={heldHrefs}
              onNavigate={closeBell}
              onOpenChat={chatOn ? (row) => openAssistant({ sessionId: row.threadId }) : undefined}
            />
          </section>
        ) : null}
        {allowed && !(feedEmpty && waiting.length > 0) ? (
          <div className="px-1">
            <NeedsList needs={needs} now={now} />
          </div>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center justify-between gap-3 border-t border-border/60 px-4 py-2">
        <Link
          href="/"
          onClick={closeBell}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-primary underline-offset-2 hover:underline glass:text-footnote"
        >
          Open {home.label}
          <Icon name="ArrowRight" size={12} />
        </Link>
        {cut ? <span className="text-xs text-muted-foreground glass:text-caption">{cut}</span> : null}
      </div>
    </>
  );
}
