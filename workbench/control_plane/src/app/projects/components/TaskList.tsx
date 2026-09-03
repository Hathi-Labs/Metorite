"use client";

/**
 * Projects · the list view.
 *
 * The same task set as the board, read from the same endpoint — Paca's lesson
 * that list and board are one query with different presentation, so growing a
 * second endpoint per surface is how the filters start disagreeing about what a
 * member may see.
 *
 * Grouping is that same presentation choice (WS-27k): the list draws the groups
 * the board would have drawn as columns, as headed sections. Switching between
 * board and list must not change which tasks are on screen or how they are
 * gathered, which is why both take the output of one `groupTasks` call.
 *
 * WS-27y: every group section ends in a quick-add pre-filled with the group's
 * value (`lib/quickAdd.ts` owns that mapping), and an arrow-key cursor walks
 * the rows — Shift extends the WS-27n selection, Enter opens the panel.
 *
 * WS-27ab item 6: **the Status and Assignees columns obey `shown_fields`** like
 * every other field on this surface. They rendered unconditionally from WS-27x
 * until now, which made the field picker a liar here — un-ticking *Status*
 * silenced its chip on the board and left the column standing on the list.
 * Which columns exist is `table.listColumns`, so the header, the group heading
 * and the quick-add row cannot disagree about the `colSpan`. No default moved:
 * both keys are in `DEFAULT_SHOWN`.
 */
import { ControlLink } from "@/components/ControlLink";
import { Checkbox } from "@/components/ui/Checkbox";
import { EmptyState } from "@/components/EmptyState";
import Icon from "@/components/Icon";
import { StatusChip } from "@/components/StatusChip";
import { AvatarStack, TaskMeta } from "@/components/TaskMeta";
import { useMemo, useState } from "react";

import { accentForGroup, accentForStatus } from "../lib/accent";
import type { StatusRow, TagRow, TaskRow } from "../lib/api";
import { projectsApi } from "../lib/api";
import { sortForView } from "../lib/board";
import { tagColours, taskDeepLink, taskRef, visibleChips } from "../lib/card";
import { clampCursor, stepCursor } from "../lib/cursor";
import { emptyStateCopy } from "../lib/emptyState";
import {
  type Filters,
  type GroupBy,
  type TaskGroup,
  isFiltered,
  personLabel,
} from "../lib/grouping";
import { quickAddPrefill } from "../lib/quickAdd";
import { listColumns } from "../lib/table";
import { QuickAdd } from "./QuickAdd";
import { useFlash } from "./useFlash";

const NOBODY: ReadonlySet<string> = new Set();

interface Props {
  groups: TaskGroup[];
  groupBy: GroupBy;
  /**
   * S4 — the view's filters, for the empty state alone.
   *
   * Required rather than optional: an unwired call site would silently draw
   * "no tasks here yet" over a filtered-to-nothing list, which is the exact
   * defect this props pair exists to end. `tsc` is the fence.
   */
  filters: Filters;
  onClearFilters: () => void;
  statuses: StatusRow[];
  /** S6 — the project's tag registry, for the colour of a tag chip alone. */
  tags?: readonly TagRow[];
  /** WS-27y — where a quick-added task is created (the selected node). */
  projectId: string;
  /** WS-27x — the view's shown fields; chips a hidden field earned are not drawn. */
  shownFields: readonly string[];
  onCreated: (task: TaskRow) => void;
  /** WS-27n — ids currently multi-selected. */
  selected?: ReadonlySet<string>;
  onToggle?: (id: string, shift: boolean) => void;
  onToggleAll?: () => void;
  allChecked?: boolean;
  /** WS-27y — Shift+Arrow grew the selection to exactly these ids. */
  onExtendSelection?: (ids: string[]) => void;
  onSelect: (task: TaskRow) => void;
}

export function TaskList({
  groups,
  groupBy,
  filters,
  onClearFilters,
  statuses,
  tags,
  projectId,
  shownFields,
  onCreated,
  selected,
  onToggle,
  onToggleAll,
  allChecked = false,
  onExtendSelection,
  onSelect,
}: Props) {
  const [cursor, setCursor] = useState(-1);
  const [anchor, setAnchor] = useState<number | null>(null);
  const { flash, attach, scrollTo } = useFlash();
  // Group sections collapse, /tasks-style (TaskListGrouped's grammar): a
  // chevron on the header, local state, the header row itself stays put.
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const toggleFold = (key: string) =>
    setFolded((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const statusById = new Map(statuses.map((s) => [s.id, s]));
  const total = groups.reduce((sum, group) => sum + group.tasks.length, 0);
  // Once per registry, not once per row.
  const tagHues = useMemo(() => tagColours(tags ?? []), [tags]);

  // The cursor's world: rendered order, each id once (a two-owner task is
  // drawn in two sections but is one row to the keyboard, as to WS-27n).
  const sections = useMemo(
    () =>
      groups
        .filter((group) => group.tasks.length > 0)
        .map((group) => ({ ...group, tasks: sortForView(group.tasks) })),
    [groups]
  );
  const rows = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const group of sections) {
      // A folded section's rows are off-screen, so the cursor skips them —
      // same rule the board applies to a collapsed lane.
      if (folded.has(group.key)) continue;
      for (const task of group.tasks)
        if (!seen.has(task.id)) {
          seen.add(task.id);
          out.push(task.id);
        }
    }
    return out;
  }, [sections, folded]);
  const taskById = useMemo(() => {
    const map = new Map<string, TaskRow>();
    for (const group of sections) for (const task of group.tasks) map.set(task.id, task);
    return map;
  }, [sections]);

  // Clamped at READ time rather than synced by an effect: the rows shrink
  // under the cursor on every reload, and a state write per reload is exactly
  // the cascading-render pattern the lint forbids.
  const cursorAt = clampCursor(rows.length, cursor);

  function onKeyDown(event: React.KeyboardEvent) {
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

  async function quickAdd(title: string, groupKey: string) {
    const plan = quickAddPrefill(groupBy, groupKey);
    const created = await projectsApi.createTask({
      project_id: projectId,
      title,
      ...plan.create,
    });
    if (plan.assignees?.length) {
      // Best-effort: the task exists; a failed PUT leaves it honestly in
      // Unassigned rather than inviting a duplicate-creating retry.
      try {
        await projectsApi.setAssignees(created.id, plan.assignees);
      } catch {
        /* the list will show where it actually landed */
      }
    }
    flash(created.id);
    onCreated(created);
  }

  if (total === 0) {
    // S4 — two states, never one. "No tasks here yet" told somebody who had
    // filtered the list that their project was empty; `isFiltered` is the same
    // predicate the toolbar's Clear button reads, so the state and the control
    // that caused it cannot disagree.
    const copy = emptyStateCopy({ canvas: "list", filtered: isFiltered(filters) });
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

  // ONE list of columns, so the header, every group heading's colSpan and the
  // quick-add row's colSpan are the same number by construction.
  const columns = listColumns(shownFields, Boolean(onToggle));
  const columnCount = columns.length;
  const showStatus = columns.includes("status");
  const showAssignees = columns.includes("assignees");

  return (
    <div
      tabIndex={0}
      onKeyDown={onKeyDown}
      className="overflow-x-auto outline-none"
      aria-label="Task list — arrow keys move, Shift extends the selection, Enter opens"
    >
      <table className="w-full min-w-[640px] text-sm">
        <thead className="border-b border-border text-left text-xs text-muted-foreground">
          <tr>
            {onToggle ? (
              <th className="px-3 py-2 font-medium">
                <Checkbox
                  aria-label="Select every task on this page"
                  checked={allChecked}
                  onChange={() => onToggleAll?.()}
                />
              </th>
            ) : null}
            <th className="px-3 py-2 font-medium">#</th>
            <th className="px-3 py-2 font-medium">Title</th>
            {showStatus ? (
              <th className="px-3 py-2 font-medium">Status</th>
            ) : null}
            {showAssignees ? (
              <th className="px-3 py-2 font-medium">Assignees</th>
            ) : null}
            {/* Was "Due", showing a bare locale date. The shared chip row
                (WS-27s) carries the due date *and* says when it is overdue,
                what is blocking, how far a checklist has got, what priority
                somebody gave it and which tags it wears (S6) — the same strip
                the board card draws, so the two views describe a task
                identically. Renamed because it is no longer only the date. */}
            <th className="px-3 py-2 font-medium">Details</th>
          </tr>
        </thead>
        {/* An empty status lane is kept on the board so a missing column reads
            as a missing state; a list has no columns, so an empty section is
            just a heading with nothing under it — `sections` dropped it. */}
        {sections.map((group, groupIndex) => {
          const isFolded = groupBy !== "none" && folded.has(group.key);
          // WS-27ad — the same accent the board's column for this group wears,
          // as the left bar /tasks' list-group headers already used. A grouped
          // list and a board are two drawings of one grouping; they must not
          // disagree about what colour "Done" is.
          const accent = accentForGroup(
            groupBy,
            group.key,
            groupIndex,
            sections.length,
            statuses
          );
          return (
          <tbody key={group.key}>
            {groupBy === "none" ? null : (
              <tr className={accent.soft}>
                <th
                  colSpan={columnCount}
                  className={`border-l-2 px-3 py-1.5 text-left text-xs font-medium ${accent.bar} ${accent.text}`}
                >
                  {/* The /tasks group-header grammar (TaskListGrouped):
                      chevron to collapse, label, then the count as a pill —
                      so the two apps' grouped lists read identically. */}
                  <button
                    type="button"
                    onClick={() => toggleFold(group.key)}
                    aria-expanded={!isFolded}
                    className="flex min-w-0 items-center gap-2 text-left"
                  >
                    <Icon
                      name="ChevronRight"
                      className={`h-3.5 w-3.5 shrink-0 transition-transform ${accent.text} ${
                        isFolded ? "" : "rotate-90"
                      }`}
                    />
                    <span className={`h-2 w-2 shrink-0 rounded-full ${accent.dot}`} />
                    <span className="truncate">{group.label}</span>
                    <span className="shrink-0 rounded-full bg-background/60 px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
                      {group.tasks.length}
                    </span>
                  </button>
                </th>
              </tr>
            )}
            {isFolded ? null : group.tasks.map((task) => {
              const status = statusById.get(task.status_id);
              const atCursor = cursorAt >= 0 && rows[cursorAt] === task.id;
              return (
                <tr
                  key={task.id}
                  ref={attach(task.id)}
                  onClick={() => onSelect(task)}
                  className={`cursor-pointer border-b border-border last:border-0 hover:bg-muted ${
                    selected?.has(task.id) ? "bg-accent/40" : ""
                  } ${atCursor ? "bg-muted/60 ring-2 ring-inset ring-ring" : ""}`}
                >
                  {onToggle ? (
                    <td className="px-3 py-2">
                      <Checkbox
                        aria-label={`Select ${task.title}`}
                        checked={selected?.has(task.id) ?? false}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) =>
                          onToggle(
                            task.id,
                            (e.nativeEvent as MouseEvent).shiftKey,
                          )
                        }
                      />
                    </td>
                  ) : null}
                  <td className="px-3 py-2 text-muted-foreground">
                    {taskRef(task) ?? "—"}
                  </td>
                  <td className="px-3 py-2 text-foreground">
                    {/* WS-27al(1) — the title is a REAL link to the task's own
                        deep link, so cmd/ctrl/shift/middle-click open it in a
                        new tab the way they do everywhere else on the machine.
                        A plain click is intercepted and opens the docked panel,
                        exactly as the row's own `onClick` does. It lands in
                        this cell rather than around the row because an `<a>`
                        cannot wrap a `<tr>`; the row keeps its handler for
                        clicks anywhere else along it. */}
                    <ControlLink
                      href={taskDeepLink(task)}
                      onActivate={() => onSelect(task)}
                      className={task.completed_at ? "line-through opacity-60" : ""}
                    >
                      {task.title}
                    </ControlLink>
                  </td>
                  {showStatus ? (
                    <td className="px-3 py-2 text-muted-foreground">
                      {/* WS-27ad — the shared status pill, coloured by the
                          owner's stored colour / the status category. It used
                          to be a bare grey word while the same status on the
                          /tasks side was a coloured pill. */}
                      {status ? (
                        <StatusChip
                          accent={accentForStatus(status)}
                          label={status.name}
                        />
                      ) : (
                        "—"
                      )}
                    </td>
                  ) : null}
                  {showAssignees ? (
                    <td className="px-3 py-2 text-muted-foreground">
                      {task.assignees?.length ? (
                        <AvatarStack people={task.assignees} label={personLabel} />
                      ) : (
                        "—"
                      )}
                    </td>
                  ) : null}
                  <td className="px-3 py-2 text-muted-foreground">
                    <TaskMeta
                      chips={visibleChips(task, shownFields, undefined, tagHues)}
                    />
                  </td>
                </tr>
              );
            })}
            {/* WS-27y — the group's own capture box: a task added here lands
                in THIS group, pre-filled by `quickAddPrefill`. Folded away
                with the rows, exactly as /tasks folds its sections. */}
            {isFolded ? null : (
              <tr>
                <td colSpan={columnCount} className="px-3 py-1">
                  <QuickAdd
                    label={
                      groupBy === "none" ? "Add a task" : `Add to ${group.label}`
                    }
                    onAdd={(title) => quickAdd(title, group.key)}
                    className="max-w-md"
                  />
                </td>
              </tr>
            )}
          </tbody>
          );
        })}
      </table>
    </div>
  );
}
