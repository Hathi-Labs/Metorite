"use client";

/**
 * Projects · the board.
 *
 * Columns are whatever `lib/grouping.groupTasks` produced — status lanes by
 * default, but also assignee, project or priority (WS-27k). The grouping
 * decision is the page's; this component only draws it.
 *
 * WS-27y adds the second axis: with a sub-grouping chosen, the board becomes
 * group columns × sub-group swimlanes, computed by `lib/swimlanes.ts`. Lanes
 * collapse (state travels with the saved view), empty lanes hide unless
 * asked, and a drop into a lane cell writes BOTH axes at once through
 * `buildCellDropPatch`.
 *
 * **Dragging is always offered; refusing is explained.** Where the old board
 * silently disabled dragging off the status axis, a drag over a target that
 * cannot take the drop now overlays the target with `dropRefusal`'s reason —
 * a card that snaps back wordlessly teaches people the board is broken, not
 * that assignees are many-valued.
 *
 * Ordering is per view (D-PM-5): a drop writes fractional positions through
 * `planDrop`, which is one row in the normal case and the whole group on the
 * first drag into an unordered column.
 *
 * WS-27bd adds the right-click menu. The MENU is `@/components/ContextMenu` —
 * the one /tasks has used for five call sites, promoted rather than rewritten —
 * and its ITEMS come from `lib/taskMenu.ts`, the declared task-action
 * registry. Nothing new arrives from `page.tsx`: Open is `onSelect`,
 * Select is `onToggle`, and Change status is `onDrop` carrying an axis patch
 * and no reordering, which is exactly what dragging the card there would send.
 */
import { ContextMenu, type CtxItem } from "@/components/ContextMenu";
import { AvatarStack, TaskMeta } from "@/components/TaskMeta";
import { DropGap } from "@/components/DropGap";
import { EmptyState } from "@/components/EmptyState";
import Icon, { themedIcon } from "@/components/Icon";
import { StatusChip } from "@/components/StatusChip";
import { TaskCardShell, TaskCardTitle } from "@/components/TaskCardShell";
import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { dropIndexFor, gapKey } from "@/lib/boardDrop";
import { Fragment, useMemo, useState } from "react";

import { accentForGroup, accentForStatus } from "../lib/accent";
import type { StatusRow, TagRow, TaskRow } from "../lib/api";
import { projectsApi } from "../lib/api";
import {
  buildCellDropPatch,
  buildColumnDropUpdate,
  dropRefusal,
  planDrop,
  sortForView,
} from "../lib/board";
import { tagColours, taskDeepLink, taskRef, visibleChips } from "../lib/card";
import { clampCursor, stepCursor } from "../lib/cursor";
import { emptyStateCopy } from "../lib/emptyState";
import {
  type BoardLanes,
  type Filters,
  type GroupBy,
  type TaskGroup,
  isFiltered,
  personLabel,
} from "../lib/grouping";
import { mergePlans, quickAddPrefill } from "../lib/quickAdd";
import {
  type Swimlane,
  buildSwimlanes,
  hiddenLaneCount,
  visibleLanes,
} from "../lib/swimlanes";
import { type TaskMenuActions, taskMenuItems } from "../lib/taskMenu";
import { QuickAdd } from "./QuickAdd";
import { useFlash } from "./useFlash";

const NOBODY: ReadonlySet<string> = new Set();

interface Props {
  groups: TaskGroup[];
  groupBy: GroupBy;
  /**
   * S4 — the view's filters, for the empty state alone.
   *
   * Required rather than optional: an unwired call site would silently blame
   * the project's statuses for a board somebody filtered to nothing, which is
   * the defect this props pair exists to end. `tsc` is the fence.
   */
  filters: Filters;
  onClearFilters: () => void;
  /** WS-27y — the second axis and its lane state. */
  lanes: BoardLanes;
  onToggleLane: (key: string) => void;
  onShowEmptyLanes: (show: boolean) => void;
  /** Context the lane computation needs — same as the page's `groupTasks` call. */
  statuses: StatusRow[];
  /**
   * S6 — the project's tag registry, for the colour of a tag chip alone.
   *
   * The card names its tags now, and a tag has to be the SAME colour here, in
   * the picker inside the panel and in the filter bar — `pm_tags.color` is the
   * owner's choice and there is one of it. Optional so a surface that has no
   * registry (a cross-project list) draws named chips in the gray fallback
   * rather than the wrong colour.
   */
  tags?: readonly TagRow[];
  projectName?: (id: string) => string;
  /** Where a quick-added task is created (the selected node). */
  projectId: string;
  /** WS-27x — the view's shown fields; chips a hidden field earned are not drawn. */
  shownFields: readonly string[];
  onCreated: (task: TaskRow) => void;
  /** WS-27n — ids currently multi-selected. Empty when nobody is bulk editing. */
  selected?: ReadonlySet<string>;
  onToggle?: (id: string, shift: boolean) => void;
  /** WS-27y — Shift+Arrow grew the selection to exactly these ids. */
  onExtendSelection?: (ids: string[]) => void;
  onSelect: (task: TaskRow) => void;
  onDrop: (
    task: TaskRow,
    writes: ReturnType<typeof planDrop>,
    patch: Record<string, string | number | null> | null
  ) => void;
}

export function TaskBoard({
  groups,
  groupBy,
  filters,
  onClearFilters,
  lanes,
  onToggleLane,
  onShowEmptyLanes,
  statuses,
  tags,
  projectName,
  projectId,
  shownFields,
  onCreated,
  selected,
  onToggle,
  onExtendSelection,
  onSelect,
  onDrop,
}: Props) {
  const [dragging, setDragging] = useState<TaskRow | null>(null);
  const [over, setOver] = useState<{ col: string; lane: string | null } | null>(
    null
  );
  /** The exact gap the card would land in, as `@/lib/boardDrop.gapKey`. */
  const [dropAt, setDropAt] = useState<string | null>(null);
  const [cursor, setCursor] = useState(-1);
  const [anchor, setAnchor] = useState<number | null>(null);
  /** WS-27bd — where the right-click landed, and on which card. */
  const [menu, setMenu] = useState<{ x: number; y: number; task: TaskRow } | null>(
    null
  );
  const { flash, attach, scrollTo } = useFlash();

  // `fromConfig` normalises an equal sub-axis away, but the axis pickers can
  // briefly say "status × status" between two keystrokes — treat it as flat.
  const subBy = lanes.subGroupBy === groupBy ? "none" : lanes.subGroupBy;
  const laned = subBy !== "none";

  const columns = useMemo(
    () => groups.map((group) => ({ ...group, tasks: sortForView(group.tasks) })),
    [groups]
  );

  const statusById = useMemo(
    () => new Map(statuses.map((row) => [row.id, row])),
    [statuses]
  );

  // Once per registry, not once per card: a board draws hundreds of rows.
  const tagHues = useMemo(() => tagColours(tags ?? []), [tags]);

  /**
   * WS-27ad — the column's colour.
   *
   * `pm_task_statuses.color` has been stored since migration 146 and drawn
   * nowhere: every column header was the same `bg-muted`, so a Done lane and a
   * Backlog lane were indistinguishable while the /tasks board next door was
   * colour-coded per stage. The owner's stored colour answers first, then the
   * status category (`accentForGroup`), then position.
   */
  const columnAccents = useMemo(
    () =>
      columns.map((column, index) =>
        accentForGroup(groupBy, column.key, index, columns.length, statuses)
      ),
    [columns, groupBy, statuses]
  );

  const swimlanes = useMemo<Swimlane[] | null>(
    () =>
      laned ? buildSwimlanes(columns, subBy, { statuses, projectName }) : null,
    [laned, columns, subBy, statuses, projectName]
  );
  const shownLanes = useMemo(
    () => (swimlanes ? visibleLanes(swimlanes, lanes.showEmptyLanes) : null),
    [swimlanes, lanes.showEmptyLanes]
  );
  const collapsed = useMemo(
    () => new Set(lanes.collapsedLanes),
    [lanes.collapsedLanes]
  );

  // The keyboard cursor's world: every card in render order, each id once.
  const rows = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    const push = (task: TaskRow) => {
      if (!seen.has(task.id)) {
        seen.add(task.id);
        out.push(task.id);
      }
    };
    if (shownLanes) {
      for (const lane of shownLanes) {
        if (collapsed.has(lane.key)) continue;
        for (const cell of lane.cells) for (const task of cell) push(task);
      }
    } else {
      for (const column of columns) for (const task of column.tasks) push(task);
    }
    return out;
  }, [shownLanes, collapsed, columns]);

  const taskById = useMemo(() => {
    const map = new Map<string, TaskRow>();
    for (const column of columns)
      for (const task of column.tasks) map.set(task.id, task);
    return map;
  }, [columns]);

  // Clamped at READ time rather than synced by an effect: the rows shrink
  // under the cursor on every reload, and a state write per reload is exactly
  // the cascading-render pattern the lint forbids.
  const cursorAt = clampCursor(rows.length, cursor);

  function onKeyDown(event: React.KeyboardEvent) {
    // Keystrokes inside a quick-add (or any control) are that control's.
    if (
      (event.target as HTMLElement).closest(
        "input, textarea, select, [contenteditable=true]"
      )
    )
      return;
    const picked = selected ?? NOBODY;
    const next = stepCursor(
      rows,
      { cursor: cursorAt, anchor, selection: picked },
      event.key,
      event.shiftKey
    );
    if (!next) return;
    event.preventDefault();
    setCursor(next.cursor);
    setAnchor(next.anchor);
    if (next.selection !== picked) onExtendSelection?.([...next.selection]);
    if (next.open) {
      const task = taskById.get(next.open);
      if (task) onSelect(task);
    }
    if (next.cursor >= 0) scrollTo(rows[next.cursor]);
  }

  /** The refusal for the target under the drag, or null. One rule for column
   *  and cell targets: a cell involves the lane axis, a plain column does not. */
  const refusalFor = (laneKey: string | null): string | null =>
    dropRefusal(groupBy, laneKey === null ? null : subBy, dragging ?? undefined);

  /**
   * WS-27ad — a drop lands where it was aimed.
   *
   * `gapIndex` omitted means the card was dropped on the column body rather
   * than on a gap, which still appends: that is the honest reading of "somewhere
   * in this column". What changed is that there are now gaps to aim at at all —
   * this board used to append unconditionally, so dragging a card two rows up
   * dropped it at the bottom and the gesture visibly failed.
   */
  function applyDrop(
    colKey: string,
    cellTasks: TaskRow[],
    laneKey: string | null,
    gapIndex?: number
  ) {
    setOver(null);
    setDropAt(null);
    const task = dragging;
    setDragging(null);
    if (!task || refusalFor(laneKey)) return;
    const index =
      gapIndex === undefined
        ? cellTasks.length
        : dropIndexFor(cellTasks, task.id, gapIndex);
    const writes = planDrop(cellTasks, task.id, index, colKey);
    const patch = buildCellDropPatch(
      task,
      groupBy,
      colKey,
      laneKey === null ? null : subBy,
      laneKey
    );
    flash(task.id);
    onDrop(task, writes, patch);
  }

  const targetProps = (
    colKey: string,
    cellTasks: TaskRow[],
    laneKey: string | null
  ) => ({
    onDragOver: (event: React.DragEvent) => {
      if (!dragging) return;
      // preventDefault even when refusing — the browser must keep sending
      // events or the overlay could never show; the refusal is enforced in
      // `applyDrop`, and the cursor says "no" via dropEffect.
      event.preventDefault();
      event.dataTransfer.dropEffect = refusalFor(laneKey) ? "none" : "move";
      setOver((current) =>
        current?.col === colKey && current?.lane === laneKey
          ? current
          : { col: colKey, lane: laneKey }
      );
    },
    onDragLeave: (event: React.DragEvent) => {
      if (event.currentTarget.contains(event.relatedTarget as Node)) return;
      setOver((current) =>
        current?.col === colKey && current?.lane === laneKey ? null : current
      );
    },
    onDrop: (event: React.DragEvent) => {
      // The column body — no gap was hit, so this appends (see `applyDrop`).
      event.preventDefault();
      applyDrop(colKey, cellTasks, laneKey);
    },
  });

  /**
   * A cell's cards with a drop gap above each and one at the end.
   *
   * The gap keys are namespaced by lane as well as column: with a sub-grouping
   * on, two cells in the same column are the same `colKey`, and a bare
   * `col:index` would light the highlight up in both at once.
   */
  const cellCards = (
    colKey: string,
    cellTasks: TaskRow[],
    laneKey: string | null
  ) => {
    const scope = laneKey === null ? colKey : `${colKey}\0${laneKey}`;
    const gap = (index: number) => (
      <li key={`gap-${index}`}>
        <DropGap
          active={dropAt === gapKey(scope, index)}
          onOver={() => dragging && setDropAt(gapKey(scope, index))}
          onDrop={() => applyDrop(colKey, cellTasks, laneKey, index)}
        />
      </li>
    );
    return (
      <>
        {cellTasks.map((task, index) => (
          <Fragment key={task.id}>
            {gap(index)}
            {card(task)}
          </Fragment>
        ))}
        {cellTasks.length > 0 ? gap(cellTasks.length) : null}
      </>
    );
  };

  async function quickAdd(title: string, colKey: string, laneKey: string | null) {
    const plan =
      laneKey === null
        ? quickAddPrefill(groupBy, colKey)
        : mergePlans(quickAddPrefill(groupBy, colKey), quickAddPrefill(subBy, laneKey));
    const created = await projectsApi.createTask({
      project_id: projectId,
      title,
      ...plan.create,
    });
    if (plan.assignees?.length) {
      // Best-effort: the task already exists. A failed PUT leaves it visible
      // in Unassigned, which is honest; throwing here would invite a retry
      // that creates it twice.
      try {
        await projectsApi.setAssignees(created.id, plan.assignees);
      } catch {
        /* the board will show where it actually landed */
      }
    }
    flash(created.id);
    onCreated(created);
  }

  const refusalOverlay = (laneKey: string | null, colKey: string) =>
    over?.col === colKey && over?.lane === laneKey && dragging ? (
      (() => {
        const reason = refusalFor(laneKey);
        return reason ? (
          <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-background/85 p-3 text-center text-xs font-medium text-destructive">
            {reason}
          </div>
        ) : null;
      })()
    ) : null;

  /**
   * WS-27bd — what a right-click can do, over the props the board ALREADY has.
   *
   * Nothing new arrives from `page.tsx`. `onSelect` opens, `onToggle` selects,
   * and a status change is `onDrop` carrying the axis patch with an EMPTY
   * position plan: the card keeps whatever manual order it had, which is the
   * honest reading of "change this task's status" as opposed to "drag it to the
   * end of that column". `buildColumnDropUpdate` builds the patch rather than an
   * inline `{ status_id }`, so the menu and the drag write the same shape.
   */
  const menuActions: TaskMenuActions = {
    open: (task) => onSelect(task),
    copyLink: (task) => {
      void (async () => {
        try {
          await navigator.clipboard.writeText(
            taskDeepLink(task, window.location.origin)
          );
        } catch {
          // Clipboard can be unavailable (permissions, insecure context). The
          // menu closes on select and there is no "Copied" state to flip, so a
          // refusal claims nothing — the `catch` is here so a denied write is
          // not an unhandled rejection. Same reasoning `TaskPanel`'s copy site
          // writes out; this is not a second clipboard policy.
        }
      })();
    },
    // `false` — the range-extend gesture belongs to the control that starts a
    // range, and a right-click has no anchor.
    toggleSelect: (task) => onToggle?.(task.id, false),
    setStatus: (task, statusId) => {
      if (task.status_id === statusId) return;
      onDrop(task, [], buildColumnDropUpdate("status", statusId));
    },
  };

  const menuFor = (task: TaskRow) => ({
    task,
    statuses,
    // The checkbox and the menu row agree by construction: both are the
    // presence of `onToggle`, so a surface that cannot select cannot offer it.
    canSelect: Boolean(onToggle),
    selected: selected?.has(task.id) ?? false,
  });

  /** The registry's entries as the shared menu's items. Icons resolve here —
   *  `taskMenu.ts` stays pure and names glyphs, it never imports a component. */
  const menuItems = (task: TaskRow): CtxItem[] => {
    const ctx = menuFor(task);
    return taskMenuItems(ctx).map((entry): CtxItem =>
      entry.kind === "item"
        ? {
            kind: "item",
            label: entry.label,
            icon: entry.icon ? themedIcon(entry.icon) : undefined,
            checked: entry.checked,
            onSelect: () => entry.run(menuActions, ctx),
          }
        : entry
    );
  };

  const card = (task: TaskRow) => (
    <li key={task.id} className="flex items-start gap-1.5 rounded-md">
      {onToggle ? (
        <Checkbox
          className="mt-3 shrink-0"
          aria-label={`Select ${task.title}`}
          checked={selected?.has(task.id) ?? false}
          // The click must not also open the task — a checkbox inside a card
          // that opens on click is otherwise one gesture with two outcomes.
          onClick={(e) => e.stopPropagation()}
          onChange={(e) =>
            onToggle(task.id, (e.nativeEvent as MouseEvent).shiftKey)
          }
        />
      ) : null}
      {/* WS-27ad: the same box /tasks draws (`@/components/TaskCardShell`) —
          `bg-card` on the column's `bg-card` well, the shadow lift that says
          "draggable", one radius, one padding, one title treatment. This card
          used to be `bg-background`, i.e. the page colour, which read as a
          hole in the column rather than a card on it. */}
      <TaskCardShell
        innerRef={attach(task.id)}
        className="w-full min-w-0"
        draggable
        completed={Boolean(task.completed_at)}
        selected={selected?.has(task.id) ?? false}
        atCursor={cursorAt >= 0 && rows[cursorAt] === task.id}
        onActivate={() => onSelect(task)}
        // WS-27bd — the shell has accepted this prop since S1 and /projects
        // never passed it, which is why the app had zero `onContextMenu`.
        onContextMenu={(event) => {
          event.preventDefault();
          event.stopPropagation();
          setMenu({ x: event.clientX, y: event.clientY, task });
        }}
        onDragStart={(e) => {
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", task.id);
          setDragging(task);
        }}
        onDragEnd={() => {
          setDragging(null);
          setOver(null);
          setDropAt(null);
        }}
      >
        {/* Off the status axis the column no longer says what the status is,
            so the card carries it — the same rule /tasks applies with
            `showStage` on a lens-grouped list, and the same pill. */}
        {groupBy === "status" ? null : (
          <StatusChip
            accent={accentForStatus(statusById.get(task.status_id))}
            label={statusById.get(task.status_id)?.name ?? "No status"}
            className="w-fit"
          />
        )}
        <TaskCardTitle completed={Boolean(task.completed_at)} className="truncate">
          {task.title}
        </TaskCardTitle>
        {/* The chip row and the owner strip are the shared card vocabulary
            (WS-27s) — the same components /tasks draws, so a task looks like
            the same kind of thing in both. S6 added the two facts the row
            already carried and the card threw away: the priority the view's
            `shown_fields` has always asked for, and the tags by NAME in the
            colour their registry gives them, instead of a bare count. */}
        <TaskMeta chips={visibleChips(task, shownFields, undefined, tagHues)} />
        <span className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
          <span>{taskRef(task)}</span>
          <AvatarStack people={task.assignees} label={personLabel} />
        </span>
      </TaskCardShell>
    </li>
  );

  if (columns.length === 0) {
    // S4 — the old copy named both causes in one sentence ("Clear a filter, or
    // this project has no statuses yet") and left the reader to work out which
    // one was theirs. `isFiltered` already knows, and off the status axis the
    // statuses are not what is missing at all.
    const copy = emptyStateCopy({
      canvas: "board",
      filtered: isFiltered(filters),
      onStatusAxis: groupBy === "status",
    });
    return (
      <EmptyState
        icon={copy.icon}
        message={copy.message}
        hint={copy.hint}
        action={
          copy.filtered
            ? { label: "Clear filters", icon: "X", onClick: onClearFilters }
            : undefined
        }
      />
    );
  }

  const droppable = dropRefusal(groupBy, laned ? subBy : null) === null;
  const hidden = swimlanes ? hiddenLaneCount(swimlanes) : 0;

  return (
    <div
      tabIndex={0}
      onKeyDown={onKeyDown}
      className="outline-none"
      aria-label="Task board — arrow keys move, Shift extends the selection, Enter opens"
    >
      {laned ? (
        <div className="flex items-center gap-2 px-3 pt-2">
          <Button
            variant={lanes.showEmptyLanes ? "secondary" : "ghost"}
            size="sm"
            aria-pressed={lanes.showEmptyLanes}
            onClick={() => onShowEmptyLanes(!lanes.showEmptyLanes)}
          >
            Show empty lanes
          </Button>
          {!lanes.showEmptyLanes && hidden > 0 ? (
            <span className="text-xs text-muted-foreground">
              {hidden} empty {hidden === 1 ? "lane" : "lanes"} hidden
            </span>
          ) : null}
        </div>
      ) : null}

      {!laned ? (
        <div className="flex gap-3 overflow-x-auto p-3">
          {columns.map((column, columnIndex) => {
            const accent = columnAccents[columnIndex];
            return (
            <section
              key={column.key}
              {...targetProps(column.key, column.tasks, null)}
              className="relative flex w-72 shrink-0 flex-col overflow-hidden rounded-lg border border-border bg-card"
            >
              {refusalOverlay(null, column.key)}
              {/* The accent cap — /tasks' board grammar, now shared: a 1px
                  band of the lane's colour above a faintly tinted header, so
                  "where is Done" is a glance rather than a read. */}
              <div className={`h-1 w-full ${accent.dot}`} />
              <header
                className={`flex items-center justify-between px-3 py-2 ${accent.soft}`}
              >
                <span className="flex min-w-0 items-center gap-1.5">
                  <span className={`h-2 w-2 shrink-0 rounded-full ${accent.dot}`} />
                  <span className={`truncate text-sm font-medium ${accent.text}`}>
                    {column.label}
                  </span>
                </span>
                <span className="shrink-0 rounded-full bg-background/60 px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
                  {column.tasks.length}
                </span>
              </header>
              <ul className="flex-1 space-y-1 p-2">
                {cellCards(column.key, column.tasks, null)}
                {column.tasks.length === 0 ? (
                  <li className="rounded-md border border-dashed border-border p-3 text-center text-xs text-muted-foreground">
                    {droppable ? "Drop here" : "Nothing here"}
                  </li>
                ) : null}
              </ul>
              <div className="p-2 pt-0">
                <QuickAdd
                  label={`Add to ${column.label}`}
                  onAdd={(title) => quickAdd(title, column.key, null)}
                />
              </div>
            </section>
            );
          })}
        </div>
      ) : (
        <div className="overflow-x-auto p-3">
          <div className="min-w-max">
            {/* Column headers once, up top — every lane below shares them, and
                they carry the same accent the flat board's headers do. */}
            <div className="mb-2 flex gap-3">
              {columns.map((column, columnIndex) => {
                const accent = columnAccents[columnIndex];
                return (
                  <div
                    key={column.key}
                    className={`flex w-72 shrink-0 items-center justify-between rounded-md border-l-2 px-3 py-2 ${accent.soft} ${accent.bar}`}
                  >
                    <span className="flex min-w-0 items-center gap-1.5">
                      <span className={`h-2 w-2 shrink-0 rounded-full ${accent.dot}`} />
                      <span className={`truncate text-sm font-medium ${accent.text}`}>
                        {column.label}
                      </span>
                    </span>
                    <span className="shrink-0 rounded-full bg-background/60 px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
                      {column.tasks.length}
                    </span>
                  </div>
                );
              })}
            </div>

            {(shownLanes ?? []).map((lane) => {
              const folded = collapsed.has(lane.key);
              return (
                <section key={lane.key} className="mb-2">
                  <button
                    type="button"
                    onClick={() => onToggleLane(lane.key)}
                    aria-expanded={!folded}
                    className="flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-sm font-medium text-foreground hover:bg-muted"
                  >
                    <Icon name={folded ? "ChevronRight" : "ChevronDown"} size={14} />
                    <span className="truncate">{lane.label}</span>
                    <span className="text-xs font-normal text-muted-foreground">
                      {lane.total}
                    </span>
                  </button>
                  {folded ? null : (
                    <div className="mt-1 flex gap-3">
                      {lane.cells.map((cell, columnIndex) => {
                        const column = columns[columnIndex];
                        return (
                          <div
                            key={column.key}
                            {...targetProps(column.key, cell, lane.key)}
                            className="relative flex w-72 shrink-0 flex-col rounded-lg border border-border bg-card"
                          >
                            {refusalOverlay(lane.key, column.key)}
                            <ul className="flex-1 space-y-1 p-2">
                              {cellCards(column.key, cell, lane.key)}
                              {cell.length === 0 ? (
                                <li className="rounded-md border border-dashed border-border p-2 text-center text-[11px] text-muted-foreground">
                                  {droppable ? "Drop here" : "—"}
                                </li>
                              ) : null}
                            </ul>
                            <div className="p-2 pt-0">
                              <QuickAdd
                                label={`Add to ${column.label} · ${lane.label}`}
                                onAdd={(title) =>
                                  quickAdd(title, column.key, lane.key)
                                }
                              />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </section>
              );
            })}
            {shownLanes && shownLanes.length === 0 ? (
              <p className="p-3 text-sm text-muted-foreground">
                Every lane is empty. Turn on “Show empty lanes” to see them.
              </p>
            ) : null}
          </div>
        </div>
      )}

      {/* One menu for the whole board, positioned at the pointer — not one per
          card. The flat/laned branches both raise it, so it lives out here. */}
      {menu ? (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          items={menuItems(menu.task)}
          onClose={() => setMenu(null)}
        />
      ) : null}
    </div>
  );
}
