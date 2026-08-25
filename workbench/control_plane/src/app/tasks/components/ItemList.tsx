"use client";

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import AppIcon, { themedIcon, type ThemedIcon } from "@/components/Icon";
import { allSelected } from "@/lib/selection";
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { useTaskStore, itemsForView } from "../lib/taskStore";
import { isUntagged } from "../lib/priority";
import { ViewKey } from "../lib/types";
import { isWaitingOverdue } from "../lib/waiting";
import { applyFilters, applySort, type GroupBy } from "../lib/ordering";
import { FlatList } from "./FlatList";
import { TaskBoard } from "./TaskBoard";
import { TaskListGrouped } from "./TaskListGrouped";
import { TaskToolbar } from "./TaskToolbar";
import { WaitingForView } from "./WaitingForView";

// View mode (list vs kanban board) for the processed-task views, sticky per
// browser via useSyncExternalStore (SSR-safe, same recipe as the inbox density
// toggle — avoids the useState(localStorage) hydration-mismatch bug).
const MODE_KEY = "cc.tasks.viewMode";
const modeListeners = new Set<() => void>();
function subscribeMode(cb: () => void) {
  modeListeners.add(cb);
  const onStorage = (e: StorageEvent) => { if (e.key === MODE_KEY) cb(); };
  window.addEventListener("storage", onStorage);
  return () => {
    modeListeners.delete(cb);
    window.removeEventListener("storage", onStorage);
  };
}
function readMode(): "list" | "board" {
  try {
    return window.localStorage.getItem(MODE_KEY) === "board" ? "board" : "list";
  } catch {
    return "list";
  }
}
function setModePersist(m: "list" | "board") {
  try { window.localStorage.setItem(MODE_KEY, m); } catch { /* private mode */ }
  modeListeners.forEach((cb) => cb());
}

const VIEW_META: Record<
  string,
  { title: string; icon: ThemedIcon; hint: string }
> = {
  inbox: { title: "Inbox", icon: themedIcon("Inbox"), hint: "Capture, then clarify each item to zero." },
  next: { title: "My Next Actions", icon: themedIcon("ListChecks"), hint: "Tasks assigned to you, grouped by status and sorted by priority — the very next physical step for each." },
  priority: { title: "Priority", icon: themedIcon("Target"), hint: "Your open work by the founder matrix — Founder Fire first, Eliminate last." },
  engage: { title: "Engage · Now", icon: themedIcon("Zap"), hint: "What you can pick up right now, matched to your energy." },
  waiting: { title: "Waiting For", icon: themedIcon("Clock"), hint: "Delegated or blocked on someone else." },
  calendar: { title: "Calendar", icon: themedIcon("Calendar"), hint: "Date-specific actions — the hard landscape." },
  someday: { title: "Someday / Maybe", icon: themedIcon("Lightbulb"), hint: "Incubating. Reviewed weekly." },
  done: { title: "Done", icon: themedIcon("CheckCircle2"), hint: "Completed tasks. They stay here until you archive them." },
  archive: { title: "Archive", icon: themedIcon("Archive"), hint: "Archived tasks — hidden from active views. Restore anytime." },
};

export function ItemList() {
  const items = useTaskStore((s) => s.items);
  const loading = useTaskStore((s) => s.loading);
  const view = useTaskStore((s) => s.selectedView);
  const context = useTaskStore((s) => s.selectedContext);
  const sourceFilter = useTaskStore((s) => s.sourceFilter);
  const filters = useTaskStore((s) => s.filters);
  const sort = useTaskStore((s) => s.sort);
  // ⚠️ Was `accounts.length > 0`. With the connectors retired (D52) no new
  // task can be SYNCED, but rows imported BEFORE the retirement still are —
  // so the source filter is offered when the loaded items actually contain
  // one, rather than when a workspace is connected. Same question, asked of
  // the data instead of a table that no longer has rows to answer with.
  const hasSynced = useTaskStore((s) =>
    s.items.some((i) => i.source === "SYNCED"),
  );
  const bulkArchive = useTaskStore((s) => s.bulkArchive);
  const requestDelete = useTaskStore((s) => s.requestDelete);
  const groupByChoice = useTaskStore((s) => s.groupBy);
  const mode = useSyncExternalStore(subscribeMode, readMode, () => "list");

  // Multi-select for bulk archive/restore/delete. Lifted into the store so it
  // works on every Next-Actions surface — the flat lists (Done/Waiting/…), the
  // status-grouped list, AND the Kanban board — and survives the list/board
  // toggle within a view. The inbox keeps its own selection UI.
  //
  // ── The divergence WS-27ad kept, and why it did not survive ─────────────
  // That slice left /tasks with a modal "Select" button and wrote the reason
  // here: a /tasks row is a TaskCard, the whole card is the open affordance, so
  // a permanent checkbox would either steal the drag-grip gutter the manual
  // sort needs or make one click mean two things.
  //
  // **Owner ruling, 2026-08-10: /projects is canonical and /tasks conforms** —
  // and the code shows the old reason was never structural. /projects solved
  // exactly this by putting the checkbox OUTSIDE the card, a sibling in the row
  // (`projects/components/TaskBoard.tsx`), so the card surface stays the open
  // affordance and the box is a second target. /tasks put it INSIDE, absolutely
  // positioned over the same corner as the drag grip — so the collision the old
  // comment predicted was one this app had built for itself.
  //
  // What that leaves: **`selectMode` is no longer a mode.** It is a derived
  // mirror of "something is selected" (see the store), it decides only whether
  // the bulk bar is up, and it never again changes what a click means. The
  // checkbox is always drawn, selection is always available, and shift-click /
  // Shift+Arrow extend through `@/lib/selection` — the grammar this app already
  // shared with /projects, now reachable without entering anything first.
  const selectedIds = useTaskStore((s) => s.selectedIds);
  const selectAllVisible = useTaskStore((s) => s.selectAllVisible);
  const pruneSelection = useTaskStore((s) => s.pruneSelection);
  const clearSelection = useTaskStore((s) => s.clearSelection);

  // The view's items (source/archive-filtered), then the toolbar's search/
  // context/assignee filter, then the active sort. `inView` is the pre-toolbar
  // set — used to populate the toolbar's context/assignee dropdowns so they
  // never offer an option that returns nothing.
  const inView = useMemo(
    () => itemsForView(items, view, context, sourceFilter),
    [items, view, context, sourceFilter],
  );
  // The legacy Priority view (no longer a sidebar entry, but still reachable in
  // code) forces the priority sort so its sections read rank-ordered; every
  // other view honours the toolbar sort.
  const visible = useMemo(() => {
    const effectiveSort =
      view === "priority" ? ({ field: "priority", dir: "asc" } as const) : sort;
    return applySort(applyFilters(inView, filters), effectiveSort);
  }, [inView, filters, sort, view]);
  // The ids the view is actually showing — what "select all" means, and what a
  // surviving selection is measured against. Derived here rather than in each
  // surface because every surface (grouped list, flat list, board, waiting)
  // renders this same set.
  const visibleIds = useMemo(() => visible.map((i) => i.id), [visible]);
  const allChecked = allSelected(selectedIds, visibleIds);
  // A selection that outlives its filter is how a bulk action hits rows nobody
  // can see any more (`@/lib/selection.prune` says it at length). /projects
  // prunes on every change of its visible set; this is the same effect.
  useEffect(() => {
    pruneSelection(visibleIds);
  }, [visibleIds, pruneSelection]);

  // Offer "auto-assign contexts" when Next Actions has context-less tasks (the
  // synced ClickUp tasks that never went through Clarify → @no context bucket).
  const contextlessCount = useMemo(
    () => (view === "next" ? inView.filter((i) => !i.context).length : 0),
    [view, inView],
  );
  // Waiting-For overdue = rows past `expectedBy` (spec §6) — the count behind
  // the "needs a nudge" badge. Real wall-clock is intentional (same reason the
  // calendar reads it live): this was `isOverdue(i, MOCK_NOW)` against a demo
  // constant frozen at 2026-06-30, so the badge drifted one more day off the
  // truth every day. Memoised so the render itself stays idempotent.
  const overdueCount = useMemo(() => {
    if (view !== "waiting") return 0;
    // eslint-disable-next-line react-hooks/purity
    const nowMs = Date.now();
    return visible.filter((i) => isWaitingOverdue(i, nowMs)).length;
  }, [view, visible]);

  const meta = VIEW_META[view] ?? VIEW_META.inbox;
  const Icon = meta.icon;
  // Overload guard: on the Priority view, how many tasks the matrix is only
  // *guessing* about (neither flag set) — so the user can tell judged tasks
  // from defaulted ones and triage them.
  const untaggedCount =
    view === "priority" ? visible.filter((i) => isUntagged(i)).length : 0;
  // The Kanban board is the Next Actions workflow board (columns = the global
  // workflow stages). Other views stay list-only until their own status model
  // is designed, so the List/Board toggle only appears on Next Actions.
  const boardable = view === "next";
  // The filter/sort toolbar rides above every processed-task view (inbox has
  // its own triage UI; projects is a different surface). Calendar/Archive get
  // it too — search/sort still help there — but they stay list-only.
  const showToolbar = view !== "inbox";
  // Next Actions is a single grouped list (TaskListGrouped) with columns; the
  // grouping AXIS comes from the toolbar group-by ("" = Status). Every axis —
  // status, priority, suggestion, energy, context — keeps the columns now (the
  // list handles them all). Other views render a flat list.
  const grouped = view === "next";
  const groupAxis: GroupBy | "" = grouped ? groupByChoice : "";
  // Bulk multi-select (archive / restore / delete) is offered on every
  // processed-task surface — the flat lists (Done/Waiting/…), the Next-Actions
  // grouped list, AND the Kanban board — so you can check off a batch and
  // archive/delete it anywhere. Only the inbox (its own triage UI) and calendar
  // opt out.
  const isBoard = boardable && mode === "board";
  const bulkSelectable = view !== "calendar";
  const isArchiveView = view === "archive";
  // Waiting For gets its own body (grouped by WHO, with the overdue/stale
  // flags §6 asks for) instead of the flat card stack. This file's header,
  // toolbar, filters and bulk-select chrome still wrap it — but the swap is a
  // TRADE, not a free addition, and the rows pay for it:
  //   - a WaitingRow is not a TaskCard, so it has no ContextMenu — the
  //     per-row task actions (Schedule / Change stage / Mark as Done /
  //     Eliminate, wired via useCardActions in TaskCard) are gone here;
  //   - so are the card's chips: stage pill, project, due date, attachments,
  //     subtask count, energy.
  // What it buys is the who / what / since-when grouping this view exists for
  // (§1 line 46) — the axis is a PERSON, and per-task controls are noise on a
  // "chase Sai about all of it" list. Clicking a row still opens the focus
  // pane over the same editable TaskDetail. What a waiting row should afford
  // INLINE is an open design question, deliberately not answered here.
  // Sort is suppressed on this view for the same reason the rows differ:
  // WaitingForView re-derives the order (see TaskToolbar's showSort).
  const isWaitingView = view === "waiting";

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border bg-card px-4 py-3">
        {/* flex-wrap: on narrow (mobile) widths the controls drop to their own
            row instead of crushing the title into a two-line break. */}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
          <Icon className="h-4 w-4 shrink-0 text-primary" />
          <h1 className="whitespace-nowrap text-base font-bold text-foreground">
            {meta.title}
            {context && (
              <span className="ml-2 font-mono text-sm font-normal text-primary/80">
                {context}
              </span>
            )}
          </h1>
          {/* List ⇄ Board view mode toggle (Jira-style). Sticky per browser. */}
          {boardable && (
            <div className="ml-auto flex items-center gap-0.5 rounded-md border border-border bg-background p-0.5">
              <button
                type="button"
                onClick={() => setModePersist("list")}
                aria-pressed={mode === "list"}
                title="List view"
                className={[
                  "tech-transition inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium",
                  mode === "list"
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                ].join(" ")}
              >
                <AppIcon name="LayoutList" className="h-3 w-3" />
                List
              </button>
              <button
                type="button"
                onClick={() => setModePersist("board")}
                aria-pressed={mode === "board"}
                title="Board view"
                className={[
                  "tech-transition inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium",
                  mode === "board"
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                ].join(" ")}
              >
                <AppIcon name="Columns3" className="h-3 w-3" />
                Board
              </button>
            </div>
          )}
          {/* The source toggle lives in the sidebar (governs every view). When
              it's narrowed, show a small chip here so the active scope is
              obvious on this page too. */}
          {hasSynced && sourceFilter !== "all" && (
            <span
              className={[
                "inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary",
                boardable ? "ml-2" : "ml-auto",
              ].join(" ")}
              title="Filtered by source — change it in the sidebar"
            >
              {sourceFilter === "local" ? (
                <AppIcon name="HardDrive" className="h-3 w-3" />
              ) : (
                <AppIcon name="Cloud" className="h-3 w-3" />
              )}
              {sourceFilter === "local" ? "Mine" : "ClickUp"}
            </span>
          )}
          {hasSynced && contextlessCount > 0 && (
            <ContextBackfillButton count={contextlessCount} />
          )}
          {/* Select-all — /projects' header checkbox (`TaskList.tsx`), which
              /tasks had no equivalent of at all. It selects what the view is
              SHOWING (filtered, not the whole store) and unticks to nothing
              when everything already is. This is not the old "Select" button
              wearing a checkbox: selection needs no entry point now, every row
              carries its own box, and this is the shortcut. */}
          {bulkSelectable && visible.length > 0 && (
            <label
              title="Select every task this view is showing"
              className={[
                "inline-flex cursor-pointer items-center gap-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground",
                boardable ||
                (hasSynced && sourceFilter !== "all") ||
                (hasSynced && contextlessCount > 0)
                  ? "ml-2"
                  : "ml-auto",
              ].join(" ")}
            >
              <input
                type="checkbox"
                checked={allChecked}
                onChange={() => selectAllVisible(visibleIds)}
                aria-label="Select every task this view is showing"
                className="h-3.5 w-3.5 accent-primary"
              />
              Select all
            </label>
          )}
          <span
            className={
              (bulkSelectable && visible.length > 0) ||
              boardable ||
              (hasSynced && sourceFilter !== "all") ||
              (hasSynced && contextlessCount > 0)
                ? "ml-2 text-xs text-muted-foreground"
                : "ml-auto text-xs text-muted-foreground"
            }
          >
            {visible.length} item{visible.length === 1 ? "" : "s"}
          </span>
        </div>
        <p className="mt-0.5 text-[11px] text-muted-foreground">{meta.hint}</p>
        {view === "waiting" && overdueCount > 0 && (
          <p className="mt-1 inline-flex items-center gap-1 text-[11px] font-medium text-destructive">
            <AppIcon name="AlertTriangle" className="h-3 w-3" />
            {overdueCount} overdue — needs a nudge
          </p>
        )}
        {untaggedCount > 0 && (
          <p className="mt-1 inline-flex items-center gap-1 text-[11px] text-muted-foreground/80">
            <AppIcon name="AlertTriangle" className="h-3 w-3" />
            {untaggedCount} not yet judged — in{" "}
            <span className="font-medium">Eliminate</span> by default until you
            flag them important or leveraged.
          </p>
        )}
      </header>

      {showToolbar && !loading && inView.length > 0 && (
        <TaskToolbar items={inView} />
      )}

      {/* The bulk bar — TOP-mounted, directly above the rows it acts on, where
          /projects puts it (`projects/components/BulkBar.tsx`: `border-b` +
          `bg-muted`, built from Button/Badge). It was bottom-mounted here with
          hand-rolled `<button>`s, which is AGENTS.md rule 3 and invisible to
          the conformance suite — those regexes only catch solid-fill chrome,
          and these were outline buttons. Appears only when something is
          selected: a bar of controls that mostly do nothing is a bar people
          learn to ignore. */}
      {selectedIds.size > 0 && (
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border bg-muted px-3 py-2">
          <Badge tone="primary">{selectedIds.size} selected</Badge>
          {isArchiveView ? (
            <Button
              variant="secondary"
              size="sm"
              icon="ArchiveRestore"
              onClick={() => {
                bulkArchive([...selectedIds], false);
                clearSelection();
              }}
            >
              Restore
            </Button>
          ) : (
            <Button
              variant="secondary"
              size="sm"
              icon="Archive"
              onClick={() => {
                bulkArchive([...selectedIds], true);
                clearSelection();
              }}
            >
              Archive
            </Button>
          )}
          <Button
            variant="destructive"
            size="sm"
            icon="Trash2"
            onClick={() => {
              requestDelete([...selectedIds]);
              clearSelection();
            }}
          >
            Delete
          </Button>
          <Button variant="ghost" size="sm" icon="X" onClick={clearSelection}>
            Clear
          </Button>
          {/* Why this bar is shorter than /projects', and is not unfinished:
              its status / assignee / tags / priority controls drive ONE
              endpoint, `POST /projects/tasks/bulk`, which resolves each field
              per task and reports per-task refusals. /tasks has no counterpart
              — `/items/bulk` takes a disposition and `/items/bulk-archive` an
              archive flag, and that is the whole bulk surface; `gtd_items` has
              no tags column at all (its nearest axis, @context, is single
              valued). So archive/restore/delete is the honest set. Widening it
              is a gateway ticket, not a bar redesign. */}
        </div>
      )}

      {loading ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
          <AppIcon name="Loader2" className="h-6 w-6 animate-spin text-muted-foreground/60" />
          <p className="text-xs text-muted-foreground">Loading…</p>
        </div>
      ) : visible.length === 0 ? (
        inView.length > 0 ? (
          <NoMatchState />
        ) : (
          <EmptyState view={view} />
        )
      ) : isWaitingView ? (
        // "Who owes me what, since when" — the one view whose organising axis
        // is a PERSON rather than a stage (spec §1 line 46, §6).
        <WaitingForView items={visible} />
      ) : isBoard ? (
        // The Kanban board (drag-to-refile). ⚠️ The board's cards still draw
        // their checkbox only while `selectMode` is true, and `selectMode` is
        // now derived from "something is selected" — so on the board the FIRST
        // pick comes from Select all (or from a list surface) until the card
        // gains its permanent box the way /projects' board card has one. That
        // move belongs to `TaskCard`/`TaskBoard`, not to this file.
        <div className="min-h-0 flex-1">
          <TaskBoard items={visible} view={view} />
        </div>
      ) : grouped ? (
        // The one grouped list — columns + multi-select on EVERY grouping axis
        // (status / priority / suggestion / energy / context). Drag-reorder is
        // enabled only on the Status axis (you can't drag to change a computed
        // attribute); the list handles that internally.
        <TaskListGrouped items={visible} view={view} groupBy={groupAxis} />
      ) : (
        // WS-27ad — the flat views (Done / Someday / Archive / Engage /
        // Priority). Lifted into their own component when they gained the
        // shared cursor, flash and per-view quick-add: they were the last
        // /tasks surfaces where the arrow keys did nothing.
        <FlatList items={visible} view={view} showPriority={view === "priority"} />
      )}
    </div>
  );
}

/** Shown when the view has items but the toolbar filters hid them all — a
 *  different message from the true-empty state so the user knows to clear. */
function NoMatchState() {
  const clearFilters = useTaskStore((s) => s.clearFilters);
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
      <p className="text-sm text-muted-foreground">No tasks match your filters.</p>
      <button
        type="button"
        onClick={clearFilters}
        className="tech-transition rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground hover:bg-secondary"
      >
        Clear filters
      </button>
    </div>
  );
}

/** Auto-assign @context to the actionable tasks that have none — the synced
 *  ClickUp tasks that arrive context-less. One tap runs the assistant over them
 *  and re-pulls, so they move out of the "@no context" bucket. */
function ContextBackfillButton({ count }: { count: number }) {
  const backfill = useTaskStore((s) => s.backfillContext);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  return (
    <button
      type="button"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        setDone(null);
        try {
          const res = await backfill();
          setDone(res.updated > 0 ? `Set ${res.updated}` : "None to set");
        } catch {
          setDone("Failed");
        } finally {
          setBusy(false);
        }
      }}
      title="Let the assistant assign @context to tasks that have none"
      className="tech-transition ml-auto inline-flex items-center gap-1.5 rounded-md border border-primary/30 bg-primary/5 px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/10 disabled:opacity-50"
    >
      {busy ? (
        <AppIcon name="Loader2" className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <AppIcon name="Sparkles" className="h-3.5 w-3.5" />
      )}
      {done ?? `Assign context · ${count}`}
    </button>
  );
}

function EmptyState({ view }: { view: ViewKey }) {
  const msg =
    view === "inbox"
      ? "Inbox zero. Mind like water."
      : view === "waiting"
        ? "Nothing on your Waiting-For list."
        : view === "next"
          ? "No next actions assigned to you."
          : "Nothing here yet.";
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
      <AppIcon name="CheckCircle2" className="h-8 w-8 text-success/70" />
      <p className="text-sm text-muted-foreground">{msg}</p>
    </div>
  );
}
