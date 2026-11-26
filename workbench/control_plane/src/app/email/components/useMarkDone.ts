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
      const entry = back
        ? markDoneEntry(marked, { markDone: markThreadsDone, moveBack: moveThreadsBack })
        : null;
      if (entry) record(entry);
      toast.show({
        // A key of its own for each act that can move back, so the button of
        // an earlier act stays while the member works down the list.
        key: back ? `email-mark-done:${marked.map(threadKey).join()}` : undefined,
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
