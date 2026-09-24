"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { taskDeepLink } from "@/app/projects/lib/card";
import { useToast } from "@/components/ui/Toast";

import { onPromoteUnload, promoteBlocksUnload, promotePendingToast } from "../lib/promote";
import { useTaskStore } from "../lib/taskStore";

/**
 * The promote toast (my_tasks_cutover.md §5 S6g). The bridge between the
 * store's one deferred promote and THE confirmation channel (`useToast`).
 *
 * Both promote doors and the capture chip schedule through
 * `schedulePromote`, so this is the only place a promote speaks, and the
 * words come from `lib/promote.ts`. Three states, one toast key:
 *
 * 1. **waiting** — "Moving to {project}…" with **Undo**. Nothing is sent yet.
 * 2. **sending** — the same line, with no Undo. The request is in flight.
 * 3. **done** — "Moved to {project}" with **Open board**. No Undo, because
 *    D62 refuses a move back into my tree.
 *
 * A failure speaks through `SyncFailureToast`; this only takes its own toast
 * down.
 */
export function PromoteToast() {
  const pending = useTaskStore((s) => s.pendingPromote);
  const notice = useTaskStore((s) => s.promoteNotice);
  const undoPromote = useTaskStore((s) => s.undoPromote);
  const clearNotice = useTaskStore((s) => s.clearPromoteNotice);
  const toast = useToast();
  const router = useRouter();

  useEffect(() => {
    if (!pending) return;
    toast.show({
      key: pending.key,
      variant: "loading",
      ...promotePendingToast(pending.projectName),
      ...(pending.sending ? {} : { action: { label: "Undo", onClick: () => undoPromote() } }),
    });
  }, [pending, toast, undoPromote]);

  // S6g repair P2-b — a waiting promote lives only in this page. Ask before
  // the tab closes, because a close inside the window drops the move.
  const blocks = promoteBlocksUnload(pending);
  useEffect(() => {
    if (!blocks) return;
    window.addEventListener("beforeunload", onPromoteUnload);
    return () => window.removeEventListener("beforeunload", onPromoteUnload);
  }, [blocks]);

  useEffect(() => {
    if (!notice) return;
    if ("failed" in notice) {
      toast.dismiss(notice.key);
    } else {
      toast.show({
        key: notice.key,
        variant: "success",
        title: notice.title,
        description: notice.description,
        action: {
          label: "Open board",
          // The id survives the move (D53.4: same row), so the deep link
          // holds whether or not the task is still in my list.
          onClick: () => router.push(taskDeepLink({ id: notice.taskId })),
        },
      });
    }
    clearNotice();
  }, [notice, toast, router, clearNotice]);

  return null;
}
