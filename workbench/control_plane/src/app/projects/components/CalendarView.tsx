"use client";

/**
 * Projects · the calendar (WS-27q; week layout, honest overflow — WS-27ac).
 *
 * The third view, after list and board. All of the arithmetic — which days the
 * grid covers, which cells a task occupies, how many of them fit, what a drop
 * should write and when it must be refused — is in `lib/calendar.ts` and tested
 * there; this file only draws it and wires the gestures, because a calendar bug
 * is a task on the wrong Tuesday and that is not something a component test
 * would catch either.
 *
 * **Month and week are one grid, not two views.** Both come out of
 * `calendarGrid`, both render the same seven columns, and the only things that
 * differ are how many rows there are, how tall a cell is and how many chips it
 * shows before folding. A second component for the week is how the two layouts
 * come to disagree about which day starts a week.
 *
 * **Three honest admissions on the surface, all deliberate.** A calendar that
 * silently omits tasks is worse than one that looks incomplete: `truncated`
 * says when the window hit its cap, `undated` says how many tasks have no dates
 * at all and therefore cannot be here, and a full day cell says `+N more` with
 * the TRUE remainder rather than quietly drawing its first three.
 */

import { TaskMeta } from "@/components/TaskMeta";
import Button from "@/components/ui/Button";
import { useMemo, useState } from "react";

import type { TagRow,
  TaskTypeRow, TaskRow } from "../lib/api";
import { projectsApi } from "../lib/api";
import {
  type CalendarGrid,
  type CalendarLayout,
  DAY_LIMITS,
  dayDropRefusal,
  dayFill,
  gridLabel,
  isPadding,
  placeTasks,
  rescheduleTo,
} from "../lib/calendar";
import { tagColours, typeFacts, visibleChips } from "../lib/card";
import { quickAddPrefill } from "../lib/quickAdd";
import { QuickAdd } from "./QuickAdd";
import { useFlash } from "./useFlash";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** How tall an empty cell stands. A week has one row to fill, so its cells are
 *  the whole canvas; a month has four to six and must stay on one screen. */
const CELL_HEIGHT: Record<CalendarLayout, string> = {
  month: "min-h-24",
  week: "min-h-64",
};

/** What "previous"/"next" step, per layout — the button says which. */
const PERIOD: Record<CalendarLayout, string> = { month: "month", week: "week" };

const LAYOUTS: CalendarLayout[] = ["month", "week"];

interface Props {
  grid: CalendarGrid;
  tasks: TaskRow[];
  /** How many matching tasks have no dates and so cannot be drawn. */
  undated: number;
  /** The window hit the server's cap; some tasks are missing. */
  truncated: boolean;
  today?: string;
  /** WS-27y — where a day's quick-added task is created (the selected node). */
  /** WS-27x — the view's shown fields; chips a hidden field earned are not drawn. */
  shownFields: readonly string[];
  /** S6 — the project's tag registry, so a tag chip is the colour its owner
   *  chose here too. One tag, one colour, on every surface of the project. */
  tags?: readonly TagRow[];
  /** WS-27bh — the root's task types, so a card can name what it IS. */
  taskTypes?: readonly TaskTypeRow[];
  projectId: string;
  onCreated: (task: TaskRow) => void;
  onSelect: (task: TaskRow) => void;
  onMove: (task: TaskRow, patch: Record<string, string | null>) => void;
  /** Step one PERIOD — a month in month layout, a week in week layout. */
  onStep: (steps: number) => void;
  onToday: () => void;
  onLayout: (layout: CalendarLayout) => void;
  /** WS-27y — a refused drop says why, rather than doing nothing. */
  onRefuse: (reason: string) => void;
}

export function CalendarView({
  grid,
  tasks,
  undated,
  truncated,
  today,
  projectId,
  shownFields,
  tags,
  taskTypes,
  onCreated,
  onSelect,
  onMove,
  onStep,
  onToday,
  onLayout,
  onRefuse,
}: Props) {
  const byDay = placeTasks(tasks, grid);
  const { flash, attach } = useFlash();
  // Once per registry, not once per card.
  const tagHues = useMemo(() => tagColours(tags ?? []), [tags]);
  const typeHues = useMemo(() => typeFacts(taskTypes ?? []), [taskTypes]);

  /** Days the viewer has unfolded. Per DAY rather than per cell index, so
   *  stepping to the next period does not carry an expansion onto whatever
   *  happens to land in the same slot. */
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  /** The card under the cursor mid-drag, so a cell can refuse it BEFORE the
   *  drop — the same order `TaskBoard` uses. */
  const [dragging, setDragging] = useState<TaskRow | null>(null);
  const [over, setOver] = useState<string | null>(null);

  const limit = DAY_LIMITS[grid.layout];

  /** WS-27y — a title typed into a day creates a task DUE that day, through
   *  the same axis→payload mapping every other quick-add uses. */
  async function quickAdd(title: string, day: string) {
    const plan = quickAddPrefill("day", day);
    const created = await projectsApi.createTask({
      project_id: projectId,
      title,
      ...plan.create,
    });
    flash(created.id);
    onCreated(created);
  }

  function toggleDay(day: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (!next.delete(day)) next.add(day);
      return next;
    });
  }

  function applyDrop(day: string, id: string) {
    setDragging(null);
    setOver(null);
    const task = tasks.find((t) => t.id === id);
    const refusal = dayDropRefusal(task);
    if (refusal || !task) {
      onRefuse(refusal ?? "That task is not on this calendar.");
      return;
    }
    // `rescheduleTo` returns null for a drop that changes nothing, so a task
    // dropped back on its own day writes nothing rather than posting an
    // activity saying it moved to where it was. That is a NO-OP, not a
    // refusal: nothing was denied, so nothing is announced.
    const patch = rescheduleTo(task, day);
    if (!patch) return;
    // The landing flash finds the card after the window reloads and re-keys it
    // under its new day (WS-27y).
    flash(task.id);
    onMove(task, patch as Record<string, string | null>);
  }

  return (
    <div className="flex flex-col gap-2 p-3">
      <header className="flex flex-wrap items-center gap-2">
        <Button
          variant="secondary"
          size="icon-sm"
          icon="ChevronLeft"
          aria-label={`Previous ${PERIOD[grid.layout]}`}
          onClick={() => onStep(-1)}
        />
        <Button
          variant="secondary"
          size="icon-sm"
          icon="ChevronRight"
          aria-label={`Next ${PERIOD[grid.layout]}`}
          onClick={() => onStep(1)}
        />
        <Button variant="ghost" size="sm" onClick={onToday}>
          Today
        </Button>
        <h2 className="text-sm font-medium text-foreground">{gridLabel(grid)}</h2>
        <div className="flex items-center gap-1">
          {LAYOUTS.map((option) => (
            <Button
              key={option}
              variant={grid.layout === option ? "primary" : "ghost"}
              size="sm"
              aria-pressed={grid.layout === option}
              className="capitalize"
              onClick={() => onLayout(option)}
            >
              {option}
            </Button>
          ))}
        </div>
        <span className="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
          {undated > 0 ? (
            <span title="These have no start or due date, so no day to sit on.">
              {undated} unscheduled
            </span>
          ) : null}
          {truncated ? (
            <span className="font-medium text-destructive">
              Too many tasks in this window to show them all — narrow the filters.
            </span>
          ) : null}
        </span>
      </header>

      <div className="grid grid-cols-7 gap-px overflow-hidden rounded-lg border border-border bg-border">
        {WEEKDAYS.map((label) => (
          <div
            key={label}
            className="bg-muted px-2 py-1 text-center text-xs font-medium text-muted-foreground"
          >
            {label}
          </div>
        ))}
        {grid.days.map((day) => {
          const padding = isPadding(day, grid);
          const dayTasks = byDay.get(day) ?? [];
          const fill = dayFill(dayTasks.length, limit, expanded.has(day));
          const refusal = dragging ? dayDropRefusal(dragging) : null;
          return (
            <div
              key={day}
              onDragOver={(e) => {
                // preventDefault even when refusing — the browser must keep
                // sending events or the overlay could never show; the refusal
                // is enforced in `applyDrop`, and the cursor says "no" via
                // dropEffect.
                e.preventDefault();
                if (!dragging) return;
                e.dataTransfer.dropEffect = refusal ? "none" : "move";
                setOver((current) => (current === day ? current : day));
              }}
              onDragLeave={(e) => {
                if (e.currentTarget.contains(e.relatedTarget as Node)) return;
                setOver((current) => (current === day ? null : current));
              }}
              onDrop={(e) => {
                e.preventDefault();
                applyDrop(day, e.dataTransfer.getData("text/plain"));
              }}
              className={`relative ${CELL_HEIGHT[grid.layout]} bg-card p-1 ${
                padding ? "opacity-50" : ""
              }`}
            >
              <div className="flex items-center justify-between px-1">
                <span
                  className={`text-xs ${
                    day === today
                      ? "rounded bg-primary px-1.5 font-medium text-primary-foreground"
                      : "text-muted-foreground"
                  }`}
                >
                  {Number(day.slice(8))}
                </span>
              </div>
              <ul className="mt-1 space-y-1">
                {dayTasks.slice(0, fill.shown).map((task) => (
                  <li key={`${day}:${task.id}`} ref={attach(task.id)} className="rounded">
                    <button
                      type="button"
                      draggable
                      onDragStart={(e) => {
                        e.dataTransfer.setData("text/plain", task.id);
                        setDragging(task);
                      }}
                      onDragEnd={() => {
                        setDragging(null);
                        setOver(null);
                      }}
                      onClick={() => onSelect(task)}
                      className="w-full rounded border border-border bg-background px-1.5 py-1 text-left hover:border-ring"
                    >
                      <span
                        className={`block truncate text-xs text-foreground ${
                          task.completed_at ? "line-through opacity-60" : ""
                        }`}
                      >
                        {task.title}
                      </span>
                      <TaskMeta
                        chips={visibleChips(
                          task,
                          shownFields,
                          undefined,
                          tagHues,
                          // ⚠️ The fifth argument. Without it this surface
                          // built `typeHues` and passed nothing, so the type
                          // chip drew on the board and the list and NEVER on
                          // the calendar. `visibleChips`' last parameter is
                          // optional, so tsc cannot catch the omission, and
                          // `noUnusedLocals` is off so the dead memo was
                          // silent too. Found by review, 2026-09-19.
                          typeHues
                        )}
                      />
                    </button>
                  </li>
                ))}
              </ul>
              {/* WS-27ac — the count is the real remainder, and it EXPANDS.
                  Drawing three and stopping makes a day with eleven tasks look
                  exactly like a day with three. */}
              {fill.hidden > 0 || expanded.has(day) ? (
                <Button
                  variant="text"
                  size="none"
                  layout="flex w-full items-center"
                  className="mt-1 px-1.5 py-0.5 text-left text-[11px]"
                  aria-expanded={expanded.has(day)}
                  onClick={() => toggleDay(day)}
                >
                  {fill.hidden > 0 ? `+${fill.hidden} more` : "Show less"}
                </Button>
              ) : null}
              {/* WS-27y — a title typed here is due THIS day. */}
              <QuickAdd
                compact
                label={`Add a task due ${day}`}
                onAdd={(title) => quickAdd(title, day)}
                className="mt-1"
              />
              {over === day && refusal ? (
                <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-background/85 p-2 text-center text-[11px] font-medium text-destructive">
                  {refusal}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
