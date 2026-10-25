"use client";

/**
 * Projects · the overlay strip of S6e, in the ONE task panel (WS-64 OP-2b,
 * `one_projects_app.md` §4.3).
 *
 * It holds the member's own facts on a task: disposition, context, energy and
 * defer. The estimate is the task's own fact (D77), so the shared body draws
 * it and this strip does not. The rows are `PlanningRows`, imported from the
 * Tasks detail that draws them in My Tasks. Nothing is copied.
 *
 * Every write goes through the one task store, so the My work list behind the
 * panel changes at once, with no reload and no second fetch.
 */
import { themedIcon } from "@/components/Icon";
import {
  ChipMenu,
  MetaEdit,
  PlanningRows,
  SectionLabel,
} from "@/app/tasks/components/ItemDetail";
import { useTaskStore } from "@/app/tasks/lib/taskStore";
import type { Disposition } from "@/app/tasks/lib/types";

/** The dispositions a member sets by hand on an open task. */
export const STRIP_DISPOSITIONS: readonly Disposition[] = ["NEXT", "WAITING", "SOMEDAY"];

export function MyWorkStrip({ taskId }: { taskId: string }) {
  const item = useTaskStore((s) => s.items.find((i) => i.id === taskId));
  const quickDispose = useTaskStore((s) => s.quickDispose);
  if (!item) return null;
  return (
    <section
      data-testid="my-work-strip"
      aria-label="My planning"
      className="border-b border-border px-3 py-3"
    >
      <SectionLabel icon={themedIcon("Tag")}>My planning</SectionLabel>
      <div className="grid grid-cols-1 gap-y-1">
        <MetaEdit
          label="Disposition"
          icon={themedIcon("CircleDot")}
          display={<span className="capitalize">{item.disposition.toLowerCase()}</span>}
        >
          {(close) => (
            <ChipMenu
              options={[...STRIP_DISPOSITIONS]}
              active={item.disposition}
              format={(o) => o.charAt(0) + o.slice(1).toLowerCase()}
              onPick={(v) => {
                if (v) quickDispose(item.id, v as Disposition);
                close();
              }}
            />
          )}
        </MetaEdit>
        <PlanningRows item={item} lens />
      </div>
    </section>
  );
}
