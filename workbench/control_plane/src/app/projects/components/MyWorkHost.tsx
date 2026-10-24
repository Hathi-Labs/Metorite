"use client";

/**
 * Projects · My work (WS-64 OP-2, `one_projects_app.md` §9 OP-2 item 4).
 *
 * Draws one of the six My work views and mounts, ONCE, the hosts that
 * `app/tasks/page.tsx` mounts around the same views: capture, the task
 * overlay, undo, the stage prompt, promote and delete. The views are the
 * components of `app/tasks/components`, imported by path. No file of
 * `/tasks` is copied, and nothing imports its page. Fence: `myWork.test.ts`.
 *
 * The component is mounted only while a My work view is open, so
 * `hydrate()` runs only then. The store is the one store of D52 to D54.
 */

import { useEffect } from "react";

import Button from "@/components/ui/Button";
import { DelegatePopup } from "@/app/tasks/components/DelegatePopup";
import { DeleteConfirmModal } from "@/app/tasks/components/DeleteConfirmModal";
import { EliminatePopup } from "@/app/tasks/components/EliminatePopup";
import { InboxView } from "@/app/tasks/components/InboxView";
import { ItemList } from "@/app/tasks/components/ItemList";
import { PromoteHost } from "@/app/tasks/components/PromoteHost";
import { PromoteToast } from "@/app/tasks/components/PromoteToast";
import { QuickCapture } from "@/app/tasks/components/QuickCapture";
import { ReclarifyModal } from "@/app/tasks/components/ReclarifyModal";
import { SchedulePopup } from "@/app/tasks/components/SchedulePopup";
import { StagePromptHost } from "@/app/tasks/components/StagePromptHost";
import { SyncFailureToast } from "@/app/tasks/components/SyncFailureToast";
import { TaskFocusModal } from "@/app/tasks/components/TaskFocusModal";
import { UndoToast } from "@/app/tasks/components/UndoToast";
import { useTaskStore } from "@/app/tasks/lib/taskStore";

import { useUndoScope } from "@/components/UndoProvider";

import { tasksEntryView } from "@/app/tasks/lib/today";

import { myWorkView, type MyWorkId } from "../lib/myWork";

export default function MyWorkHost({ app }: { app: MyWorkId }) {
  const hydrate = useTaskStore((s) => s.hydrate);
  const loadMyToday = useTaskStore((s) => s.loadMyToday);
  const loadDone = useTaskStore((s) => s.loadDone);
  const selectView = useTaskStore((s) => s.selectView);
  const openQuickCapture = useTaskStore((s) => s.openQuickCapture);
  const view = useTaskStore((s) => s.selectedView);
  const wanted = myWorkView(app);

  // My work has its own undo scope, so Ctrl+Z never reaches the board history.
  // The page returns the board scope when this host unmounts.
  useUndoScope("projects:my-work");

  // The store is shared with /tasks. Put its view back when My work closes.
  useEffect(() => {
    const before = useTaskStore.getState().selectedView;
    return () => {
      useTaskStore.getState().selectView(tasksEntryView(before));
    };
  }, []);

  // A tab left open past midnight moves on: read the date again on return.
  useEffect(() => {
    const again = () => {
      if (document.visibilityState === "visible") void loadMyToday();
    };
    document.addEventListener("visibilitychange", again);
    window.addEventListener("focus", again);
    return () => {
      document.removeEventListener("visibilitychange", again);
      window.removeEventListener("focus", again);
    };
  }, [loadMyToday]);

  // The store loads when a My work view opens, and not before.
  useEffect(() => {
    void hydrate();
    void loadMyToday();
  }, [hydrate, loadMyToday]);

  // The sidebar entry chooses the store's view. A scope left by `/tasks`
  // (an Area, a context) does not follow the member into My work.
  useEffect(() => {
    if (!wanted) return;
    selectView(wanted);
    useTaskStore.getState().selectArea(null);
    if (wanted === "done") void loadDone();
  }, [wanted, selectView, loadDone]);

  // Draw nothing until the store holds the view the entry chose, so a stale
  // view of `/tasks` never flashes.
  const ready = wanted !== null && view === wanted;

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden" data-testid="my-work-host">
      {ready ? (
        wanted === "inbox" ? (
          <div className="min-h-0 flex-1 overflow-hidden">
            <InboxView />
          </div>
        ) : (
          <>
            <div className="flex items-center justify-end border-b border-border bg-card px-3 py-2">
              <Button
                variant="secondary"
                size="sm"
                icon="Plus"
                onClick={() => openQuickCapture("single")}
              >
                Capture
              </Button>
            </div>
            <div className="min-h-0 flex-1">
              <ItemList />
            </div>
          </>
        )
      ) : null}
      <QuickCapture />
      <TaskFocusModal />
      <ReclarifyModal />
      <UndoToast />
      <StagePromptHost />
      <SyncFailureToast />
      <PromoteToast />
      <PromoteHost />
      <DeleteConfirmModal />
      <SchedulePopup />
      <EliminatePopup />
      <DelegatePopup />
    </div>
  );
}
