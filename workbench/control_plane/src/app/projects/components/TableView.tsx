"use client";

/**
 * Projects · the spreadsheet layout (WS-27x).
 *
 * The same task set as the board and the list, read from the same endpoint —
 * one row per task, columns = the view's shown fields (plus Title), which is
 * the same `shown_fields` set the chip gate reads, so the table and the cards
 * can never disagree about what a view surfaces.
 *
 * Every cell edit drives the EXISTING write paths: `PATCH /tasks/{id}` for
 * status, dates, priority and custom fields, `PUT …/assignees` for people —
 * the same validation, the same `field_change` activity and the same revert
 * as an edit typed into the panel. There is no table-only write path.
 *
 * Sorting is the server's (`lib/table.nextSort` maps a header click onto the
 * gateway's `TASK_SORTS` keys — status is the WS-27w semantic sort); the page
 * owns the fetch, so the sort state lives there and arrives as a prop.
 *
 * Sub-tasks nest indented under their parent when both are on the page
 * (`lib/table.treeRows`); collapse state is local. The keyboard walks a CELL
 * cursor (`lib/tableCursor`) — arrows move the ring, Enter edits (or opens
 * the panel where a cell has no editor), Esc cancels back to cursor mode.
 * Every group ends in the WS-27y quick-add, pre-filled with the group's value.
 */

import { ControlLink } from "@/components/ControlLink";
import Icon from "@/components/Icon";
import { StatusChip } from "@/components/StatusChip";
import { AvatarStack } from "@/components/TaskMeta";
import { Input } from "@/components/ui/Input";
import { useToast } from "@/components/ui/Toast";
import { Checkbox } from "@/components/ui/Checkbox";
import { durationLabel } from "@/lib/taskCard";
import { useMemo, useRef, useState } from "react";

import { accentForStatus } from "../lib/accent";
import type { FieldRow, StatusRow, TaskRow } from "../lib/api";
import { projectsApi } from "../lib/api";
import { parseAssignees } from "../lib/assignees";
import { sortForView } from "../lib/board";
import { taskDeepLink, taskRef } from "../lib/card";
import { type FieldDef, displayValue, toInput, toWire } from "../lib/customFields";
import { type GroupBy, type TaskGroup, UNSET, personLabel } from "../lib/grouping";
import { dueInstantForDay, quickAddPrefill } from "../lib/quickAdd";
import {
  IMPORTANCE_OPTIONS,
  type TableColumn,
  type TableSort,
  customKeyOf,
  importanceLabel,
  nextSort,
  tableColumns,
  treeRows,
} from "../lib/table";
import { NO_CELL, clampCell, stepCell } from "../lib/tableCursor";
import { QuickAdd } from "./QuickAdd";
import { useFlash } from "./useFlash";

const SELECT =
  "cc-control w-full rounded-lg border border-border bg-background px-2 py-1 " +
  "text-xs text-foreground outline-none focus:border-primary/50";

/** The columns whose cells open an editor on Enter (or a click). */
const EDITABLE = new Set(["status", "assignees", "due_at", "start_date", "importance"]);

function isEditable(column: TableColumn): boolean {
  if (EDITABLE.has(column.key)) return true;
  // Custom fields: everything but multi_select, whose chips need more room
  // than a cell — it stays read-only here, editable from the panel.
  return Boolean(column.def && column.def.field_type !== "multi_select");
}

interface Props {
  groups: TaskGroup[];
  groupBy: GroupBy;
  statuses: StatusRow[];
  /** WS-27l — the root's custom field definitions, for custom columns. */
  fields: FieldRow[];
  /** WS-27x — the view's shown fields; columns are derived from it. */
  shownFields: readonly string[];
  /** Header sort — held by the page, which owns the fetch it drives. */
  sort: TableSort | null;
  onSort: (next: TableSort | null) => void;
  /** Where a quick-added task is created (the selected node). */
  projectId: string;
  onCreated: (task: TaskRow) => void;
  /** A cell edit landed — merge the fresh row into the page's task list. */
  onSaved: (task: TaskRow) => void;
  onSelect: (task: TaskRow) => void;
}

export function TableView({
  groups,
  groupBy,
  statuses,
  fields,
  shownFields,
  sort,
  onSort,
  projectId,
  onCreated,
  onSaved,
  onSelect,
}: Props) {
  // WS-27ak(3) — a cell edit on a spreadsheet is the mutation furthest from
  // wherever the one inline error line is drawn; see `saveCell`.
  const toast = useToast();
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [cell, setCell] = useState(NO_CELL);
  const [error, setError] = useState<string | null>(null);
  const gridRef = useRef<HTMLDivElement | null>(null);
  const { flash, attach, scrollTo } = useFlash();

  const statusById = useMemo(
    () => new Map(statuses.map((s) => [s.id, s])),
    [statuses]
  );

  const columns = useMemo(
    () => tableColumns(shownFields, fields as FieldDef[]),
    [shownFields, fields]
  );
  // Title is always drawn — a table of chips with no names is not a table.
  const colCount = 1 + columns.length;

  // Sections in render order. With a header sort active the server already
  // ordered the page and that order is kept; otherwise each group falls back
  // to the view's own hand-arranged order, exactly as the list does.
  const sections = useMemo(
    () =>
      groups
        .filter((group) => group.tasks.length > 0)
        .map((group) => ({
          key: group.key,
          label: group.label,
          rows: treeRows(sort ? group.tasks : sortForView(group.tasks), collapsed),
        })),
    [groups, sort, collapsed]
  );

  // The cursor's world: every rendered row, in render order.
  const flatRows = useMemo(
    () => sections.flatMap((section) => section.rows),
    [sections]
  );

  // Clamped at READ time rather than synced by an effect — the rows and
  // columns both shrink under the cursor (reloads, a field hidden mid-flight).
  const at = clampCell(flatRows.length, colCount, cell);

  const total = groups.reduce((sum, group) => sum + group.tasks.length, 0);

  function toggleCollapse(taskId: string) {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  }

  function closeEditor() {
    setCell((current) => ({ ...current, editing: false }));
    gridRef.current?.focus();
  }

  /**
   * WS-27ak(3) — the second call site of the toast primitive.
   *
   * A table is where the gap was widest: the edit happens in a cell that may
   * be a thousand pixels from the single inline error line this component
   * draws, and on success the only signal was a row flash that is gone in
   * 600ms and absent entirely if the row scrolled.
   *
   * Keyed on the TASK, so editing three cells of one row in a row mutates one
   * toast rather than stacking three — and editing three different rows still
   * says three things, because they are three facts.
   *
   * ⚠️ `setError` stays; see `TaskPanel.changeStatus` for the argument.
   */
  async function saveCell(task: TaskRow, patch: Record<string, unknown>) {
    setError(null);
    try {
      const fresh = await toast.promise(projectsApi.patchTask(task.id, patch), {
        key: `projects:task-edit:${task.id}`,
        loading: "Saving…",
        success: (row) => `Saved “${row.title}”`,
        error: "Couldn't save that edit",
        errorAction: { label: "Retry", onClick: () => void saveCell(task, patch) },
      });
      onSaved(fresh);
      flash(task.id);
    } catch (err) {
      setError(String((err as Error).message));
    }
    closeEditor();
  }

  async function saveAssignees(task: TaskRow, raw: string) {
    setError(null);
    try {
      const result = await projectsApi.setAssignees(task.id, parseAssignees(raw));
      onSaved({ ...task, assignees: result.assignees });
      flash(task.id);
    } catch (err) {
      setError(String((err as Error).message));
    }
    closeEditor();
  }

  async function quickAdd(title: string, groupKey: string) {
    const plan = quickAddPrefill(groupBy, groupKey);
    const created = await projectsApi.createTask({
      project_id: projectId,
      title,
      ...plan.create,
    });
    if (plan.assignees?.length) {
      // Best-effort, as on the list: the task exists; a failed PUT leaves it
      // honestly unassigned rather than inviting a duplicate-creating retry.
      try {
        await projectsApi.setAssignees(created.id, plan.assignees);
      } catch {
        /* the table will show where it actually landed */
      }
    }
    flash(created.id);
    onCreated(created);
  }

  function onKeyDown(event: React.KeyboardEvent) {
    // Keystrokes inside an editor (or the quick-add) belong to it; the cell
    // editors surrender only Esc, via their own handlers.
    if (
      (event.target as HTMLElement).closest(
        "input, textarea, select, [contenteditable=true]"
      )
    )
      return;
    if (event.key === "Enter" && at.row >= 0 && !at.editing) {
      const column = at.col === 0 ? null : columns[at.col - 1];
      if (!column || !isEditable(column)) {
        // A cell with no editor: Enter opens the task, like the list's row
        // cursor would.
        event.preventDefault();
        onSelect(flatRows[at.row].task);
        return;
      }
    }
    const next = stepCell(flatRows.length, colCount, at, event.key);
    if (!next) return;
    event.preventDefault();
    setCell(next);
    if (next.row >= 0 && next.row !== at.row) scrollTo(flatRows[next.row].task.id);
  }

  function header(column: TableColumn) {
    if (!column.sortKey) {
      return <span>{column.label}</span>;
    }
    const active = sort?.key === column.sortKey;
    return (
      <button
        type="button"
        onClick={() => onSort(nextSort(sort, column.sortKey))}
        aria-label={`Sort by ${column.label}`}
        title={
          active
            ? sort?.dir === "asc"
              ? `Sorted by ${column.label}, ascending — click for descending`
              : `Sorted by ${column.label}, descending — click to clear`
            : `Sort by ${column.label}`
        }
        className="inline-flex items-center gap-1 hover:text-foreground"
      >
        {column.label}
        <Icon
          name={active ? (sort?.dir === "asc" ? "ArrowUp" : "ArrowDown") : "ChevronsUpDown"}
          size={12}
          className={active ? "text-foreground" : "opacity-50"}
          aria-hidden
        />
      </button>
    );
  }

  function readOnlyCell(task: TaskRow, column: TableColumn): React.ReactNode {
    switch (column.key) {
      case "status": {
        // WS-27ad — the shared pill, not a bare word. Same fact, same
        // appearance, whichever of the two apps you are reading it in.
        const status = statusById.get(task.status_id);
        return status ? (
          <StatusChip accent={accentForStatus(status)} label={status.name} />
        ) : (
          "—"
        );
      }
      case "assignees":
        return task.assignees?.length ? (
          <AvatarStack people={task.assignees} label={personLabel} />
        ) : (
          "—"
        );
      case "due_at":
        return task.due_at ? new Date(task.due_at).toLocaleDateString() : "—";
      case "start_date":
        // A floating DATE — shown as stored, never routed through `new
        // Date()`, which would move it a day west of Greenwich (see api.ts).
        return task.start_date ? task.start_date.slice(0, 10) : "—";
      case "importance":
        return importanceLabel(task.importance) || "—";
      case "subtasks": {
        const counts = task.subtasks;
        return counts && counts.total > 0 ? `${counts.done}/${counts.total}` : "—";
      }
      case "blocked":
        return task.blocked_by_count ? String(task.blocked_by_count) : "—";
      case "tags":
        return task.tags?.length ? task.tags.join(", ") : "—";
      case "attachments":
        // Counted only on the single-task read (WS-27i); the list rows this
        // table draws honestly do not know.
        return "—";
      case "estimate":
        return durationLabel(task.estimate_mins) || "—";
      case "created_at":
        return task.created_at ? new Date(task.created_at).toLocaleDateString() : "—";
      default: {
        const key = customKeyOf(column.key);
        if (!key || !column.def) return "—";
        return displayValue(column.def, (task.custom_fields ?? {})[key]);
      }
    }
  }

  function editorCell(task: TaskRow, column: TableColumn): React.ReactNode {
    if (column.key === "status") {
      return (
        <select
          autoFocus
          aria-label={`Status of ${task.title}`}
          className={SELECT}
          defaultValue={task.status_id}
          onChange={(e) => void saveCell(task, { status_id: e.target.value })}
          onBlur={closeEditor}
          onKeyDown={(e) => {
            if (e.key === "Escape") closeEditor();
          }}
        >
          {statuses.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      );
    }
    if (column.key === "importance") {
      return (
        <select
          autoFocus
          aria-label={`Priority of ${task.title}`}
          className={SELECT}
          defaultValue={task.importance == null ? "" : String(task.importance)}
          onChange={(e) =>
            void saveCell(task, {
              importance: e.target.value === "" ? null : Number(e.target.value),
            })
          }
          onBlur={closeEditor}
          onKeyDown={(e) => {
            if (e.key === "Escape") closeEditor();
          }}
        >
          {IMPORTANCE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      );
    }
    if (column.key === "assignees") {
      return (
        <CellText
          label={`Assignees of ${task.title}`}
          defaultValue={(task.assignees ?? []).join(", ")}
          placeholder="email or agent:name, comma-separated"
          onCommit={(value) => void saveAssignees(task, value)}
          onCancel={closeEditor}
        />
      );
    }
    if (column.key === "due_at") {
      return (
        <CellText
          label={`Due date of ${task.title}`}
          type="date"
          defaultValue={task.due_at ? task.due_at.slice(0, 10) : ""}
          onCommit={(value) =>
            // Noon local, the calendar quick-add's rule (`dueInstantForDay`),
            // so the chosen day survives every viewer's timezone.
            void saveCell(task, { due_at: value ? dueInstantForDay(value) : null })
          }
          onCancel={closeEditor}
        />
      );
    }
    if (column.key === "start_date") {
      return (
        <CellText
          label={`Start date of ${task.title}`}
          type="date"
          defaultValue={task.start_date ? task.start_date.slice(0, 10) : ""}
          // A floating DATE: sent as the bare `YYYY-MM-DD`, never an instant.
          onCommit={(value) => void saveCell(task, { start_date: value || null })}
          onCancel={closeEditor}
        />
      );
    }
    const key = customKeyOf(column.key);
    const def = column.def;
    if (!key || !def) return readOnlyCell(task, column);
    const stored = (task.custom_fields ?? {})[key];
    if (def.field_type === "boolean") {
      return (
        <Checkbox
          autoFocus
          aria-label={`${def.name} of ${task.title}`}
          defaultChecked={stored === true}
          onChange={(e) =>
            void saveCell(task, { custom_fields: { [key]: e.target.checked } })
          }
          onBlur={closeEditor}
          onKeyDown={(e) => {
            if (e.key === "Escape") closeEditor();
          }}
        />
      );
    }
    if (def.field_type === "select") {
      return (
        <select
          autoFocus
          aria-label={`${def.name} of ${task.title}`}
          className={SELECT}
          defaultValue={String(toInput("select", stored))}
          onChange={(e) =>
            void saveCell(task, {
              custom_fields: { [key]: toWire("select", e.target.value) },
            })
          }
          onBlur={closeEditor}
          onKeyDown={(e) => {
            if (e.key === "Escape") closeEditor();
          }}
        >
          <option value="">— not set —</option>
          {def.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      );
    }
    return (
      <CellText
        label={`${def.name} of ${task.title}`}
        type={
          def.field_type === "number"
            ? "number"
            : def.field_type === "date"
              ? "date"
              : def.field_type === "url"
                ? "url"
                : "text"
        }
        defaultValue={String(toInput(def.field_type, stored))}
        onCommit={(value) =>
          void saveCell(task, {
            custom_fields: { [key]: toWire(def.field_type, value) },
          })
        }
        onCancel={closeEditor}
      />
    );
  }

  if (total === 0) {
    return (
      <div className="p-6">
        <p className="mb-2 text-sm text-muted-foreground">No tasks here yet.</p>
        {/* No group to land in, so the UNSET sentinel: every axis maps it to
            the empty plan, and the server assigns the default status. */}
        <QuickAdd
          label="Add a task"
          onAdd={(title) => quickAdd(title, UNSET)}
          className="max-w-md"
        />
      </div>
    );
  }

  return (
    <div
      ref={gridRef}
      tabIndex={0}
      onKeyDown={onKeyDown}
      className="overflow-x-auto outline-none"
      aria-label="Task table — arrow keys move the cell cursor, Enter edits, Esc cancels"
    >
      {error ? (
        <p className="border-b border-border bg-muted px-3 py-2 text-xs text-foreground">
          {error}
        </p>
      ) : null}
      <table className="w-full min-w-[640px] text-sm">
        <thead className="border-b border-border text-left text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">
              {header({ key: "title", label: "Title", sortKey: "title" })}
            </th>
            {columns.map((column) => (
              <th key={column.key} className="px-3 py-2 font-medium">
                {header(column)}
              </th>
            ))}
          </tr>
        </thead>
        {sections.map((section, sectionIndex) => {
          // This section's first row's index in the cursor's flat world.
          const base = sections
            .slice(0, sectionIndex)
            .reduce((sum, s) => sum + s.rows.length, 0);
          return (
            <tbody key={section.key}>
              {groupBy === "none" ? null : (
                <tr className="bg-muted">
                  <th
                    colSpan={colCount}
                    className="px-3 py-1.5 text-left text-xs font-medium text-foreground"
                  >
                    {section.label}
                    <span className="ml-2 font-normal text-muted-foreground">
                      {section.rows.length}
                    </span>
                  </th>
                </tr>
              )}
              {section.rows.map((row, rowIndex) => {
                const task = row.task;
                const flatIndex = base + rowIndex;
                const cursorHere = at.row === flatIndex;
                return (
                  <tr
                    key={task.id}
                    ref={attach(task.id)}
                    className={`border-b border-border last:border-0 hover:bg-muted ${
                      cursorHere ? "bg-muted/60" : ""
                    }`}
                  >
                    <td
                      onClick={() => {
                        setCell({ row: flatIndex, col: 0, editing: false });
                        onSelect(task);
                      }}
                      className={`cursor-pointer px-3 py-2 text-foreground ${
                        cursorHere && at.col === 0 ? "ring-2 ring-inset ring-ring" : ""
                      }`}
                    >
                      <span
                        className="flex items-center gap-1"
                        style={{ paddingLeft: `${row.depth * 1.25}rem` }}
                      >
                        {row.childCount > 0 ? (
                          <button
                            type="button"
                            aria-label={
                              collapsed.has(task.id)
                                ? `Expand ${row.childCount} sub-tasks of ${task.title}`
                                : `Collapse sub-tasks of ${task.title}`
                            }
                            aria-expanded={!collapsed.has(task.id)}
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleCollapse(task.id);
                            }}
                            className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                          >
                            <Icon
                              name={
                                collapsed.has(task.id) ? "ChevronRight" : "ChevronDown"
                              }
                              size={12}
                              aria-hidden
                            />
                          </button>
                        ) : row.depth > 0 ? (
                          <Icon
                            name="CornerDownRight"
                            size={12}
                            className="shrink-0 text-muted-foreground"
                            aria-hidden
                          />
                        ) : null}
                        <span className="text-muted-foreground">
                          {taskRef(task) ?? ""}
                        </span>
                        {/* WS-27al(1) — a real `<a href>` on the title, so
                            cmd/ctrl/shift/middle-click open the task in a new
                            tab. A plain click is intercepted and does exactly
                            what the cell's own handler does: park the cursor
                            here and open the panel. It cannot delegate to that
                            handler by bubbling, because a modified click must
                            NOT open the panel as well as the tab. */}
                        <ControlLink
                          href={taskDeepLink(task)}
                          onActivate={() => {
                            setCell({ row: flatIndex, col: 0, editing: false });
                            onSelect(task);
                          }}
                          className={task.completed_at ? "line-through opacity-60" : ""}
                        >
                          {task.title}
                        </ControlLink>
                      </span>
                    </td>
                    {columns.map((column, columnIndex) => {
                      const col = columnIndex + 1;
                      const here = cursorHere && at.col === col;
                      const editing = here && at.editing && isEditable(column);
                      return (
                        <td
                          key={column.key}
                          onClick={() => {
                            if (editing) return;
                            setCell({
                              row: flatIndex,
                              col,
                              editing: isEditable(column),
                            });
                          }}
                          className={`px-3 py-2 text-muted-foreground ${
                            isEditable(column) ? "cursor-pointer" : ""
                          } ${here ? "ring-2 ring-inset ring-ring" : ""}`}
                        >
                          {editing ? editorCell(task, column) : readOnlyCell(task, column)}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
              {/* WS-27y machinery, per the ticket: a task added here lands in
                  THIS group, pre-filled by `quickAddPrefill`. */}
              <tr>
                <td colSpan={colCount} className="px-3 py-1">
                  <QuickAdd
                    label={groupBy === "none" ? "Add a task" : `Add to ${section.label}`}
                    onAdd={(title) => quickAdd(title, section.key)}
                    className="max-w-md"
                  />
                </td>
              </tr>
            </tbody>
          );
        })}
      </table>
    </div>
  );
}

/**
 * A one-line text-ish cell editor: Enter commits, Esc cancels, blur commits
 * (leaving a half-typed edit behind silently is worse than saving it).
 * Uncontrolled — the draft dies with the editor, which is what Esc means.
 */
function CellText({
  label,
  defaultValue,
  type = "text",
  placeholder,
  onCommit,
  onCancel,
}: {
  label: string;
  defaultValue: string;
  type?: string;
  placeholder?: string;
  onCommit: (value: string) => void;
  onCancel: () => void;
}) {
  const cancelled = useRef(false);
  return (
    <Input
      autoFocus
      inputSize="sm"
      type={type}
      aria-label={label}
      defaultValue={defaultValue}
      placeholder={placeholder}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          onCommit((e.target as HTMLInputElement).value);
        } else if (e.key === "Escape") {
          cancelled.current = true;
          onCancel();
        }
      }}
      onBlur={(e) => {
        if (cancelled.current) return;
        // Only a real change commits — tabbing through an untouched cell
        // must not write a PATCH and post a timeline entry for nothing.
        if (e.target.value !== defaultValue) onCommit(e.target.value);
        else onCancel();
      }}
    />
  );
}
