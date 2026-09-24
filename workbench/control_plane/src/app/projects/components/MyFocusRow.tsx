"use client";

/**
 * Projects · MY focus on this task, in the task panel. Private to me.
 *
 * Owner decision, 2026-09-23. One task sits in Projects and in My Tasks
 * (D53), and the two apps prioritised it in unrelated languages: the board's
 * shared Priority here, and the member's matrix — important × urgent ×
 * leveraged — over there. Nothing on either screen said they were about the
 * same task. This row puts the member's side on the board's card.
 *
 * ## Who owns what
 *
 *   • The ORG owns the priority and the due date. They are on the task row,
 *     and this reads them from the panel's live row, not from a copy.
 *   • The MEMBER owns important, leveraged and deep work. They are on the
 *     member's overlay (`pm_task_personal`), and only the member sees them.
 *   • URGENT is nobody's to set. It is derived from the shared due date, so
 *     the org already moves half of the matrix, through a field it owns.
 *
 * ⚠️ **Same controls as My Tasks, not a lookalike.** `WeightToggles` and
 * `PriorityBadge` are imported from the Tasks app, so a flag reads and
 * behaves identically in both. A second drawing of the matrix here would be a
 * second vocabulary for one fact (CLAUDE.md §4).
 *
 * The urgency window is the default 48 hours here. The member's own window
 * lives in the Tasks store, which this page does not load.
 */
import Icon from "@/components/Icon";
import {
  PriorityBadge,
  WeightToggles,
} from "@/app/tasks/components/PriorityControls";
import type { MyOverlay } from "@/app/tasks/lib/lens";

export function MyFocusRow({
  overlay,
  dueAt,
  orgPriority,
  onChange,
}: {
  overlay: MyOverlay;
  /** The task's SHARED due date, off the live row. Drives derived urgency. */
  dueAt: string | null | undefined;
  /** The task's SHARED priority, off the live row. Seeds `important`. */
  orgPriority: number | null | undefined;
  onChange: (patch: {
    important?: boolean;
    leveraged?: boolean;
    deepWork?: boolean;
  }) => void;
}) {
  const item = {
    important: overlay.important,
    leveraged: overlay.leveraged,
    deepWork: overlay.deepWork,
    dueAt: dueAt ?? undefined,
    orgPriority: orgPriority ?? undefined,
  };
  return (
    <div className="mt-2 flex flex-col gap-1.5 rounded-lg border border-dashed border-border px-2.5 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
          <Icon name="Lock" className="h-3 w-3" aria-hidden />
          Your focus
          <span className="font-normal">· only you see this</span>
        </span>
        <PriorityBadge item={item} />
      </div>
      <WeightToggles item={item} onChange={onChange} size="sm" />
    </div>
  );
}
