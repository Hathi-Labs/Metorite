"use client";

/**
 * Projects · My tasks: what the Today chip waits for, or lost, from the read
 * of the Today set (WS-MDC CC-2 fix round 2).
 *
 * With `TODAY_RULE_ORGS` on, the chip counts the set of the one Today rule,
 * and the set can hold tasks that the mine list lacks. Only
 * `GET /projects/my/today?with_tasks=true` brings those rows. So:
 *
 * - while the read is on its way, one skeleton row holds the place of each
 *   missing task, at the height of a row of the list, so no row jumps in;
 * - when the read fails, one line says what happened and what to do, with a
 *   Retry that reads the set again. Not `text-destructive`: the tasks that
 *   are assigned to the member still show, so nothing is lost.
 *
 * The rule of when each state shows is `todaySetGap` in `lib/myTasks.ts`.
 * Fence: `myTasks.test.ts`.
 */

import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";

import { TODAY_SET_FAILED, type TodaySetGap as Gap } from "../lib/myTasks";

/** The widths step, so the rows read as text that is on its way. */
const TITLE_WIDTHS = ["w-2/5", "w-1/3", "w-1/2", "w-2/5"];

interface Props {
  gap: Gap;
  /** Reads the set again. */
  onRetry: () => void;
}

export function TodaySetGap({ gap, onRetry }: Props) {
  if (gap.failed) {
    return (
      <div
        role="status"
        data-today-set-failed=""
        className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/40 px-3 py-1.5 text-xs"
      >
        <span className="text-muted-foreground">{TODAY_SET_FAILED}</span>
        <Button variant="secondary" size="sm" icon="RotateCcw" onClick={onRetry}>
          Retry
        </Button>
      </div>
    );
  }
  if (gap.pending <= 0) return null;
  return (
    <div role="status" aria-busy="true" aria-label="Loading more of today's tasks" data-today-set-pending="">
      {Array.from({ length: gap.pending }, (_, i) => (
        // The height of a row of `TaskList`: `py-2` around a title line
        // (`text-sm`) and a project line (`text-xs`), and the border.
        <div key={i} data-today-set-placeholder="" className="border-b border-border px-3 py-2">
          <div className="flex h-5 items-center">
            <Skeleton className={`h-3.5 ${TITLE_WIDTHS[i % TITLE_WIDTHS.length]}`} />
          </div>
          <div className="flex h-4 items-center">
            <Skeleton className="h-2.5 w-1/4" />
          </div>
        </div>
      ))}
    </div>
  );
}
