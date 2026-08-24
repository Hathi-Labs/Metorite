"use client";

import { useEffect } from "react";
import { useTaskStore } from "@/app/tasks/lib/taskStore";
import { CalendarView } from "./CalendarView";
import { TaskFocusModal } from "@/app/tasks/components/TaskFocusModal";
import { SchedulePopup } from "@/app/tasks/components/SchedulePopup";
import { DeleteConfirmModal } from "@/app/tasks/components/DeleteConfirmModal";
import { UndoToast } from "@/app/tasks/components/UndoToast";

/**
 * Calendar — the Personal Center app for *when* your work happens.
 *
 * Spec: `project-docs/specs/calendar_focus_os.md` §10 · decision **D54**
 * (`work_plan.md` §3) · board **WS-39 slice S2**.
 *
 * **This is a lens, not a system.** D53 makes one task store the store of
 * record and gives it three surfaces: **Projects** answers "what is the company
 * doing", **Tasks** answers "what am I doing next", and this one answers "when
 * am I doing it". None is a copy of another, so a block dragged here moves the
 * same row a project manager is looking at.
 *
 * ⚠️ **The calendar has no store of its own** — measured 2026-08-24 and
 * recorded as D54.5, because two specs said otherwise. `gtd_time_blocks` and
 * `calendar_accounts` **do not exist**; scheduling is fields on the task row
 * (`routes/tasks/calendar.py` writes `gtd_items` directly), and the only
 * calendar-owned tables are `gtd_settings`, `gtd_day_state` and
 * `gtd_rollover_log`. That is why this page imports the task store rather than
 * owning one, and why those three tables must survive the `gtd_*` retirement
 * (D53.6).
 *
 * **Why it imports from `@/app/tasks/lib/`.** That is the shared task store,
 * and sharing it is the point — a second copy would be the CLAUDE.md §5
 * defect and would re-introduce exactly the sync this whole change removes.
 * The import direction is deliberately one-way and fenced: this app may read
 * `tasks/lib` and the store-driven overlays, and **Tasks may not import
 * anything from here** (`calendarBoundary.test.ts`). When WS-39 **S3a**
 * re-points the store at `pm_tasks`, the store moves once and both apps follow
 * it — which is why promoting it now, ahead of that rewrite, would be work
 * done twice.
 *
 * **The overlays are mounted here on purpose.** They are store-driven and the
 * calendar raises all four: `openFocus` → TaskFocusModal, `openSchedule` →
 * SchedulePopup, `requestDelete` → DeleteConfirmModal, `quickDispose` →
 * UndoToast. Before this app existed they were mounted by `/tasks` and the
 * calendar borrowed them; a standalone route that omitted them would answer a
 * right-click with silence. (`FocusSession` is NOT here — `AppShell` mounts it
 * globally, so a focus session survives navigating away.)
 */
export default function CalendarPage() {
  const hydrate = useTaskStore((s) => s.hydrate);

  // Same hydration the Tasks app performs. Both surfaces read one store, so
  // arriving directly at /calendar must not depend on having visited /tasks
  // first — that is the bug a shared store makes easy to write.
  useEffect(() => {
    void hydrate();
  }, [hydrate]);

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      <CalendarView />
      <TaskFocusModal />
      <SchedulePopup />
      <DeleteConfirmModal />
      <UndoToast />
    </div>
  );
}
