"use client";

import { useMemo } from "react";

import { type RemovalScope, canPurge, removalLabel, removalLabelFor } from "./removal";
import { useTaskStore } from "./taskStore";
import type { MyTask } from "./types";

/**
 * The delete gesture's rule and label, for a component (S6g, P0).
 * `removal.ts` holds the rule. This only reads my root and my Areas.
 */
export function useRemoval() {
  const personalRootId = useTaskStore((s) => s.personalRootId);
  const areas = useTaskStore((s) => s.areas);
  const scope: RemovalScope = useMemo(
    () => ({ personalRootId, areaIds: areas.map((a) => a.id) }),
    [personalRootId, areas],
  );
  return {
    scope,
    canPurge: (item: Pick<MyTask, "projectId">) => canPurge(item, scope),
    label: (item: Pick<MyTask, "projectId">) => removalLabel(item, scope),
    labelFor: (items: readonly Pick<MyTask, "projectId">[]) => removalLabelFor(items, scope),
  };
}
