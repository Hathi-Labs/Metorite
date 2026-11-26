"use client";

/**
 * The one gesture of Mark done (task 3995). The row menu, the bulk bar, the
 * "Done" button of the reading pane and the key `d` all call it. The store
 * draws and writes. This says it in THE toast with a button that moves the
 * conversations back, and puts the same step on the one undo stack, so Ctrl+Z
 * runs it too. `lib/markDone.ts` holds the rules.
 */
import { useCallback } from "react";

import { useUndo } from "@/components/UndoProvider";
import { useToast } from "@/components/ui/Toast";

import { useEmailStore } from "../lib/emailStore";
import {
  MARK_DONE_TOAST_MS, doneTitle, markDoneEntry, moveBackLabel, threadKey,
} from "../lib/markDone";

export function useMarkDone() {
  const enabled = useEmailStore((s) => s.markDoneEnabled);
  const markThreadsDone = useEmailStore((s) => s.markThreadsDone);
  const moveThreadsBack = useEmailStore((s) => s.moveThreadsBack);
  const toast = useToast();
  const { record, undoEntry } = useUndo();

  const markDone = useCallback(
    async (rows: ReadonlyArray<{ accountId?: string; threadId?: string }>) => {
      const { marked } = await markThreadsDone(rows);
      if (marked.length === 0) return;
      const back = moveBackLabel(marked);
      // A key of its own for each act that can move back, so the button of
      // an earlier act stays while the member works down the list.
      const key = back ? `email-mark-done:${marked.map(threadKey).join()}` : undefined;
      const entry = back
        ? markDoneEntry(marked, {
            markDone: markThreadsDone,
            // Ctrl+Z runs the same step as the button. Once it ran, the toast
            // offers nothing more, so it closes.
            moveBack: async (threads) => {
              const failed = await moveThreadsBack(threads);
              if (failed === 0 && key) toast.dismiss(key);
              return failed;
            },
          })
        : null;
      if (entry) record(entry);
      toast.show({
        key,
        title: doneTitle(marked.length),
        timeout: MARK_DONE_TOAST_MS,
        action: back && entry
          // The store says a failure in the error line, so nothing is lost here.
          ? { label: back, onClick: () => void undoEntry(entry).catch(() => {}) }
          : undefined,
      });
    },
    [markThreadsDone, moveThreadsBack, toast, record, undoEntry],
  );

  return { enabled, markDone };
}
