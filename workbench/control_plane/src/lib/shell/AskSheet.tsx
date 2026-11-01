"use client";

/**
 * The Ask bar's sheet (owner, 2026-11-01, prototype): the live runs, opened
 * OUT of the bar. "Instead of showing me a card the chat box can expand in
 * height to show me what is happening." The bar's own surface grows upward,
 * so the AI's work shows where the member talks to the AI, and the field
 * stays under it. Needs you is My Day's (the sidebar's My Day row).
 *
 * - **The same list.** `ActivityList` is the activity panel's own code.
 * - **Open and close.** The bar's "N running" pill opens and shuts it. Esc,
 *   a press outside the bar, the close button or a row close it.
 *
 * The open state is the activity panel's store (`openActivity`).
 * `panelHold.ts` keeps that Modal shut while the bar holds the list.
 */

import { useEffect, useRef, useState, type RefObject } from "react";

import { useAccess } from "@/components/AccessProvider";
import Icon from "@/components/Icon";
import { useActivityRows, useRunActivity } from "@/hooks/useActiveSessions";
import { shouldPollWorkspace } from "@/lib/access";
import { wantLiveSteps } from "@/lib/liveRuns";
import { ActivityList, closeActivity, useHeldHrefs } from "./ActivityControl";
import { openAssistant } from "./assistant";
import { shellChatOn } from "./chatFlag";
import { holdPanel } from "./panelHold";

/**
 * The bar holds the runs list while it is mounted, and shuts it when it
 * leaves, so the Modal never opens behind the assistant. Esc and a press
 * outside the bar shut the sheet.
 */
export function useAskSheetHost(barRef: RefObject<HTMLElement | null>, open: boolean): void {
  useEffect(() => {
    const release = holdPanel("runs");
    return () => {
      release();
      closeActivity();
    };
  }, []);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Element | null;
      if (!t || barRef.current?.contains(t)) return;
      closeActivity();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) closeActivity();
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, barRef]);
}

/**
 * The sheet: a grid row that grows from 0 to its content's height, so the
 * bar's surface rises. It keeps its rows while it folds away.
 */
export function AskSheet({ open }: { open: boolean }) {
  const [mounted, setMounted] = useState(open);
  if (open && !mounted) setMounted(true);
  return (
    <div
      data-ask-sheet={open ? "open" : "closed"}
      inert={!open}
      onTransitionEnd={(e) => {
        if (e.target === e.currentTarget && !open) setMounted(false);
      }}
      className={`grid transition-[grid-template-rows] duration-(--panel-duration) ease-(--panel-easing) motion-reduce:transition-none ${
        open ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
      }`}
    >
      <div className="min-h-0 overflow-hidden">
        <div
          data-ask-sheet-body=""
          className={`transition-[opacity,transform] duration-(--panel-duration) ease-(--panel-easing) motion-reduce:transition-none ${
            open ? "translate-y-0 opacity-100 delay-75" : "translate-y-2 opacity-0"
          }`}
        >
          {mounted ? <RunsBody open={open} /> : null}
        </div>
      </div>
    </div>
  );
}

/** How often the relative times move while the sheet is open. */
const TICK_MS = 30_000;

function RunsBody({ open }: { open: boolean }) {
  const { access, loading } = useAccess();
  const workspace = shouldPollWorkspace(access, loading);
  const { total: running } = useRunActivity(null, workspace);
  const rows = useActivityRows(workspace);
  const heldHrefs = useHeldHrefs();
  const [chatOn] = useState(() => shellChatOn());
  const closeRef = useRef<HTMLButtonElement>(null);

  // The clock and the run steps, only while open.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!open) return;
    const release = wantLiveSteps();
    const tick = () => setNow(Date.now());
    const first = setTimeout(tick, 0);
    const id = setInterval(tick, TICK_MS);
    return () => {
      release();
      clearTimeout(first);
      clearInterval(id);
    };
  }, [open]);

  return (
    <div className="flex flex-col border-b border-border/60">
      <div className="flex items-center gap-2 pl-4 pr-2 pt-2.5 pb-1">
        <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-success motion-safe:animate-pulse" />
        <span className="text-xs font-semibold text-foreground glass:text-footnote">
          {running > 0 ? `${running} running` : "Nothing running"}
        </span>
        <button
          ref={closeRef}
          type="button"
          aria-label="Hide the runs"
          title="Hide (Esc)"
          onClick={closeActivity}
          className="ml-auto flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground tech-transition hover:bg-secondary hover:text-foreground"
        >
          <Icon name="ChevronDown" size={15} />
        </button>
      </div>
      <div className="max-h-[min(24rem,50vh)] overflow-y-auto px-1 pb-1">
        <ActivityList
          rows={rows}
          now={now}
          visibleHrefs={heldHrefs}
          onNavigate={closeActivity}
          onOpenChat={chatOn ? (row) => openAssistant({ sessionId: row.threadId }) : undefined}
        />
      </div>
    </div>
  );
}
