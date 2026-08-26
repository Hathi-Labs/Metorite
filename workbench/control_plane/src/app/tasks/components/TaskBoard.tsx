"use client";

import { DropGap } from "@/components/DropGap";
import { QuickAdd } from "@/components/QuickAdd";
import { useFlash } from "@/components/useFlash";
import { gapKey } from "@/lib/boardDrop";
import { clampCursor, stepCursor } from "@/lib/cursor";
import { Fragment, useCallback, useMemo, useState } from "react";
import { GtdItem, ViewKey } from "../lib/types";
import { useTaskStore } from "../lib/taskStore";
import { TaskCard } from "./TaskCard";
import { dropRefusal } from "../lib/dropRules";
import { applySort, byManualOrder, statusColumnForItem } from "../lib/ordering";
import { quickAddPrefill } from "../lib/quickAdd";
import { stageAccent } from "../lib/stageColors";
import { formatStatus } from "../lib/utils";

// A Kanban board over the Next Actions items (Jira/ClickUp-style). Columns are
// the user's 4 FIXED workflow stages (settings.workflowStages) — not the raw
// ClickUp statuses. A LOCAL card keys off its `workflowStage`; a SYNCED card off
// its ClickUp `providerStatus` translated through the status→stage MAP, so many
// upstream statuses collapse into one clean stage. Dragging a card writes the
// mapped status back to ClickUp (per task's project). (@context is a card chip,
// not a column.) Dropping on the LAST stage marks the task DONE (backend). The
// per-PROJECT view passes explicit `stages` (that project's real ClickUp
// statuses) and bypasses the map. Fixed columns — empty stages still show.
//
// Cards render in manual (sortKey) order within a column and are drag-
// reorderable: a drop computes a fractional rank between its new neighbours
// (reorderItem), and a cross-column drop ALSO re-files the stage in the same
// write. A field sort disables reordering (the sort overrides manual position).
// Native HTML5 DnD, no extra deps.
//
// The board is only offered for Next Actions (see ItemList `boardable`); other
// views render list-only until their own status model is designed.
//
// ── WS-27ad · what this board shares, and the one thing it does not ────────
// Shared with /projects' board: the keyboard cursor and its Shift-sweep
// (`@/lib/cursor`), the selection grammar (`@/lib/selection`), the landing
// flash (`@/components/useFlash`), the group-context quick-add
// (`@/components/QuickAdd`), the column accent (`@/lib/statusAccent` via
// lib/stageColors), the card shell (`@/components/TaskCardShell`) and — as of
// this ticket, in the other direction — the drop-gap reorder
// (`@/components/DropGap` + `@/lib/boardDrop`), which /projects lacked.
//
// NOT shared: /projects' SWIMLANES (a second grouping axis drawn as rows).
// Deliberate, and the reason is this app's data rather than effort: /tasks'
// board axis is the fixed workflow-stage set, and its other axes are COMPUTED
// projections — priority and mode come from important × leveraged × urgent-from-
// dueAt, which no drop can write (`lib/quickAdd` refuses a quick-add on them for
// the same reason, and `lib/dropRules` refuses the drag). A lane grid here would
// be a grid most of whose cells refuse every gesture. If /tasks ever grows a
// second SETTABLE axis, this is the note to delete.

const NOBODY: ReadonlySet<string> = new Set();

export function TaskBoard({
  items,
  stages,
}: {
  items: GtdItem[];
  view: ViewKey;
  /** Explicit ordered column set (e.g. a project's own ClickUp statuses). When
   *  omitted, the columns are the union of the global local workflow stages and
   *  the connected tools' statuses (so ClickUp tasks land in their real stage,
   *  not all in the first column). */
  stages?: string[];
}) {
  const workflowSettingStages = useTaskStore((s) => s.settings.workflowStages);
  const statusStageMap = useTaskStore((s) => s.settings.statusStageMap);
  const sort = useTaskStore((s) => s.sort);
  const reorderItem = useTaskStore((s) => s.reorderItem);
  const updateItem = useTaskStore((s) => s.updateItem);
  const quickAddNext = useTaskStore((s) => s.quickAddNext);
  const openFocus = useTaskStore((s) => s.openFocus);
  // Multi-select for bulk archive/delete — works right on the board.
  //
  // S1: a card is no longer a selection toggle. Its checkbox is a permanent
  // sibling of the card (see `TaskCard`), so clicking a card always opens it and
  // `selectMode` no longer changes what a click MEANS. What it still does here
  // is suppress the drag: `lib/dropRules.dropRefusal` treats select mode as a
  // refusal with its own user-facing copy, so making cards draggable while that
  // stands would offer a gesture every column then refuses. Un-gating the drag
  // is a change to `dropRules` and the bulk bar together, not to this file
  // alone — recorded here rather than half-done.
  const selectMode = useTaskStore((s) => s.selectMode);
  const selectedIds = useTaskStore((s) => s.selectedIds);
  const toggleSelected = useTaskStore((s) => s.toggleSelected);
  const extendSelection = useTaskStore((s) => s.extendSelection);

  // Columns: an explicit stage set (the per-project view's real ClickUp
  // statuses) or the user's 4 fixed workflow stages. A LOCAL task keys off its
  // `workflowStage`; a SYNCED task off its ClickUp status through the status→
  // stage map (or the project view's own statuses, where the map is a no-op).
  const stageKeys = useMemo(
    () => stages ?? workflowSettingStages,
    [stages, workflowSettingStages],
  );
  // In the project view (explicit `stages`) grouping is by raw status, so the
  // map is bypassed; on the global board it translates the ClickUp status.
  const effectiveMap = useMemo(
    () => (stages ? {} : statusStageMap),
    [stages, statusStageMap],
  );
  // Manual drag-reorder is a sort affordance; suppressed while multi-selecting.
  const manual = sort.field === "manual" && !selectMode;
  const [dragId, setDragId] = useState<string | null>(null);
  const [overCol, setOverCol] = useState<string | null>(null);
  // Exact gap "<colKey>:<index>" the card would drop into (manual mode only).
  const [dropAt, setDropAt] = useState<string | null>(null);
  // WS-27y backport: the keyboard cursor and the landing flash — the same
  // shared machinery the Projects board runs (`@/lib/cursor`, `useFlash`).
  const [cursor, setCursor] = useState(-1);
  const [anchor, setAnchor] = useState<number | null>(null);
  const { flash, attach, scrollTo } = useFlash();

  // An unstaged task sits in the FIRST column of the axis.
  const firstStage = stageKeys[0];
  const stageOf = useCallback(
    (i: GtdItem): string =>
      statusColumnForItem(i, stageKeys, firstStage, effectiveMap),
    [stageKeys, firstStage, effectiveMap],
  );

  const columns = useMemo(
    () => stageKeys.map((s) => ({ key: s, label: s })),
    [stageKeys],
  );

  const byColumn = useMemo(() => {
    const m = new Map<string, GtdItem[]>();
    for (const c of columns) m.set(c.key, []);
    for (const i of items) {
      const k = stageOf(i);
      (m.get(k) ?? m.set(k, []).get(k)!).push(i);
    }
    // Cards within a column follow the active sort (manual → sortKey order).
    for (const [k, arr] of m) m.set(k, applySort(arr, sort));
    return m;
  }, [items, columns, stageOf, sort]);

  // The keyboard cursor's world: every card in render order (column by
  // column), same as the Projects board walks its lanes.
  const rows = useMemo(() => {
    const out: string[] = [];
    for (const c of columns) for (const i of byColumn.get(c.key) ?? []) out.push(i.id);
    return out;
  }, [columns, byColumn]);

  // Clamped at READ time rather than synced by an effect: the rows shrink
  // under the cursor on every reload, and a state write per reload is exactly
  // the cascading-render pattern the lint forbids.
  const cursorAt = clampCursor(rows.length, cursor);

  function onKeyDown(event: React.KeyboardEvent) {
    // A keystroke a control already consumed (a quick-add's Enter, a card's
    // own Enter-to-open) is not the cursor's; nor is typing in an input.
    if (event.defaultPrevented) return;
    if (
      (event.target as HTMLElement).closest(
        "input, textarea, select, [contenteditable=true]",
      )
    )
      return;
    // WS-27ad — Shift+Arrow sweeps a range, exactly as the /projects board
    // does, but ONLY inside select mode. Outside it there is no selection on
    // screen and no bulk bar, so a shift-arrow would grow a set nobody can
    // see; the mode is what makes the gesture legible here (see the note on
    // `selectMode` in ItemList).
    const picked = selectMode ? selectedIds : NOBODY;
    const next = stepCursor(
      rows,
      { cursor: cursorAt, anchor, selection: picked },
      event.key,
      selectMode && event.shiftKey,
    );
    if (!next) return;
    event.preventDefault();
    setCursor(next.cursor);
    setAnchor(next.anchor);
    if (next.selection !== picked) extendSelection([...next.selection]);
    if (next.open) openFocus(next.open);
    if (next.cursor >= 0) scrollTo(rows[next.cursor]);
  }

  // WS-27y backport: dragging is always offered; a refused target explains
  // itself while the card hovers (`lib/dropRules`), instead of the old
  // silent snap-back.
  const dragged = dragId ? items.find((i) => i.id === dragId) : undefined;
  const refusalFor = (colKey: string): string | null =>
    dragged
      ? dropRefusal({
          selectMode,
          sortField: sort.field,
          sameColumn: stageOf(dragged) === colKey,
        })
      : null;

  // Refile depends on the axis:
  //  • Global board (columns = local STAGES): set `workflowStage` for both
  //    LOCAL and SYNCED. For a synced task the backend translates the stage into
  //    that task's own ClickUp status via the status→stage map and writes it
  //    back; if nothing maps, the move stays local (workflowStage override).
  //  • Project view (columns = the project's raw ClickUp STATUSES): set
  //    `providerStatus` directly (back-syncs), as before.
  const refileFor = (colKey: string, id: string | null) => {
    const it = id ? items.find((i) => i.id === id) : undefined;
    if (!it) return undefined;
    if (stages) {
      // Project view: raw ClickUp status axis.
      return it.source === "LOCAL"
        ? { workflowStage: colKey }
        : { providerStatus: colKey };
    }
    // Global board: local stage axis (backend maps synced → ClickUp status).
    return { workflowStage: colKey };
  };

  // Drop onto a specific gap (index) within a column — reorder + re-file.
  // The landed card scrolls into view and flashes (shared useFlash), so the
  // gesture visibly ends where the card now lives.
  const dropAtIndex = (colKey: string, index: number) => {
    setDropAt(null);
    setOverCol(null);
    const id = dragId;
    setDragId(null);
    if (!id) return;
    const dest = byManualOrder(byColumn.get(colKey) ?? []);
    flash(id);
    reorderItem(id, dest, index, refileFor(colKey, id));
  };

  // Drop anywhere in a column (not on a card gap): keep the old semantics —
  // in a field sort we can't rank, so just re-file the stage/status. The
  // refused case (same column, field sort) already explained itself via the
  // hover overlay; here it simply does nothing.
  const dropColumn = (colKey: string) => {
    setOverCol(null);
    setDropAt(null);
    const id = dragId;
    setDragId(null);
    if (!id) return;
    const item = items.find((i) => i.id === id);
    if (!item) return;
    if (manual) {
      // append to the end of the column
      const dest = byManualOrder(byColumn.get(colKey) ?? []);
      flash(id);
      reorderItem(id, dest, dest.length, refileFor(colKey, id));
      return;
    }
    if (stageOf(item) === colKey) return; // refused — the overlay said why
    const refile = refileFor(colKey, id);
    if (refile) {
      flash(id);
      updateItem(id, refile);
    }
  };

  // Group-context quick-add (shared QuickAdd + this app's prefill): a task
  // added at a column's foot is born a NEXT action IN that stage, then
  // announces its landing with the same flash a drop gets.
  const quickAdd = async (title: string, colKey: string) => {
    const id = quickAddNext(title, quickAddPrefill("", colKey) ?? {});
    if (id) flash(id);
  };

  return (
    <div
      tabIndex={0}
      onKeyDown={onKeyDown}
      aria-label="Task board — arrow keys move, Enter opens"
      className="flex h-full gap-3 overflow-x-auto p-4 outline-none"
    >
      {columns.map((col, ci) => {
        const colItems = byColumn.get(col.key) ?? [];
        const isOver = overCol === col.key;
        const refusal = isOver && dragged ? refusalFor(col.key) : null;
        const accent = stageAccent(col.label || col.key, ci, columns.length);
        return (
          <div
            key={col.key}
            onDragOver={(e) => {
              // preventDefault even when refusing — the browser must keep
              // sending events or the overlay could never show; the refusal
              // is enforced in the drop handlers, and the cursor says "no"
              // via dropEffect.
              e.preventDefault();
              e.dataTransfer.dropEffect = refusalFor(col.key) ? "none" : "move";
              setOverCol(col.key);
            }}
            onDragLeave={() => setOverCol((c) => (c === col.key ? null : c))}
            onDrop={() => dropColumn(col.key)}
            className={[
              // S1: the column's chrome is /projects' — `rounded-lg border
              // border-border bg-card`, where this board drew `rounded-xl` on a
              // `bg-secondary/30` well. Both radii are themed here (globals.css
              // derives the whole `--radius-*` scale from `--radius`, and
              // `--radius-xl` IS `--radius`), so this is a convergence, not a
              // theming fix: two boards side by side drew the same object at two
              // corner radii on two surfaces. The drop highlight stays —
              // /projects has no equivalent, and a column that does not react
              // while a card hovers over it reads as a refusal.
              "relative flex h-full w-72 shrink-0 flex-col overflow-hidden rounded-lg border bg-card",
              isOver ? "border-primary bg-primary/5" : "border-border",
            ].join(" ")}
          >
            {/* WS-27y backport: the refusal, said on the target while the
                card hovers — same overlay grammar as the Projects board. */}
            {refusal ? (
              <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-background/85 p-3 text-center text-xs font-medium text-destructive">
                {refusal}
              </div>
            ) : null}
            {/* accent cap so each stage column is identifiable at a glance */}
            <div className={`h-1 w-full ${accent.dot}`} />
            <div
              className={[
                "flex items-center justify-between gap-2 border-b border-border px-3 py-2",
                accent.soft,
              ].join(" ")}
            >
              <span className="flex min-w-0 items-center gap-1.5">
                <span className={`h-2 w-2 shrink-0 rounded-full ${accent.dot}`} />
                <span className={`truncate text-xs font-semibold ${accent.text}`}>
                  {/* Per-project columns ARE raw ClickUp statuses — show them
                      title-cased like the tool. The global board's columns are
                      the user's own workflow-stage names, left as typed. */}
                  {stages ? formatStatus(col.label) : col.label}
                </span>
              </span>
              <span className="shrink-0 rounded-full bg-background/60 px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
                {colItems.length}
              </span>
            </div>
            {/* S1: `space-y-1` on the column, /projects' gutter, rather than a
                `mb-2` on each card — the gap belongs to the list, and putting
                it on the child left the drop gaps spaced differently from the
                cards they sit between. Cards and gaps are siblings here for the
                same reason they are on /projects: `space-y-*` only reaches
                direct children, so a card wrapped in a div of its own opts out
                of the spacing the column just set. */}
            <div className="flex flex-1 flex-col space-y-1 overflow-y-auto p-2">
              {colItems.map((i, idx) => (
                <Fragment key={i.id}>
                  {/* drop gap ABOVE this card (manual reorder) */}
                  {manual && (
                    <DropGap
                      active={dropAt === gapKey(col.key, idx)}
                      onOver={() => dragId && setDropAt(gapKey(col.key, idx))}
                      onDrop={() => dropAtIndex(col.key, idx)}
                    />
                  )}
                  <TaskCard
                    item={i}
                    // S1: the cursor ring and the flash target are the CARD's,
                    // handed to the shared shell through the card. This board
                    // used to wrap every card in a div and re-draw `ring-2
                    // ring-ring` on it — a second implementation of a prop the
                    // shell already had, and the wrapper is what made the ring
                    // sit a pixel off the card's own radius.
                    innerRef={attach(i.id)}
                    atCursor={cursorAt >= 0 && rows[cursorAt] === i.id}
                    draggable={!selectMode}
                    selected={selectedIds.has(i.id)}
                    // The column IS the stage here — a per-card status pill
                    // would just repeat it, so it's off on the board.
                    showStage={false}
                    onToggleSelected={(shift) => toggleSelected(i.id, shift, rows)}
                    onDragStart={() => setDragId(i.id)}
                    onDragEnd={() => { setDragId(null); setOverCol(null); setDropAt(null); }}
                  />
                </Fragment>
              ))}
              {/* trailing gap → drop at the end */}
              {manual && colItems.length > 0 && (
                <DropGap
                  active={dropAt === gapKey(col.key, colItems.length)}
                  onOver={() => dragId && setDropAt(gapKey(col.key, colItems.length))}
                  onDrop={() => dropAtIndex(col.key, colItems.length)}
                />
              )}
              {colItems.length === 0 && (
                <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-border/60 py-6 text-[11px] text-muted-foreground/60">
                  {isOver ? "Drop here" : "Empty"}
                </div>
              )}
            </div>
            {/* WS-27y backport: the column's own capture box — a task added
                here is born a NEXT action in THIS stage (shared QuickAdd +
                lib/quickAdd prefill), and flashes where it lands. */}
            <div className="p-2 pt-0">
              <QuickAdd
                label={`Add to ${stages ? formatStatus(col.label) : col.label}`}
                onAdd={(title) => quickAdd(title, col.key)}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
