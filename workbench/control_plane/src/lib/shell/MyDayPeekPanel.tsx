"use client";

/**
 * The panel on My Day (PK-1, task 2469, D204, `my_day_feed.md` §11.5).
 *
 * It shows what a row of My Day links to, on My Day. PK-1 shows a task.
 *
 * ⚠️ **Two shared parts, and nothing of its own.**
 *
 * - **The frame is the record card's** (`components/CardPanel.tsx`, D181,
 *   `one_record_card.md` §3.1). `useCardStop` picks the stop: a column
 *   beside the page, the same card over the page, or the whole screen on a
 *   phone. It owns Escape, the scrim, focus return, and `inert` on the page
 *   behind a card that stands over it. My Day asks it to keep 36 rem of page
 *   beside a docked card (`MY_DAY_PAGE_MIN_REM`).
 * - **The body is Projects' own `TaskPanel`**: its header (the ref, the
 *   copy-link, the expand toggle, the title, the chips) and `TaskBody`. The
 *   task comes off its id through `useTaskById`, the one loader that My
 *   Tasks' detail reads through too.
 *
 * What this file adds: the way out ("Open in Projects", D101 point 3), the
 * focus on open, a click on a `#123` pill that opens that task here, and a
 * tap on a phone bar tab that closes the card.
 *
 * `useMyDayPeek.tsx` loads this file on demand, so the task body is not part
 * of My Day's first load. Fence: `myDayPeek.test.ts`.
 */

import { type MouseEvent, type RefObject, useEffect, useLayoutEffect, useState } from "react";

import { TaskPanel } from "@/app/projects/components/TaskPanel";
import { taskDeepLink } from "@/app/projects/lib/card";
import { type TaskById, useTaskById } from "@/app/projects/lib/useTaskById";
import CardPanel, { type CardStop, useCardStop } from "@/components/CardPanel";
import { LayoutBoundary } from "@/components/LayoutBoundary";
import { TaskHeaderRow } from "@/components/TaskPanelHeader";
import Button from "@/components/ui/Button";
import { Skeleton, SkeletonRows } from "@/components/ui/Skeleton";
import { useViewMode } from "@/components/ViewModeProvider";
import { useBarTap } from "@/lib/phoneBarTap";

import {
  MY_DAY_PAGE_MIN_REM,
  OPEN_APP_ATTR,
  OPEN_IN_PROJECTS,
  PEEK_FRAME_ATTR,
  type PeekTarget,
  barTapCloses,
  clickedLinkOf,
  panelModeFor,
  peekOfClick,
  sentenceOf,
} from "./myDayPeek";

export interface MyDayPeekPanelProps {
  target: PeekTarget;
  /** ✕ and Escape: back over the page's own history entries. */
  onClose: () => void;
  /**
   * A tap on a phone bar tab. The tab goes on to its own page, so this
   * closes in place and moves no history entry, or Back and the tab race.
   */
  onLeave: () => void;
  /** Open another target in the panel: a subtask, a linked task, a pill. */
  onOpen: (target: PeekTarget) => void;
  /** The panel wrote something. My Day reads its rows again on close. */
  onChanged: () => void;
  /** Where the card stands now. My Day draws one column only beside it. */
  onStop: (stop: CardStop) => void;
  /** The signed-in member, so an agent pill says "Needs you" to them. */
  me?: string;
}

/** The width of the card, in px, and the root font size, while it is on screen. */
function useCardWidth(ref: RefObject<HTMLElement | null>): { width: number; rem: number } {
  const [size, setSize] = useState({ width: 0, rem: 16 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = () =>
      setSize({ width: el.clientWidth, rem: parseFloat(getComputedStyle(document.documentElement).fontSize) || 16 });
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(el);
    return () => watch.disconnect();
  }, [ref]);
  return size;
}

export default function MyDayPeekPanel({
  target,
  onClose,
  onLeave,
  onOpen,
  onChanged,
  onStop,
  me,
}: MyDayPeekPanelProps) {
  const { isMobile } = useViewMode();
  const { stop, expand, asideRef } = useCardStop(isMobile, MY_DAY_PAGE_MIN_REM);
  const card = useCardWidth(asideRef);
  // A merged task opens the task it went into, as Projects does.
  const loaded = useTaskById(target.kind === "task" ? target.id : null, { followMerge: true });

  // ⚠️ Above every return (rules of hooks). On a phone the card covers the
  // page, so a tap on a bar tab closes it. Ask keeps it (`barTapCloses`).
  useBarTap((tab) => {
    if (barTapCloses(tab)) onLeave();
  }, isMobile);

  useEffect(() => onStop(stop), [stop, onStop]);

  // The keyboard goes into the card on each open, at every stop. The card
  // itself moves it over the page and on a phone. A docked card does not, and
  // a member who opened a row with Enter must land on what opened.
  // A frame later, so it lands after the card's own focus work, which runs in
  // a microtask (`focusLandmark`). On a page load in a dev build, React runs
  // the effects twice, and that work took the focus back to the landmark.
  useEffect(() => {
    const aside = asideRef.current;
    if (!aside) return;
    const frame = window.requestAnimationFrame(() => {
      if (!aside.contains(document.activeElement)) aside.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [target.id, asideRef]);

  // A `#123` pill in the title, the description or a comment links
  // `/projects?task=<id>`. It opens here too. "Open in Projects" never does.
  const onClickCapture = (event: MouseEvent<HTMLElement>) => {
    const next = peekOfClick(event, clickedLinkOf(event));
    if (!next) return;
    event.preventDefault();
    onOpen(next);
  };

  const title = loaded.task?.title;
  return (
    <CardPanel stop={stop} label={title ? `Task: ${title}` : "Task"} onClose={onClose} asideRef={asideRef}>
      {/* The card draws the frame and the edge. `TaskPanel` is an `<aside>`
          with its own width and edge for the Projects page, so both are
          lifted here, as the Projects phone frame lifts the width. */}
      <div
        {...{ [PEEK_FRAME_ATTR]: target.kind }}
        className="flex min-h-0 w-full flex-1 [&>aside]:max-w-none [&>aside]:border-l-0"
        onClickCapture={onClickCapture}
      >
        <TaskPeek
          key={target.id}
          id={target.id}
          loaded={loaded}
          mode={panelModeFor(stop, card.width, card.rem)}
          expand={expand}
          onClose={onClose}
          onOpen={onOpen}
          onChanged={onChanged}
          me={me}
        />
      </div>
    </CardPanel>
  );
}

function TaskPeek({
  id,
  loaded,
  mode,
  expand,
  onClose,
  onOpen,
  onChanged,
  me,
}: {
  id: string;
  loaded: TaskById;
  mode: "side" | "full";
  expand: { expanded: boolean; onToggle: () => void } | undefined;
  onClose: () => void;
  onOpen: (target: PeekTarget) => void;
  onChanged: () => void;
  me?: string;
}) {
  const href = taskDeepLink({ id: loaded.task?.id ?? id });
  // A real link, so Ctrl-click and a middle click open a new tab too.
  const openApp = (
    <Button
      href={href}
      variant="ghost"
      size="icon-xs"
      icon="ArrowUpRight"
      aria-label={OPEN_IN_PROJECTS}
      title={OPEN_IN_PROJECTS}
      {...{ [OPEN_APP_ATTR]: "" }}
    />
  );

  if (!loaded.task) {
    // The read is out, or it failed. The header has the same close and the
    // same way out, so neither state traps the member. Escape is the card's.
    return (
      <div aria-busy={loaded.error ? undefined : true} className="flex h-full w-full flex-col bg-background glass:bg-card">
        <header className="shrink-0 border-b border-border bg-card px-3 py-3">
          <TaskHeaderRow taskRef="Task" actions={openApp} onClose={onClose} />
          {loaded.error ? null : <Skeleton className="mt-1 h-5 w-3/4" />}
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {loaded.error ? (
            <div role="alert" className="rounded-lg border border-border bg-card p-4">
              <p className="text-sm font-medium text-foreground">Could not open this task.</p>
              <p className="mt-1 break-words text-xs text-muted-foreground">
                {sentenceOf(loaded.error)}. Try again, or open it in Projects.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button size="sm" variant="secondary" icon="RefreshCw" onClick={loaded.retry}>
                  Try again
                </Button>
                <Button size="sm" variant="ghost" href={href}>
                  {OPEN_IN_PROJECTS}
                </Button>
              </div>
            </div>
          ) : (
            <>
              <span className="sr-only">Loading the task…</span>
              <SkeletonRows count={6} />
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    // A throw in the body stays in the card, and My Day stays on screen.
    <LayoutBoundary layout="task panel">
      <TaskPanel
        task={loaded.task}
        statuses={loaded.statuses}
        fields={loaded.fields}
        tags={loaded.tags}
        me={me}
        actions={openApp}
        // The card's stop is the panel's width stop (`usePanelMode`, the one
        // stored choice), with two columns only in a card wide enough for
        // them (`panelModeFor`). No toggle on a phone, nor where the page is
        // too narrow for a docked card.
        mode={mode}
        onMode={expand ? () => expand.onToggle() : undefined}
        onClose={onClose}
        onOpenTask={(taskId) => onOpen({ kind: "task", id: taskId })}
        onTaskAdded={onChanged}
        onChanged={(fresh) => {
          loaded.setTask(fresh);
          onChanged();
        }}
      />
    </LayoutBoundary>
  );
}
