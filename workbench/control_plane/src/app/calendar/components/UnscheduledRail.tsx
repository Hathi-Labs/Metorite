"use client";

import Icon, { themedIcon } from "@/components/Icon";
import { useState } from "react";
import { GtdItem } from "@/app/tasks/lib/types";
import { durationLabel } from "@/app/tasks/lib/utils";
import { priorityRank } from "@/app/tasks/lib/priority";
import {
  DEFAULT_BLOCK_MINS,
} from "@/app/tasks/lib/scheduling";
import {
  DRAG_TYPE,
} from "./shared";
import { ContextMenu } from "@/components/ContextMenu";


// ── Unscheduled rail ─────────────────────────────────────────────────────────
export function UnscheduledRail({
  tasks,
  capacityMins,
  capacityTarget,
  leveragedMins,
  oneThingId,
  onToggleOneThing,
  dueSoon,
  urgentWindowHours,
  doneStats,
  onPlan,
  onOpen,
  onTimebox,
  onReschedulePopup,
  onDelete,
}: {
  tasks: GtdItem[];
  capacityMins: number;
  capacityTarget: number;
  /** of the booked minutes, how many sit on leveraged/important work (80/20). */
  leveragedMins: number;
  oneThingId: string | null;
  onToggleOneThing: (id: string) => void;
  /** approaching deadlines — rendered as a badge + sort boost ON the normal
   *  cards (one list, no duplication; every card drags + timeboxes alike). */
  dueSoon: { item: GtdItem; days: number }[];
  urgentWindowHours: number;
  doneStats: { count: number; mins: number };
  onPlan: () => void;
  onOpen: (id: string) => void;
  /** context menu: timebox into the first free slot (no dragging needed). */
  onTimebox: (t: GtdItem) => void;
  /** context menu: exact date/time via the global Schedule popup. */
  onReschedulePopup: (id: string) => void;
  /** context menu: confirm-first delete flow. */
  onDelete: (id: string) => void;
}) {
  const over = capacityMins > capacityTarget;
  // Right-click menu on a card (the rail is desktop-only, so no long-press).
  const [ctx, setCtx] = useState<{ x: number; y: number; item: GtdItem } | null>(
    null,
  );
  const leveragePct =
    capacityMins > 0 ? Math.round((leveragedMins / capacityMins) * 100) : 0;
  // ONE list: ★ One Thing first, then approaching deadlines (soonest first),
  // then the rest by the priority matrix. Deadline pressure is a property of
  // a card, not a separate pile.
  const dueDays = new Map(dueSoon.map((d) => [d.item.id, d.days]));
  const ordered = [...tasks].sort((a, b) => {
    const oa = a.id === oneThingId ? 0 : 1;
    const ob = b.id === oneThingId ? 0 : 1;
    if (oa !== ob) return oa - ob;
    const da = dueDays.get(a.id) ?? Infinity;
    const db = dueDays.get(b.id) ?? Infinity;
    if (da !== db) return da - db;
    return (
      priorityRank(a, urgentWindowHours) - priorityRank(b, urgentWindowHours)
    );
  });
  return (
    <aside className="hidden w-64 shrink-0 flex-col border-l border-border bg-card md:flex">
      <div className="border-b border-border px-3 py-2">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
          <Icon name="CalendarPlus" className="h-3.5 w-3.5 text-primary" />
          Unscheduled
        </div>
        <p className="mt-0.5 text-[10px] text-muted-foreground">
          Drag a card onto the grid to timebox it · click to open.
        </p>
        <div
          className={[
            "mt-1 text-[10px]",
            over ? "font-medium text-warning" : "text-muted-foreground",
          ].join(" ")}
        >
          {Math.round((capacityMins / 60) * 10) / 10}h /{" "}
          {Math.round((capacityTarget / 60) * 10) / 10}h booked
          {over ? " — over capacity" : ""}
        </div>
        {/* leverage meter (80/20): how much of the booked day is the 20% */}
        {capacityMins > 0 && (
          <div
            className="mt-0.5 text-[10px] text-amber-500"
            title="Share of booked focus-time on leveraged/important work (the 80/20 scoreboard)"
          >
            ★ {Math.round((leveragedMins / 60) * 10) / 10}h leveraged ·{" "}
            {leveragePct}%
            <span className="mt-0.5 block h-1 overflow-hidden rounded-full bg-secondary">
              <span
                className="block h-full rounded-full bg-amber-400/80"
                style={{ width: `${Math.min(100, leveragePct)}%` }}
              />
            </span>
          </div>
        )}
        {doneStats.count > 0 && (
          <div className="mt-0.5 inline-flex items-center gap-1 text-[10px] font-medium text-success">
            <Icon name="Check" className="h-3 w-3" />
            {doneStats.count} done ·{" "}
            {Math.round((doneStats.mins / 60) * 10) / 10}h
          </div>
        )}
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {tasks.length === 0 ? (
          <p className="px-1 py-4 text-center text-[11px] text-muted-foreground">
            Nothing to schedule — inbox zero on next actions. 🎉
          </p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {ordered.map((t) => (
              <div
                key={t.id}
                draggable
                // Same grammar as a calendar block: CLICK opens the task card,
                // CLICK-AND-HOLD drags it onto the grid at the slot you want,
                // RIGHT-CLICK for everything else (incl. first-free-slot).
                onClick={() => onOpen(t.id)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  setCtx({ x: e.clientX, y: e.clientY, item: t });
                }}
                onDragStart={(e) => {
                  e.dataTransfer.setData(
                    DRAG_TYPE,
                    JSON.stringify({
                      id: t.id,
                      durationMins: t.timeEstimateMins ?? DEFAULT_BLOCK_MINS,
                      grabOffsetMins: 0,
                    }),
                  );
                  e.dataTransfer.effectAllowed = "move";
                }}
                className={[
                  "group cursor-grab rounded-md border p-2 active:cursor-grabbing",
                  t.id === oneThingId
                    ? "border-amber-400/70 bg-amber-500/10"
                    : t.leveraged
                      ? "border-amber-500/40 bg-background/60 hover:border-amber-400/60"
                      : dueDays.has(t.id)
                        ? "border-warning/40 bg-warning/5 hover:border-warning/70"
                        : "border-border bg-background/60 hover:border-primary/40",
                ].join(" ")}
              >
                <div className="flex items-start gap-1">
                  <span className="min-w-0 flex-1 truncate text-left text-xs text-foreground">
                    {t.id === oneThingId && (
                      <span className="text-amber-400">★ </span>
                    )}
                    {t.title}
                  </span>
                  <button
                    type="button"
                    aria-label={
                      t.id === oneThingId
                        ? "Unset the One Thing"
                        : "Make this the One Thing"
                    }
                    title={
                      t.id === oneThingId
                        ? "Your One Thing today. Click to unset."
                        : "If only one thing gets done today… make it this."
                    }
                    onClick={(e) => {
                      e.stopPropagation();
                      onToggleOneThing(t.id);
                    }}
                    className={[
                      "tech-transition shrink-0 rounded p-0.5",
                      t.id === oneThingId
                        ? "text-amber-400"
                        : "text-muted-foreground/60 opacity-0 hover:text-amber-400 group-hover:opacity-100",
                    ].join(" ")}
                  >
                    <Icon name="Star"
                      className="h-3 w-3"
                      fill={t.id === oneThingId ? "currentColor" : "none"}
                    />
                  </button>
                </div>
                <div className="mt-1 flex items-center gap-2 text-[10px] text-muted-foreground">
                  {t.timeEstimateMins ? (
                    <span className="inline-flex items-center gap-0.5">
                      <Icon name="Clock" className="h-3 w-3" />
                      {durationLabel(t.timeEstimateMins)}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-0.5">
                      <Icon name="Clock" className="h-3 w-3" />
                      {DEFAULT_BLOCK_MINS}m
                    </span>
                  )}
                  {t.energy && (
                    <span className="inline-flex items-center gap-0.5 capitalize">
                      <Icon name="Zap" className="h-3 w-3" />
                      {t.energy}
                    </span>
                  )}
                  {dueDays.has(t.id) && (
                    <span
                      className={[
                        "inline-flex items-center gap-0.5 font-medium",
                        (dueDays.get(t.id) as number) <= 1
                          ? "text-destructive"
                          : "text-warning",
                      ].join(" ")}
                    >
                      <Icon name="AlertTriangle" className="h-3 w-3" />
                      {(dueDays.get(t.id) as number) <= 0
                        ? "due today"
                        : dueDays.get(t.id) === 1
                          ? "due tomorrow"
                          : `due in ${dueDays.get(t.id)}d`}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      {/* AI plan-my-day — the assistant timeboxes your Next Actions. */}
      <div className="border-t border-border p-2">
        <button
          type="button"
          onClick={onPlan}
          title="Rebuild your day: reshuffle what's scheduled + fill from your list, around priority, energy + deadlines"
          className="tech-transition flex w-full items-center justify-center gap-1.5 rounded-md bg-primary/10 px-2 py-2 text-[11px] font-medium text-primary hover:bg-primary/20"
        >
          <Icon name="Sparkles" className="h-3.5 w-3.5" />
          Rebuild my day
        </button>
      </div>

      {/* card context menu */}
      {ctx && (
        <ContextMenu
          x={ctx.x}
          y={ctx.y}
          items={[
            {
              kind: "item",
              label: "Open task",
              icon: themedIcon("ExternalLink"),
              onSelect: () => onOpen(ctx.item.id),
            },
            {
              kind: "item",
              label: "Timebox → first free slot",
              icon: themedIcon("CalendarPlus"),
              onSelect: () => onTimebox(ctx.item),
            },
            { kind: "sep" },
            {
              kind: "item",
              label:
                ctx.item.id === oneThingId
                  ? "Unset One Thing"
                  : "Make it the One Thing",
              icon: themedIcon("Star"),
              checked: ctx.item.id === oneThingId,
              onSelect: () => onToggleOneThing(ctx.item.id),
            },
            { kind: "sep" },
            {
              kind: "item",
              label: "Schedule…",
              icon: themedIcon("CalendarClock"),
              onSelect: () => onReschedulePopup(ctx.item.id),
            },
            {
              kind: "item",
              label: "Delete task…",
              icon: themedIcon("Trash2"),
              danger: true,
              onSelect: () => onDelete(ctx.item.id),
            },
          ]}
          onClose={() => setCtx(null)}
        />
      )}
    </aside>
  );
}
