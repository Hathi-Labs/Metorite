"use client";

import { useEffect, useState } from "react";

import { fetchMyTaskLanes } from "./api";
import type { LensLane } from "./lens";
import { DEMO_LANES, useTaskStore } from "./taskStore";

/**
 * The statuses of one task's status set, read when `enabled` turns true —
 * the status menu opening (D79).
 *
 * Through `my/tasks/{id}/lanes`, the door a member reached by assignment
 * alone can pass (S6e). The demo backend has no statuses, so it answers the
 * three stand-ins `DEMO_LANES` keys by stage. `null` while the read is in
 * flight, and after it fails: the failure goes to the store's toast.
 */
export function useTaskLanes(taskId: string, enabled: boolean): readonly LensLane[] | null {
  const live = useTaskStore((s) => s.backend === "live");
  const report = useTaskStore((s) => s.reportSyncFailure);
  const [lanes, setLanes] = useState<readonly LensLane[] | null>(null);

  useEffect(() => {
    if (!enabled) return;
    if (!live) {
      setLanes(DEMO_LANES);
      return;
    }
    let current = true;
    fetchMyTaskLanes(taskId).then(
      (rows) => {
        if (current) setLanes(rows);
      },
      (err: unknown) => {
        if (current) report(err instanceof Error ? err.message : String(err));
      },
    );
    return () => {
      current = false;
    };
  }, [taskId, enabled, live, report]);

  return lanes;
}
