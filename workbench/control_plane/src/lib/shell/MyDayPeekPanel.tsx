"use client";

/**
 * The panel on My Day (PK-1, task 2469, D204, `my_day_feed.md` §11.5).
 *
 * It shows what a row of My Day links to, on My Day. PK-1 shows a task.
 *
 * ⚠️ **It hosts Projects' own `TaskPanel`, never a second task view.** The
 * header (the ref, the copy-link, the title, the chips) and the body
 * (`TaskBody`) are the ones Projects draws, so a change there shows here at
 * once. The task comes off its id through `useTaskById`, the one loader that
 * My Tasks' detail reads through too. What this file adds is the frame and
 * the way out:
 *
 * - **Desktop:** a column at the right of My Day, at the docked width of
 *   every task panel (`PANEL_WIDTH_CLASS.side`). The page column beside it
 *   stays live, so the member can open the next row.
 * - **Phone:** the task card's own phone frame (`TASK_CARD_FRAME.phone`). It
 *   ends at the top edge of the phone bar, and a tap on a bar tab closes it
 *   (`useCloseOnBarTap`).
 * - **"Open in Projects"** in the header, the act D101 point 3 keeps: the
 *   member can always open the app.
 *
 * `useMyDayPeek.tsx` loads this file on demand, so the task body is not part of
 * My Day's first load. Fence: `myDayPeek.test.ts`.
 */

import { TaskPanel } from "@/app/projects/components/TaskPanel";
import { taskDeepLink } from "@/app/projects/lib/card";
import { useTaskById } from "@/app/projects/lib/useTaskById";
import { LayoutBoundary } from "@/components/LayoutBoundary";
import { TaskHeaderRow } from "@/components/TaskPanelHeader";
import Button from "@/components/ui/Button";
import { Skeleton, SkeletonRows } from "@/components/ui/Skeleton";
import { useViewMode } from "@/components/ViewModeProvider";
import { TASK_CARD_FRAME } from "@/lib/layers";
import { PANEL_WIDTH_CLASS } from "@/lib/panelMode";
import { useCloseOnBarTap } from "@/lib/phoneBarTap";

import { OPEN_IN_PROJECTS, PEEK_FRAME_ATTR, type PeekTarget } from "./myDayPeek";

export interface MyDayPeekPanelProps {
  target: PeekTarget;
  /** ✕ and Escape: back over the page's own history entries. */
  onClose: () => void;
  /**
   * A tap on a phone bar tab. The tab goes on to its own page, so this
   * closes in place and moves no history entry, or Back and the tab race.
   */
  onLeave: () => void;
  /** Open another target in the panel: a subtask or a linked task. */
  onOpen: (target: PeekTarget) => void;
  /** The panel wrote something. My Day reads its rows again on close. */
  onChanged: () => void;
  /** The signed-in member, so an agent pill says "Needs you" to them. */
  me?: string;
}

/** The frame on each layout. Pure, so a test reads it. */
export function peekFrameClass(phone: boolean): string {
  return phone
    ? `${TASK_CARD_FRAME.phone} flex bg-background pt-safe [&>aside]:max-w-none`
    : `flex h-full w-full min-w-0 ${PANEL_WIDTH_CLASS.side} shrink-0`;
}

export default function MyDayPeekPanel({ target, onClose, onLeave, onOpen, onChanged, me }: MyDayPeekPanelProps) {
  const { isMobile } = useViewMode();
  // ⚠️ Above every return (rules of hooks). On a phone the frame covers the
  // page, so a tap on a bar tab closes it, as every overlay does.
  useCloseOnBarTap(onLeave, isMobile);
  return (
    <div {...{ [PEEK_FRAME_ATTR]: target.kind }} className={peekFrameClass(isMobile)}>
      {/* PK-1 knows one kind. PK-2 and PK-3 add a branch each. */}
      <TaskPeek key={target.id} id={target.id} onClose={onClose} onOpen={onOpen} onChanged={onChanged} me={me} />
    </div>
  );
}

function TaskPeek({
  id,
  onClose,
  onOpen,
  onChanged,
  me,
}: {
  id: string;
  onClose: () => void;
  onOpen: (target: PeekTarget) => void;
  onChanged: () => void;
  me?: string;
}) {
  // A merged task opens the task it went into, as Projects does.
  const loaded = useTaskById(id, { followMerge: true });
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
      data-peek-open-app=""
    />
  );

  if (!loaded.task) {
    // The read is out, or it failed. The header has the same close and
    // the same way out, so neither state traps the member.
    return (
      <aside
        aria-label="Task"
        aria-busy={loaded.error ? undefined : true}
        className="flex h-full w-full flex-col border-l border-border bg-background glass:bg-card"
      >
        <header className="shrink-0 border-b border-border bg-card px-3 py-3">
          <TaskHeaderRow taskRef="Task" actions={openApp} onClose={onClose} />
          {loaded.error ? null : <Skeleton className="mt-1 h-5 w-3/4" />}
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {loaded.error ? (
            <div role="alert" className="rounded-lg border border-border bg-card p-4">
              <p className="text-sm font-medium text-foreground">Could not open this task.</p>
              <p className="mt-1 break-words text-xs text-muted-foreground">
                {loaded.error}. Try again, or open it in Projects.
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
      </aside>
    );
  }

  return (
    // A throw in the body stays in the panel, and My Day stays on screen.
    <LayoutBoundary layout="task panel">
      <TaskPanel
        task={loaded.task}
        statuses={loaded.statuses}
        fields={loaded.fields}
        tags={loaded.tags}
        me={me}
        actions={openApp}
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
