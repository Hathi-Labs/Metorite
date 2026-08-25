"use client";

import Icon from "@/components/Icon";
import { useEffect, useMemo } from "react";
import { useTaskStore } from "../lib/taskStore";
import { TaskDetail } from "./ItemDetail";

const NOOP = () => {};

// Full-page task view — a ClickUp/Linear-style focused overlay over the same
// editable TaskDetail. Escape / backdrop / × closes it. It has two lives, and
// `tasks/page.tsx` picks which one by whether it passes `itemId`:
//
//  * UNCONTROLLED (`<TaskFocusModal />`) — driven by the store's
//    `focusedItemId`, i.e. `openFocus` from anywhere. This is the phone branch,
//    where the surface IS the screen (the same move Projects makes at
//    `projects/page.tsx:1329`), and every desktop surface that carries no
//    docked pane: Inbox, Engage, Calendar, Assistant.
//  * CONTROLLED (`itemId` + `onClose`) — the explicit **maximise** from the
//    docked detail pane. The pane is 380px and this detail is far denser than
//    Projects' (nine sections plus the provider ones), so maximise is what
//    keeps this `max-w-3xl` reading width available. The page derives `itemId`
//    from the pane's own selection, so maximise is a mode of that selection:
//    deleting the task, or navigating to another one (a subtask opened from
//    inside the overlay moves `selectedItemId`), lands back on the docked pane
//    instead of leaving a stale overlay up.
export function TaskFocusModal({
  itemId,
  onClose,
}: {
  /** Present (even as `null`) → controlled. Absent → store-driven. */
  itemId?: string | null;
  onClose?: () => void;
} = {}) {
  const focusedItemId = useTaskStore((s) => s.focusedItemId);
  const items = useTaskStore((s) => s.items);
  const backend = useTaskStore((s) => s.backend);
  const closeFocus = useTaskStore((s) => s.closeFocus);

  const controlled = itemId !== undefined;
  const openId = controlled ? itemId : focusedItemId;
  // useMemo, not a plain ternary: the Escape listener below depends on it, and
  // a fresh closure each render would re-bind the listener on every keystroke
  // the detail receives.
  const close = useMemo(
    () => (controlled ? (onClose ?? NOOP) : closeFocus),
    [controlled, onClose, closeFocus],
  );

  const item = openId ? items.find((i) => i.id === openId) : undefined;

  useEffect(() => {
    if (!openId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openId, close]);

  if (!item) return null;

  return (
    <div className="fixed inset-0 z-[80] flex items-start justify-center overflow-y-auto bg-background/70 backdrop-blur-sm sm:p-8">
      {/* backdrop */}
      <button
        type="button"
        aria-label="Close"
        onClick={close}
        className="absolute inset-0 -z-10 cursor-default"
      />
      {/* Full-screen sheet on mobile; floating card from sm: up. dvh (not
          vh) so the browser chrome on phones doesn't hide the bottom.
          Radius is `rounded-lg` off `--radius`, not a fixed 2xl — DESIGN_SYSTEM
          §4: a 16px corner ignores Graphite's 0.125rem and Material's 1rem. */}
      <div className="relative flex h-dvh max-h-dvh w-full max-w-3xl flex-col overflow-hidden border-border bg-card shadow-2xl sm:h-auto sm:max-h-[calc(100dvh-4rem)] sm:rounded-lg sm:border">
        <button
          type="button"
          onClick={close}
          aria-label="Close"
          className="tech-transition absolute right-3 top-3 z-10 rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground"
        >
          <Icon name="X" className="h-4 w-4" />
        </button>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <TaskDetail
            key={item.id}
            item={item}
            backend={backend}
            focused
          />
        </div>
      </div>
    </div>
  );
}
