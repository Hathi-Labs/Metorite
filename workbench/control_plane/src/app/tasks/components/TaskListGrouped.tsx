"use client";

import { CATEGORY_LABEL } from "@/lib/statusCategory";
import Icon from "@/components/Icon";
import { Checkbox } from "@/components/ui/Checkbox";
import { QuickAdd } from "@/components/QuickAdd";
import { useFlash } from "@/components/useFlash";
import { clampCursor, stepCursor } from "@/lib/cursor";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";
import { MyTask, ViewKey } from "../lib/types";
import { useTaskStore } from "../lib/taskStore";
import { TaskCard } from "./TaskCard";
import {
  applySort,
  byManualOrder,
  groupItems,
  type GroupBy,
  type TaskGroup,
} from "../lib/ordering";
import { categoryAccent, stageAccent } from "../lib/stageColors";
import {
  NEXT_CATEGORIES,
  isNextCategory,
  nextCategoryOf,
} from "../lib/statusCategory";
import { groupIcon } from "../lib/priorityIcons";
import {
  readColumnVisibility,
  subscribeColumns,
  visibleColumns,
  gridTemplate,
  DEFAULT_VISIBLE,
  type ColumnDef,
} from "../lib/columns";
import { quickAddPrefill } from "../lib/quickAdd";
import { ColumnHeader, ColumnCell } from "./ListColumns";
import { StatusPill } from "./StatusPill";

// A status-segmented list (Jira backlog style): rows grouped under collapsible
// headers with counts. In Manual sort the rows are drag-reorderable — within a
// group (reposition) and across groups (move to that status category). A field
// sort disables dragging (the sort would override the manual position),
// matching the board and how Jira/Linear behave.
//
// Grouping axis (D73.9):
//   next    → the Projects status CATEGORY: To do, In progress, Done. Each row
//             keeps its own lane name as its pill.
//   else → a single flat group (no status headers)
//
// Grouping is STATUS-ONLY and applies to Next Actions alone. @context is not a
// grouping axis here — it already drives the left sidebar. Waiting/Someday/etc.
// render flat for now (their own status model is a later workstream).

const UNSET = "—";

export function TaskListGrouped({
  items,
  view,
  groupBy = "",
}: {
  items: MyTask[];
  view: ViewKey;
  /** The grouping axis. "" (default) groups by STATUS (drag-reorderable stages).
   *  A lens ("priority" | "mode" | "energy" | "context") groups by that signal —
   *  read-only (you can't drag to change a computed attribute), but columns and
   *  multi-select still work. */
  groupBy?: GroupBy | "";
}) {
  const setStage = useTaskStore((s) => s.setStage);
  // D79 — a drop on a stage with two or more statuses asks which one. While
  // it asks, the row draws under the header it was dropped on.
  const stagePrompt = useTaskStore((s) => s.stagePrompt);
  const urgentWindowHours = useTaskStore((s) => s.settings.urgentWindowHours);
  const sort = useTaskStore((s) => s.sort);
  const reorderItem = useTaskStore((s) => s.reorderItem);
  const quickAddNext = useTaskStore((s) => s.quickAddNext);
  const openFocus = useTaskStore((s) => s.openFocus);
  // Multi-select. The checkbox is ALWAYS drawn, in its own gutter beside the
  // grip rather than over it — so selecting and dragging are two targets and
  // neither has to be switched off for the other (the mode this list used to
  // require is gone; see ItemList's note).
  const selectedIds = useTaskStore((s) => s.selectedIds);
  const toggleSelected = useTaskStore((s) => s.toggleSelected);
  const extendSelection = useTaskStore((s) => s.extendSelection);

  // Status grouping (the default): the drag-reorderable workflow-stage swimlanes.
  // A lens grouping (priority/mode/energy/context) is read-only swimlanes over
  // the same set — you can't drag to change a computed attribute. Both keep the
  // columns. Only Next Actions groups at all; other views are a single group.
  const isLens = groupBy !== "" && groupBy !== "none";
  const statusGrouped = view === "next" && !isLens;
  const grouped = view === "next"; // any grouping (status or lens) shows headers
  // Columnar list (desktop) applies to ALL Next-Actions groupings now — the same
  // aligned columns whether you group by status, priority, energy, etc. The
  // project view (explicit `stages`) keeps the simple stacked row. Mobile always
  // falls back to the stacked card (handled per-row via the sm: breakpoint).
  const columnVis = useSyncExternalStore(
    subscribeColumns,
    readColumnVisibility,
    () => DEFAULT_VISIBLE,
  );
  const columnar = view === "next";
  const cols = useMemo(
    () => (columnar ? visibleColumns(columnVis) : []),
    [columnar, columnVis],
  );
  const grid = useMemo(() => gridTemplate(cols), [cols]);
  // Drag-reorder is a manual-sort affordance on the STATUS axis only, and off
  // for a lens grouping (you can't drag to change a computed attribute). It is
  // no longer switched off while something is selected: the checkbox has its
  // own gutter, so the two gestures no longer compete for one.
  const manual = sort.field === "manual" && statusGrouped;

  const [dragId, setDragId] = useState<string | null>(null);
  // The drop target as "<groupKey>:<index>" so a highlight can mark the exact
  // gap the card would land in.
  const [dropAt, setDropAt] = useState<string | null>(null);

  // A lens grouping delegates to groupItems() (the shared slicer) — precomputed
  // once, since a lens can't reorder mid-render. Status grouping keys off the
  // stage each item resolves to. Non-next views are a single flat group.
  const lensGroups = useMemo<TaskGroup[]>(
    () =>
      isLens && view === "next"
        ? groupItems(items, groupBy as GroupBy, urgentWindowHours)
        : [],
    [isLens, view, items, groupBy, urgentWindowHours],
  );

  // A task whose category is not a Next group (backlog, triage, cancelled)
  // answers null and is not drawn under any header.
  const groupOf = useCallback(
    (i: MyTask): string | null =>
      !statusGrouped
        ? UNSET
        : stagePrompt?.taskId === i.id
          ? stagePrompt.stage
          : nextCategoryOf(i),
    [statusGrouped, stagePrompt],
  );

  const groups = useMemo(() => {
    if (isLens && view === "next") {
      return lensGroups.map((g) => ({ key: g.key, label: g.label, emoji: g.emoji }));
    }
    if (!statusGrouped) return [{ key: UNSET, label: "" }];
    return NEXT_CATEGORIES.map((c) => ({ key: c as string, label: CATEGORY_LABEL[c] }));
  }, [isLens, view, lensGroups, statusGrouped]);

  const byGroup = useMemo(() => {
    const m = new Map<string, MyTask[]>();
    for (const g of groups) m.set(g.key, []);
    if (isLens && view === "next") {
      // The lens slicer already bucketed the items; just sort within each group.
      for (const g of lensGroups) m.set(g.key, applySort(g.items, sort));
      return m;
    }
    for (const i of items) {
      const k = groupOf(i);
      if (k === null) continue;
      (m.get(k) ?? m.set(k, []).get(k)!).push(i);
    }
    // Order each group by the active sort (manual → sortKey; else the field).
    for (const [k, arr] of m) m.set(k, applySort(arr, sort));
    return m;
  }, [items, groups, groupOf, sort, isLens, view, lensGroups]);

  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggle = (k: string) =>
    setCollapsed((c) => {
      const next = new Set(c);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  // WS-27y backport: the keyboard cursor and the landing flash — the same
  // shared machinery the Projects list runs (`@/lib/cursor`, `useFlash`).
  const [cursor, setCursor] = useState(-1);
  const [anchor, setAnchor] = useState<number | null>(null);
  const { flash, attach, scrollTo, element } = useFlash();

  // The cursor's world: the rows in render order, skipping collapsed groups.
  const rows = useMemo(() => {
    const out: string[] = [];
    for (const g of groups) {
      if (collapsed.has(g.key)) continue;
      for (const i of byGroup.get(g.key) ?? []) out.push(i.id);
    }
    return out;
  }, [groups, byGroup, collapsed]);

  // Clamped at READ time rather than synced by an effect: the rows shrink
  // under the cursor on every reload, and a state write per reload is exactly
  // the cascading-render pattern the lint forbids.
  const cursorAt = clampCursor(rows.length, cursor);

  function onKeyDown(event: React.KeyboardEvent) {
    // A keystroke a control already consumed (a quick-add's Enter, a row's
    // own Enter-to-open) is not the cursor's; nor is typing in an input.
    if (event.defaultPrevented) return;
    if (
      (event.target as HTMLElement).closest(
        "input, textarea, select, [contenteditable=true]",
      )
    )
      return;
    // Shift+Arrow sweeps a range — the same gesture the /projects list has,
    // and now on the same terms: ungated. It used to work only after pressing
    // "Select", which meant the two apps disagreed about what Shift does while
    // both claimed to share `@/lib/selection`.
    const picked = selectedIds;
    const next = stepCursor(
      rows,
      { cursor: cursorAt, anchor, selection: picked },
      event.key,
      event.shiftKey,
    );
    if (!next) return;
    event.preventDefault();
    setCursor(next.cursor);
    setAnchor(next.anchor);
    if (next.selection !== picked) extendSelection([...next.selection]);
    if (next.open) openFocus(next.open);
    if (next.cursor >= 0) scrollTo(rows[next.cursor]);
  }

  // Group-context quick-add (shared QuickAdd + this app's prefill): a task
  // added under a group header is born a NEXT action IN that group, then
  // announces its landing with the same flash a drop gets. The computed
  // lenses (priority / mode) return null and never offer the box.
  const quickAdd = async (title: string, groupKey: string) => {
    const prefill = quickAddPrefill(statusGrouped ? "" : (groupBy as GroupBy), groupKey);
    if (!prefill) return;
    const id = quickAddNext(title, prefill);
    if (id) flash(id);
  };

  const onDrop = (groupKey: string, index: number) => {
    setDropAt(null);
    const id = dragId;
    setDragId(null);
    if (!id || !manual) return;
    const dest = byManualOrder(byGroup.get(groupKey) ?? []);
    // A drop across groups moves the task into that STAGE (`setStage`, D79):
    // one status there writes at once, two or more ask, anchored to the row.
    // The rank lands just before the status write, and not at all when the
    // member backs out of the question.
    const dragged = items.find((i) => i.id === id);
    const from = dragged ? nextCategoryOf(dragged) : null;
    const rank = () => reorderItem(id, dest, index);
    // The landed row scrolls into view and flashes (shared useFlash), so the
    // gesture visibly ends where the row now lives.
    flash(id);
    if (grouped && isNextCategory(groupKey) && groupKey !== from)
      void setStage(id, groupKey, { anchor: () => element(id), onLanded: rank });
    else rank();
  };

  const total = groups.length;

  return (
    <div
      tabIndex={0}
      onKeyDown={onKeyDown}
      aria-label="Task list — arrow keys move, Enter opens"
      className="flex-1 overflow-y-auto outline-none"
    >
      {/* Desktop column header row (Context list only). Hidden on mobile, where
          rows stay stacked. The left spacer matches a row's three gutters —
          checkbox (w-6) + grip (w-5) + expand (w-5) = 64px — so "Name" and the
          cells sit above their columns. All three are now drawn on every row,
          whatever the sort, which is what lets one fixed spacer be right. */}
      {columnar && cols.length > 0 && (
        <div className="sticky top-0 z-20 hidden border-b border-border bg-card/95 px-3.5 py-1.5 backdrop-blur sm:block">
          <div
            className="grid items-center gap-2"
            style={{ gridTemplateColumns: grid }}
          >
            <span className="pl-16 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Name
            </span>
            {cols.map((c) => (
              <ColumnHeader key={c.key} col={c} />
            ))}
          </div>
        </div>
      )}
      {groups.map((g, gi) => {
        // `groupRows`, not `rows` — the outer `rows` is the keyboard cursor's
        // whole-list world, and shadowing it here is how the cursor ring ends
        // up comparing against the wrong array.
        const groupRows = byGroup.get(g.key) ?? [];
        const isCollapsed = collapsed.has(g.key);
        const showHeader = grouped;
        // Status groups take their category's accent; a lens grouping uses a
        // plain neutral header + its own emoji.
        const accent = statusGrouped
          ? categoryAccent(g.key)
          : stageAccent(g.label || g.key, gi, total);
        const emoji = (g as { emoji?: string }).emoji;
        // A lens grouping by priority/mode gets the matching lucide icon; other
        // lenses (energy/context) fall back to their emoji marker if any.
        const LensIcon = isLens ? groupIcon(groupBy, g.key) : null;
        const isDone = statusGrouped && gi === total - 1;
        // Highlight the whole group while a card hovers anywhere over it, so a
        // cross-stage move reads clearly even before hitting a precise gap.
        const groupHot = dropAt?.startsWith(`${g.key}:`) ?? false;
        return (
          <section
            key={g.key}
            className={groupHot ? "bg-primary/[0.03]" : undefined}
          >
            {showHeader && (
              <div
                className={[
                  "sticky top-0 z-10 flex items-center gap-2 border-b border-border px-3 py-1.5 backdrop-blur",
                  statusGrouped ? `border-l-2 ${accent.soft} ${accent.bar}` : "bg-card/95",
                ].join(" ")}
              >
                <button
                  type="button"
                  onClick={() => toggle(g.key)}
                  className="tech-transition flex min-w-0 flex-1 items-center gap-2 text-left"
                >
                  <Icon name="ChevronRight"
                    className={[
                      "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
                      isCollapsed ? "" : "rotate-90",
                    ].join(" ")}
                  />
                  {statusGrouped ? (
                    <span className={`h-2 w-2 shrink-0 rounded-full ${accent.dot}`} />
                  ) : LensIcon ? (
                    <LensIcon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  ) : emoji ? (
                    <span aria-hidden className="shrink-0 text-xs">{emoji}</span>
                  ) : null}
                  <span
                    className={[
                      "truncate text-[11px] font-semibold uppercase tracking-wide",
                      statusGrouped ? accent.text : "text-foreground",
                    ].join(" ")}
                  >
                    {g.label}
                  </span>
                  <span
                    className={[
                      "shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold",
                      "bg-background/60 text-muted-foreground",
                    ].join(" ")}
                  >
                    {groupRows.length}
                  </span>
                  {isDone && (
                    <span className="shrink-0 rounded-full bg-success/15 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-success">
                      Done
                    </span>
                  )}
                </button>
              </div>
            )}
            {!isCollapsed && (
              <div>
                {groupRows.map((item, idx) => (
                  <DraggableRow
                    key={item.id}
                    item={item}
                    manual={manual}
                    selected={selectedIds.has(item.id)}
                    onToggleSelected={(shift) => toggleSelected(item.id, shift, rows)}
                    columns={cols}
                    grid={grid}
                    // The pill shows the task's own LANE name (D73.9). The group
                    // header is only its category, so the pill always carries
                    // news: "Building" and "In progress" share a header.
                    showStage
                    attachRef={attach(item.id)}
                    atCursor={cursorAt >= 0 && rows[cursorAt] === item.id}
                    isDropTarget={dropAt === `${g.key}:${idx}`}
                    onDragStart={() => setDragId(item.id)}
                    onDragEnd={() => {
                      setDragId(null);
                      setDropAt(null);
                    }}
                    onDragOverGap={() => setDropAt(`${g.key}:${idx}`)}
                    onDropGap={() => onDrop(g.key, idx)}
                  />
                ))}
                {/* trailing gap → drop at the end of the group. Taller when a
                    drag is active so an empty/short stage is an easy target. */}
                {manual && (
                  <div
                    onDragOver={(e) => {
                      if (!dragId) return;
                      e.preventDefault();
                      setDropAt(`${g.key}:${groupRows.length}`);
                    }}
                    onDrop={() => onDrop(g.key, groupRows.length)}
                    className={[
                      "transition-all",
                      dragId ? "h-6" : "h-2",
                      dropAt === `${g.key}:${groupRows.length}`
                        ? "border-t-2 border-primary bg-primary/10"
                        : "border-t-2 border-transparent",
                    ].join(" ")}
                  />
                )}
                {groupRows.length === 0 && showHeader && (
                  <p className="px-9 py-2.5 text-[11px] italic text-muted-foreground/50">
                    {dragId
                      ? "Drop here to move to this stage"
                      : statusGrouped
                        ? "No tasks in this stage"
                        : "No tasks in this group"}
                  </p>
                )}
                {/* WS-27y backport: the group's own capture box — a task
                    added here is born a NEXT action IN this group (shared
                    QuickAdd + lib/quickAdd prefill), and flashes where it
                    lands. The computed lenses (priority / mode) offer no box:
                    no create payload can promise the landing. */}
                {showHeader &&
                quickAddPrefill(statusGrouped ? "" : (groupBy as GroupBy), g.key) !== null ? (
                  <div className="px-9 py-1">
                    <QuickAdd
                      label={`Add to ${g.label || "this group"}`}
                      onAdd={(title) => quickAdd(title, g.key)}
                      className="max-w-md"
                    />
                  </div>
                ) : null}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

function DraggableRow({
  item,
  manual,
  selected,
  onToggleSelected,
  columns,
  grid,
  showStage,
  attachRef,
  atCursor,
  isDropTarget,
  onDragStart,
  onDragEnd,
  onDragOverGap,
  onDropGap,
}: {
  item: MyTask;
  manual: boolean;
  selected: boolean;
  /** `shift` extends the selection from the anchor (`@/lib/selection`). */
  onToggleSelected: (shift: boolean) => void;
  /** Visible desktop columns (empty → no columnar layout, stacked card only). */
  columns: ColumnDef[];
  /** grid-template-columns matching the header (only used when columns set). */
  grid: string;
  /** Show the card's status pill (off when the list is grouped by status). */
  showStage: boolean;
  /** useFlash registration — the landing flash / cursor scroll finds the row. */
  attachRef: (el: HTMLElement | null) => void;
  /** The keyboard cursor stands on this row (WS-27y backport). */
  atCursor: boolean;
  isDropTarget: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDragOverGap: () => void;
  onDropGap: () => void;
}) {
  // Jira/ClickUp-style nesting: a task with subtasks shows ONE row with an
  // expand chevron + a progress count; expanding lazily loads and reveals the
  // child subtasks (the actual next actions) indented beneath it.
  const [expanded, setExpanded] = useState(false);
  const hasSubtasks = (item.subtaskCount ?? 0) > 0;

  return (
    <div
      ref={attachRef}
      draggable={manual}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragOver={(e) => {
        if (!manual) return;
        e.preventDefault();
        onDragOverGap();
      }}
      onDrop={(e) => {
        if (!manual) return;
        e.preventDefault();
        onDropGap();
      }}
      className={[
        "group/row relative border-t-2 transition-colors",
        isDropTarget ? "border-primary" : "border-transparent",
        // The keyboard cursor's ring — same classes the Projects list draws
        // on its active row.
        atCursor ? "bg-muted/60 ring-2 ring-inset ring-ring" : "",
      ].join(" ")}
    >
      {/* a precise drop line that reads even over a dense row */}
      {isDropTarget && (
        <span className="pointer-events-none absolute -top-[3px] left-0 h-1 w-1.5 rounded-full bg-primary" />
      )}
      <div className={["flex items-stretch", selected ? "bg-primary/5" : ""].join(" ")}>
        {/* The checkbox is a SIBLING of the row content, in its own gutter —
            /projects' arrangement. It used to replace the grip gutter and only
            in select mode, which is why selecting and manual sort could not
            coexist. Two gutters, two gestures, and the row content beside them
            still opens the task. */}
        <label
          // The row above is `draggable`; this says the box itself is not a
          // drag handle. (Belt and braces — a press-and-release on a checkbox
          // ticks it in any case; this is about a press-and-drag on one, which
          // was never verified in a browser here.)
          draggable={false}
          className="flex w-6 shrink-0 cursor-pointer items-center justify-center"
        >
          <Checkbox
            checked={selected}
            onChange={(e) =>
              onToggleSelected((e.nativeEvent as MouseEvent).shiftKey)
            }
            aria-label={selected ? "Deselect task" : "Select task"}
          />
        </label>
        {/* Kept even when the sort is not manual, so the columns above stay
            over their cells instead of shifting 20px when the sort changes. */}
        <span
          className={[
            "flex w-5 shrink-0 items-center justify-center text-muted-foreground/25 transition-colors",
            manual
              ? "cursor-grab group-hover/row:text-muted-foreground/60 active:cursor-grabbing"
              : "",
          ].join(" ")}
        >
          {manual && <Icon name="GripVertical" className="h-3.5 w-3.5" />}
        </span>
        {/* expand toggle — only for parents; keeps a fixed-width gutter so all
            rows stay left-aligned whether or not they have subtasks. */}
        <span className="flex w-5 shrink-0 items-center justify-center">
          {hasSubtasks && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              aria-label={expanded ? "Collapse subtasks" : "Expand subtasks"}
              aria-expanded={expanded}
              className="tech-transition rounded p-0.5 text-muted-foreground/60 hover:bg-secondary hover:text-foreground"
            >
              <Icon name="ChevronRight"
                className={[
                  "h-3.5 w-3.5 transition-transform",
                  expanded ? "rotate-90" : "",
                ].join(" ")}
              />
            </button>
          )}
        </span>
        {/* The row content is the OPEN affordance, always — it no longer turns
            into a selection toggle behind a mode. Selecting is the checkbox. */}
        <div className="min-w-0 flex-1">
          <RowContent item={item} columns={columns} grid={grid} showStage={showStage} />
        </div>
      </div>
      {hasSubtasks && expanded && <SubtaskRows parent={item} />}
    </div>
  );
}

/** The row's body. With columns (desktop, Context view) it renders as an aligned
 *  grid — Name in the flexible track, then one cell per visible column. Mobile
 *  always falls back to the stacked TaskCard row (title + pills beneath), and so
 *  does the project view (no columns). */
function RowContent({
  item,
  columns,
  grid,
  showStage,
}: {
  item: MyTask;
  columns: ColumnDef[];
  grid: string;
  showStage: boolean;
}) {
  const urgentWindowHours = useTaskStore((s) => s.settings.urgentWindowHours);
  const openFocus = useTaskStore((s) => s.openFocus);
  if (columns.length === 0) {
    return <TaskCard item={item} variant="row" showStage={showStage} />;
  }
  return (
    <>
      {/* Mobile: the stacked card (title + wrapping pills) — clickable itself. */}
      <div className="sm:hidden">
        <TaskCard item={item} variant="row" showStage={showStage} />
      </div>
      {/* Desktop: aligned columns matching the header grid. The row opens the
          focus modal on click (Enter/Space too) — same affordance as the card,
          so the columnar list is clickable. Column cells that carry their own
          interactive controls stop propagation. */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => openFocus(item.id)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openFocus(item.id);
          }
        }}
        className="tech-transition hidden cursor-pointer items-center gap-2 py-2.5 pr-3.5 hover:bg-secondary/40 sm:grid"
        style={{ gridTemplateColumns: grid }}
      >
        {/* Name cell. When the list is grouped by a lens the section header no
            longer carries the stage, so the status pill rides in front of the
            title (right where the eye associates it with the task) instead of
            eating a fixed column and crushing the name. */}
        <span className="flex min-w-0 items-center gap-2">
          {showStage && <StatusPill item={item} />}
          <span className="min-w-0 truncate text-sm text-foreground">
            {item.title}
          </span>
        </span>
        {columns.map((c) => (
          <ColumnCell
            key={c.key}
            col={c}
            item={item}
            urgentWindowHours={urgentWindowHours}
          />
        ))}
      </div>
    </>
  );
}

// The lazily-loaded child subtasks of an expanded parent row. Each is the next
// physical action for finishing the parent; clicking opens it, and the leading
// dot toggles completion (a one-tap "did this step").
function SubtaskRows({ parent }: { parent: MyTask }) {
  const loadSubtasks = useTaskStore((s) => s.loadSubtasks);
  const openFocus = useTaskStore((s) => s.openFocus);
  const quickDispose = useTaskStore((s) => s.quickDispose);
  const [children, setChildren] = useState<MyTask[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    void loadSubtasks(parent.id).then((rows) => {
      if (!cancelled) setChildren(rows);
    });
    return () => {
      cancelled = true;
    };
    // Re-load when the parent's subtask count changes (added/removed elsewhere).
  }, [parent.id, parent.subtaskCount, loadSubtasks]);

  if (children === null) {
    return (
      <div className="flex items-center gap-2 py-2 pl-14 text-[11px] text-muted-foreground">
        <Icon name="Loader2" className="h-3 w-3 animate-spin" />
        Loading subtasks…
      </div>
    );
  }
  if (children.length === 0) {
    return (
      <p className="py-1.5 pl-14 text-[11px] italic text-muted-foreground/50">
        No subtasks.
      </p>
    );
  }
  return (
    <div className="border-l border-border/60 ml-[26px]">
      {children.map((c) => {
        const done = c.disposition === "DONE";
        return (
          <div
            key={c.id}
            className="tech-transition group/sub flex items-center gap-2 py-1.5 pl-4 pr-3.5 hover:bg-secondary/40"
          >
            <button
              type="button"
              onClick={() => quickDispose(c.id, done ? "NEXT" : "DONE")}
              aria-label={done ? "Mark not done" : "Mark done"}
              title={done ? "Mark not done" : "Mark done"}
              className="tech-transition shrink-0 text-muted-foreground/50 hover:text-success"
            >
              {done ? (
                <Icon name="CheckCircle2" className="h-4 w-4 text-success" />
              ) : (
                <Icon name="Circle" className="h-4 w-4" />
              )}
            </button>
            <Icon name="CornerDownRight" className="h-3 w-3 shrink-0 text-muted-foreground/30" />
            <button
              type="button"
              onClick={() => openFocus(c.id)}
              className={[
                "min-w-0 flex-1 truncate text-left text-sm",
                done
                  ? "text-muted-foreground line-through"
                  : "text-foreground hover:text-primary",
              ].join(" ")}
            >
              {c.nextAction || c.title}
            </button>
          </div>
        );
      })}
    </div>
  );
}
