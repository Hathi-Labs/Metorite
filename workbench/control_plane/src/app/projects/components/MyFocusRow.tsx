"use client";

/**
 * Projects · MY focus on this task, in the task panel. Private to me.
 *
 * ## What is left here after D78 (owner decision 2026-09-24)
 *
 * This row held the member's whole matrix: Important, Leveraged and Deep
 * work. D78 made Important and Leveraged ONE shared answer on the task, and
 * the task body draws them under "Priority" in both apps. Drawing them here
 * as well would put two priority controls on one panel, which is the
 * duplication the owner asked to remove.
 *
 * Deep work stays, because it is how I do the work, not what the work is.
 * It lives on my overlay (`pm_task_personal.deep_work`), and only I see it.
 *
 * ⚠️ **The same button as My Tasks, not a lookalike.** `DeepWorkToggle` is
 * imported from the Tasks app (CLAUDE.md §4).
 */
import Icon from "@/components/Icon";
import { DeepWorkToggle } from "@/app/tasks/components/PriorityControls";
import type { MyOverlay } from "@/app/tasks/lib/lens";

export function MyFocusRow({
  overlay,
  onChange,
}: {
  overlay: MyOverlay;
  onChange: (patch: { deepWork?: boolean }) => void;
}) {
  return (
    <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-dashed border-border px-2.5 py-2">
      <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
        <Icon name="Lock" className="h-3 w-3" aria-hidden />
        Your focus
        <span className="font-normal">· only you see this</span>
      </span>
      <DeepWorkToggle
        active={Boolean(overlay.deepWork)}
        onChange={(next) => onChange({ deepWork: next })}
        size="sm"
      />
    </div>
  );
}
