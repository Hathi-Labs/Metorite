"use client";

import { useEffect } from "react";
import { useToast } from "@/components/ui/Toast";
import { useTaskStore } from "../lib/taskStore";

/**
 * The bridge between the store's `syncFailure` and THE confirmation channel
 * (`useToast`, D-PM-15). The store cannot call a hook, and the writes that
 * fail are fired from it after the optimistic row has already moved — so the
 * store re-fetches, records what went wrong, and this component says it.
 *
 * WS-39 S6a repair. Before this, a refused organize, delegate or bulk
 * completion vanished into `sync(...).catch(() => {})` and the member watched
 * an item leave the inbox that the server had kept exactly where it was.
 */
export function SyncFailureToast() {
  const failure = useTaskStore((s) => s.syncFailure);
  const clear = useTaskStore((s) => s.clearSyncFailure);
  const toast = useToast();

  useEffect(() => {
    if (!failure) return;
    toast.show({
      key: `tasks-sync:${failure.at}`,
      variant: "error",
      title: failure.message,
    });
    clear();
  }, [failure, toast, clear]);

  return null;
}
