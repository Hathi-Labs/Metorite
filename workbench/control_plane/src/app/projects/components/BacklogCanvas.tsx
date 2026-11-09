"use client";

/**
 * Projects · the Backlog canvas (WS-39 SP-3a, `project_management_app.md`
 * §13.6 row SP-3a, D195).
 *
 * Reading this as: a canvas of the tab row, under the project's `AppTopBar`
 * title and `ViewTabs`, with no title of its own. It draws the planned sprints
 * at the top and the backlog below them, as two groups of task cards. It uses
 * `TaskCardShell` (the board's draggable card), the board's drop ring, `useFlash`
 * for the arrival tint, `SheetButton` for "Move to sprint" and "Plan a sprint",
 * and the one undo stack. It matches the Board canvas beside it.
 *
 * The interaction: the member drags a task from the backlog onto a planned
 * sprint, the card lands there with a tint, and a toast offers Undo, which puts
 * it back. Or the member ticks many tasks and picks "Move to sprint". On a
 * phone nothing drags: "Move to sprint" opens a sheet.
 *
 * Every rule is in `lib/backlogCanvas.ts`. This file draws and wires.
 * Rules of hooks: every hook is above the first return.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useUndo } from "@/components/UndoProvider";
import { StatusChip } from "@/components/StatusChip";
import { TaskCardShell, TaskCardTitle } from "@/components/TaskCardShell";
import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Input } from "@/components/ui/Input";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { categoryLabel, closesTask } from "@/lib/statusCategory";
import { clickSelect, type SelectionState } from "@/lib/selection";

import { accentForStatus } from "../lib/accent";
import {
  type CycleRow,
  type CycleTask,
  type ProjectRow,
  type StatusRow,
  type TaskRow,
  projectsApi,
} from "../lib/api";
import {
  BACKLOG,
  type MoveIo,
  type MoveResult,
  type Place,
  applyResults,
  backlogQuery,
  countWords,
  cycleDates,
  cyclePlace,
  moveLabel,
  moveMany,
  moveSurface,
  moveTargets,
  moveTitle,
  placeKey,
  planDefaults,
  planProblem,
  plannedCycles,
  probeSprints,
  recordMove,
  refusalSummary,
  runningCycle,
  samePlace,
} from "../lib/backlogCanvas";
import { taskRef } from "../lib/card";
import { MoreTasksBar } from "./MoreTasksBar";
import { SheetButton } from "./SheetButton";
import { useFlash } from "./useFlash";

/** One task as the canvas draws it, from the backlog read or a sprint read. */
interface CanvasTask {
  id: string;
  number: number | null;
  title: string;
  statusId: string | null;
  statusName: string | null;
  category: string | null;
}

interface CanvasData {
  cycles: CycleRow[];
  /** Each planned sprint's visible tasks, by sprint id. */
  members: Record<string, CanvasTask[]>;
  /** Each planned sprint's tasks that the reader cannot see (§13.7). */
  hidden: Record<string, number>;
  backlog: CanvasTask[];
  total: number;
}

function fromTaskRow(row: TaskRow, statuses: readonly StatusRow[]): CanvasTask {
  const status = statuses.find((s) => s.id === row.status_id);
  const category = (row as { status_category?: string | null }).status_category ?? status?.category ?? null;
  return {
    id: row.id,
    number: row.task_number ?? null,
    title: row.title,
    statusId: row.status_id ?? null,
    statusName: status?.name ?? null,
    category,
  };
}

function fromCycleTask(row: CycleTask): CanvasTask {
  return {
    id: row.id,
    number: row.task_number,
    title: row.title,
    statusId: row.status_id,
    statusName: row.status_name,
    category: row.status_category,
  };
}

const io: MoveIo = {
  add: (cycleId, taskId) => projectsApi.addCycleTask(cycleId, taskId),
  remove: (cycleId, taskId) => projectsApi.removeCycleTask(cycleId, taskId),
};

export interface BacklogCanvasProps {
  node: ProjectRow;
  /** True on a phone: no drag, and "Move to sprint" opens a sheet. */
  phone: boolean;
  /** The lanes of the node, for the name and the hue of each status. */
  statuses: readonly StatusRow[];
  onOpenTask: (taskId: string) => void;
}

export function BacklogCanvas({ node, phone, statuses, onOpenTask }: BacklogCanvasProps) {
  const toast = useToast();
  const undoApi = useUndo();
  const { flash, attach } = useFlash();
  const surface = moveSurface(phone);

  const [data, setData] = useState<CanvasData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [off, setOff] = useState(false);
  const [pages, setPages] = useState(1);
  const [loadingMore, setLoadingMore] = useState(false);
  // Where a task stands while its move runs and until the next read lands.
  const [overlay, setOverlay] = useState<Map<string, Place>>(() => new Map());
  const inflight = useRef(0);
  const [selection, setSelection] = useState<SelectionState>({ selected: new Set(), anchor: null });
  const [dragging, setDragging] = useState<{ id: string; from: Place } | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const statusesRef = useRef(statuses);
  useEffect(() => {
    statusesRef.current = statuses;
  }, [statuses]);

  const load = useCallback(
    async (pageCount: number) => {
      try {
        const probe = await probeSprints(() => projectsApi.cycles(node.id));
        if (!probe.on) {
          setOff(true);
          return;
        }
        const planned = plannedCycles(probe.cycles);
        const [details, backlogPages] = await Promise.all([
          Promise.all(planned.map((c) => projectsApi.cycle(c.id))),
          Promise.all(
            Array.from({ length: pageCount }, (_, i) => projectsApi.tasks(backlogQuery(node.id, i + 1))),
          ),
        ]);
        const members: Record<string, CanvasTask[]> = {};
        const hidden: Record<string, number> = {};
        for (const detail of details) {
          members[detail.cycle.id] = detail.tasks.map(fromCycleTask);
          hidden[detail.cycle.id] = detail.hidden ?? 0;
        }
        const rows = backlogPages.flatMap((page) => page.rows);
        setData({
          cycles: probe.cycles,
          members,
          hidden,
          backlog: rows.map((row) => fromTaskRow(row, statusesRef.current)),
          total: backlogPages[0]?.total ?? rows.length,
        });
        setError(null);
        setOff(false);
        // The read holds every move that finished. Clear the overlay only
        // when no move runs, or a task in flight would jump back.
        if (inflight.current === 0) setOverlay(new Map());
      } catch (err) {
        setError((err as Error).message || "Could not read the backlog. Try again.");
      }
    },
    [node.id],
  );

  useEffect(() => {
    setData(null);
    setPages(1);
    setOverlay(new Map());
    setSelection({ selected: new Set(), anchor: null });
    void load(1);
  }, [load]);

  // Every task on the canvas, with the place the server gave it.
  const base = useMemo(() => {
    const tasks = new Map<string, CanvasTask>();
    const places = new Map<string, Place>();
    if (data) {
      for (const [cycleId, rows] of Object.entries(data.members)) {
        for (const row of rows) {
          tasks.set(row.id, row);
          places.set(row.id, cyclePlace(cycleId));
        }
      }
      for (const row of data.backlog) {
        tasks.set(row.id, row);
        places.set(row.id, BACKLOG);
      }
    }
    return { tasks, places };
  }, [data]);

  const placeOf = useCallback(
    (id: string): Place => overlay.get(id) ?? base.places.get(id) ?? BACKLOG,
    [overlay, base],
  );

  // The tasks of one place, in the order of the read. A task that arrived by a
  // move goes last until the next read sorts it.
  const tasksAt = useCallback(
    (place: Place): CanvasTask[] => {
      const key = placeKey(place);
      const native = [...base.tasks.values()].filter((t) => placeKey(base.places.get(t.id) ?? BACKLOG) === key);
      const stayed = native.filter((t) => samePlace(placeOf(t.id), place));
      const arrived = [...overlay.keys()]
        .filter((id) => samePlace(overlay.get(id) ?? BACKLOG, place) && !samePlace(base.places.get(id) ?? BACKLOG, place))
        .map((id) => base.tasks.get(id))
        .filter((t): t is CanvasTask => Boolean(t));
      return [...stayed, ...arrived];
    },
    [base, overlay, placeOf],
  );

  const planned = useMemo(() => plannedCycles(data?.cycles ?? []), [data]);
  const running = runningCycle(data?.cycles ?? []);
  const cycleName = (id: string) => planned.find((c) => c.id === id)?.name ?? "the sprint";

  const selectedIds = [...selection.selected].filter((id) => base.tasks.has(id));

  async function move(ids: readonly string[], to: Place) {
    const items = ids
      .map((id) => ({ taskId: id, from: placeOf(id) }))
      .filter((item) => !samePlace(item.from, to));
    if (items.length === 0) return;
    setOverlay((prev) => {
      const next = new Map(prev);
      for (const item of items) next.set(item.taskId, to);
      return next;
    });
    inflight.current += 1;
    let results: MoveResult[];
    try {
      results = await moveMany(io, items, to);
    } finally {
      inflight.current -= 1;
    }
    setOverlay((prev) => applyResults(prev, results));
    const okIds = new Set(results.filter((r) => r.ok).map((r) => r.taskId));
    for (const id of okIds) flash(id);
    const moved = items.filter((item) => okIds.has(item.taskId));
    const toName = to.kind === "cycle" ? cycleName(to.id) : null;
    if (moved.length > 0) {
      const entry = recordMove(
        io,
        {
          record: undoApi.record,
          notify: (message) => toast.show({ variant: "error", title: message }),
          after: () => load(pages),
          placed: (done) => {
            setOverlay((prev) => applyResults(prev, done));
            for (const r of done) if (r.ok) flash(r.taskId);
          },
        },
        moved,
        to,
        moveLabel(moved.length, toName),
      );
      toast.show({
        key: "projects:backlog:move",
        variant: "success",
        title: moveTitle(moved.length, toName),
        action: {
          label: "Undo",
          onClick: () =>
            void undoApi
              .undoEntry(entry)
              .catch((e: Error) => toast.show({ variant: "error", title: String(e.message) })),
        },
      });
    }
    const refusal = refusalSummary(results);
    if (refusal) toast.show({ variant: "error", title: refusal });
    setSelection((prev) => ({
      selected: new Set([...prev.selected].filter((id) => !okIds.has(id))),
      anchor: prev.anchor,
    }));
    void load(pages);
  }

  async function loadMore() {
    setLoadingMore(true);
    const next = pages + 1;
    setPages(next);
    await load(next);
    setLoadingMore(false);
  }

  const dropProps = (place: Place) => {
    const key = placeKey(place);
    if (!surface.drag) return {};
    return {
      onDragOver: (event: React.DragEvent) => {
        if (!dragging) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        if (over !== key) setOver(key);
      },
      onDragLeave: (event: React.DragEvent) => {
        if (event.currentTarget.contains(event.relatedTarget as Node)) return;
        setOver((now) => (now === key ? null : now));
      },
      onDrop: (event: React.DragEvent) => {
        event.preventDefault();
        setOver(null);
        const held = dragging;
        setDragging(null);
        if (held && !samePlace(held.from, place)) void move([held.id], place);
      },
    };
  };

  const isOver = (place: Place) => Boolean(dragging) && over === placeKey(place) && !samePlace(dragging!.from, place);

  const orderOnScreen = useMemo(
    () => [...planned.flatMap((c) => tasksAt(cyclePlace(c.id))), ...tasksAt(BACKLOG)].map((t) => t.id),
    [planned, tasksAt],
  );

  const card = (task: CanvasTask) => {
    const picked = selection.selected.has(task.id);
    const accent = accentForStatus(
      statuses.find((s) => s.id === task.statusId) ?? { color: "", category: task.category ?? "" },
    );
    return (
      <li key={task.id} className="list-none">
        <TaskCardShell
          innerRef={attach(task.id)}
          dense
          selected={picked}
          // A sprint may hold a finished task. The backlog holds open work only.
          completed={closesTask(task.category ?? "")}
          // The canvas has no keyboard cursor: Tab walks the cards, and the
          // checkbox and "Move to sprint" are the keyboard path of a move.
          atCursor={false}
          draggable={surface.drag}
          ariaLabel={picked ? `${task.title}, selected` : task.title}
          onActivate={() => onOpenTask(task.id)}
          onDragStart={(event) => {
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", task.id);
            setDragging({ id: task.id, from: placeOf(task.id) });
          }}
          onDragEnd={() => {
            setDragging(null);
            setOver(null);
          }}
        >
          <div className="flex min-w-0 items-center gap-2">
            <span
              className="inline-flex"
              onClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => event.stopPropagation()}
            >
              <Checkbox
                size="sm"
                checked={picked}
                aria-label={`Select ${task.title}`}
                onChange={(event) =>
                  setSelection((prev) =>
                    clickSelect(prev, orderOnScreen, task.id, (event.nativeEvent as MouseEvent).shiftKey ?? false),
                  )
                }
              />
            </span>
            {taskRef({ task_number: task.number }) ? (
              <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
                {taskRef({ task_number: task.number })}
              </span>
            ) : null}
            <TaskCardTitle className="flex-1">{task.title}</TaskCardTitle>
            <StatusChip
              accent={accent}
              label={task.statusName ?? categoryLabel(task.category)}
              className="shrink-0"
            />
          </div>
        </TaskCardShell>
      </li>
    );
  };

  const moveControl = (ids: readonly string[]) => {
    const targets = moveTargets(planned, ids.map(placeOf));
    const first = targets.findIndex((target) => !target.here);
    return (
      <SheetButton label="Move to sprint" icon="ArrowRightLeft" phone={surface.sheet} ariaLabel="Move to sprint">
        {(close) => (
          <div
            className="flex flex-col gap-0.5"
            role="menu"
            aria-label="Move to sprint"
            data-move-targets=""
            // The keyboard path of a move (M6): the menu takes the focus when it
            // opens, and the arrow keys walk its targets.
            onKeyDown={(event) => {
              if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
              event.preventDefault();
              const items = [
                ...event.currentTarget.querySelectorAll<HTMLButtonElement>("[role='menuitem']:not([disabled])"),
              ];
              if (items.length === 0) return;
              const at = items.indexOf(document.activeElement as HTMLButtonElement);
              const step = event.key === "ArrowDown" ? 1 : -1;
              items[(at + step + items.length) % items.length]?.focus();
            }}
          >
            {targets.map((target, index) => (
              <button
                key={placeKey(target.place)}
                type="button"
                role="menuitem"
                autoFocus={index === first}
                disabled={target.here}
                data-move-target={placeKey(target.place)}
                onClick={() => {
                  close();
                  void move(ids, target.place);
                }}
                className="cc-link flex w-full items-center gap-2 rounded px-2 py-2 text-left text-sm text-foreground hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
              >
                <span className="min-w-0 flex-1 truncate">{target.label}</span>
                {target.here ? <span className="text-[11px] text-muted-foreground">Here now</span> : null}
              </button>
            ))}
            {planned.length === 0 ? (
              <p className="px-2 py-1 text-xs text-muted-foreground">Plan a sprint first.</p>
            ) : null}
          </div>
        )}
      </SheetButton>
    );
  };

  if (off) {
    return (
      <p className="p-6 text-sm text-muted-foreground" role="status">
        Sprints are off for this workspace, so the backlog has nothing to plan into.
      </p>
    );
  }
  if (!data) {
    return error ? (
      <div className="flex flex-col items-start gap-2 p-6" role="alert">
        <p className="text-sm text-foreground">Could not read the backlog. {error}</p>
        <Button size="sm" variant="secondary" onClick={() => void load(pages)}>
          Try again
        </Button>
      </div>
    ) : (
      <div className="p-4">
        <SkeletonRows count={6} />
      </div>
    );
  }

  const backlogTasks = tasksAt(BACKLOG);
  // The server's total, with the moves that its last read does not hold yet.
  const backlogCount = Math.max(0, data.total + backlogTasks.length - data.backlog.length);

  return (
    <div className="flex flex-col gap-4 p-3 glass:p-4" data-backlog-canvas="">
      {selectedIds.length > 0 ? (
        <div
          className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 shadow-sm"
          role="toolbar"
          aria-label="Selected tasks"
          data-backlog-selection=""
        >
          <span className="text-xs font-medium text-foreground" role="status">
            {selectedIds.length} selected
          </span>
          {moveControl(selectedIds)}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setSelection({ selected: new Set(), anchor: null })}
          >
            Clear
          </Button>
        </div>
      ) : null}

      <section aria-label="Planned sprints" className="flex flex-col gap-2" data-backlog-cycles="">
        {/* pl-3: the heading lines up with the sprint names and the Backlog heading. */}
        <div className="flex items-center gap-2 pl-3">
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Planned sprints
          </h3>
          <span className="ml-auto" />
          <PlanSprint
            phone={surface.sheet}
            count={data.cycles.length}
            onPlan={async (plan) => {
              const made = await projectsApi.createCycle(node.id, plan);
              flash(`cycle:${made.cycle.id}`);
              await load(pages);
            }}
          />
        </div>
        {running ? (
          <p className="text-xs text-muted-foreground" data-backlog-running="">
            {running.name} is running, with {countWords(running.tasks ?? 0, running.hidden ?? 0).toLowerCase()}.
            Its tasks are not in the backlog.
          </p>
        ) : null}
        {planned.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
            No planned sprints yet. Plan one, then move tasks into it.
          </p>
        ) : (
          planned.map((cycle) => {
            const place = cyclePlace(cycle.id);
            const rows = tasksAt(place);
            return (
              <div
                key={cycle.id}
                ref={attach(`cycle:${cycle.id}`)}
                role="group"
                aria-label={`${cycle.name}, ${countWords(rows.length, data.hidden[cycle.id] ?? 0)}`}
                data-backlog-cycle={cycle.id}
                className={[
                  "tech-transition flex flex-col gap-2 rounded-lg border border-border bg-muted/30 p-2",
                  isOver(place) ? "ring-2 ring-primary/40" : "",
                ].join(" ")}
                {...dropProps(place)}
              >
                <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 px-1">
                  <span className="min-w-0 truncate text-sm font-medium text-foreground">{cycle.name}</span>
                  <span className="text-xs text-muted-foreground">{cycleDates(cycle)}</span>
                  <span className="ml-auto text-xs tabular-nums text-muted-foreground" data-backlog-count="">
                    {countWords(rows.length, data.hidden[cycle.id] ?? 0)}
                  </span>
                </div>
                {rows.length === 0 ? (
                  <p className="px-1 pb-1 text-xs text-muted-foreground">
                    {surface.drag ? "Drag tasks here, or use Move to sprint." : "Select tasks, then use Move to sprint."}
                  </p>
                ) : (
                  <ul className="flex flex-col gap-1.5">{rows.map(card)}</ul>
                )}
              </div>
            );
          })
        )}
      </section>

      <section
        aria-label="Backlog"
        data-backlog-list=""
        className={[
          "tech-transition flex flex-col gap-2 rounded-lg p-2",
          isOver(BACKLOG) ? "ring-2 ring-primary/40" : "",
        ].join(" ")}
        {...dropProps(BACKLOG)}
      >
        <div className="flex items-baseline gap-2 px-1">
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Backlog</h3>
          <span className="text-xs tabular-nums text-muted-foreground" data-backlog-count="">
            {countWords(backlogCount)}
          </span>
          <span className="ml-auto text-[11px] text-muted-foreground">Most pressing first</span>
        </div>
        {backlogTasks.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
            The backlog is empty. Every open task is in a sprint.
          </p>
        ) : (
          <ul className="flex flex-col gap-1.5">{backlogTasks.map(card)}</ul>
        )}
        <MoreTasksBar
          atEnd
          loaded={data.backlog.length}
          total={data.total}
          busy={loadingMore}
          onLoadMore={() => void loadMore()}
        />
      </section>
    </div>
  );
}

/** "Plan a sprint": a name and two dates, through `POST /nodes/{id}/cycles`. */
function PlanSprint({
  phone,
  count,
  onPlan,
}: {
  phone: boolean;
  count: number;
  onPlan: (plan: { name: string; starts_on: string; ends_on: string }) => Promise<void>;
}) {
  const [plan, setPlan] = useState(() => planDefaults(new Date(), count));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const problem = planProblem(plan);

  return (
    <SheetButton label="Plan a sprint" icon="Plus" phone={phone}>
      {(close) => (
        <form
          className="flex flex-col gap-2 p-1"
          data-plan-sprint=""
          onSubmit={(event) => {
            event.preventDefault();
            if (problem || busy) return;
            setBusy(true);
            setError(null);
            onPlan({ ...plan, name: plan.name.trim() })
              .then(() => {
                setPlan(planDefaults(new Date(), count + 1));
                close();
              })
              .catch((err: Error) => setError(err.message || "Could not plan the sprint. Try again."))
              .finally(() => setBusy(false));
          }}
        >
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Name
            <Input
              inputSize="sm"
              value={plan.name}
              maxLength={120}
              onChange={(e) => setPlan({ ...plan, name: e.target.value })}
              aria-label="Sprint name"
            />
          </label>
          <div className="flex gap-2">
            <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs text-muted-foreground">
              Starts
              <Input
                inputSize="sm"
                type="date"
                value={plan.starts_on}
                onChange={(e) => setPlan({ ...plan, starts_on: e.target.value })}
                aria-label="Start date"
              />
            </label>
            <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs text-muted-foreground">
              Ends
              <Input
                inputSize="sm"
                type="date"
                value={plan.ends_on}
                onChange={(e) => setPlan({ ...plan, ends_on: e.target.value })}
                aria-label="End date"
              />
            </label>
          </div>
          {problem || error ? (
            <p className="text-xs text-destructive" role="alert">
              {error ?? problem}
            </p>
          ) : null}
          <Button type="submit" size="sm" loading={busy} disabled={Boolean(problem) || busy}>
            Plan sprint
          </Button>
        </form>
      )}
    </SheetButton>
  );
}

export default BacklogCanvas;
