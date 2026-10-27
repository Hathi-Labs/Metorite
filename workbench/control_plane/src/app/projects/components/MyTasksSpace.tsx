"use client";

/**
 * Projects · My tasks, the roll-up space (WS-64 OP-11, `one_projects_app.md`
 * §9 OP-11).
 *
 * My tasks looks and behaves like a project space, and it is not one. It has
 * the project tabs (Overview, Board, List, Table, Calendar), the Needs
 * attention lens and a Reports entry. A row of chips (All, Today, Next,
 * Waiting, Someday, Done) filters every tab.
 *
 * It mounts the SAME canvases a project mounts, through their props: no
 * canvas is copied. The data is the board's, over one definition of "mine":
 *
 * - the list pages through `GET /projects/tasks?scope=mine&with_overlay=1`,
 *   as a board does. The task store's whole-list hydrate does NOT run here
 *   (§9 OP-11, server note 11). It stays for "My task inbox" until OP-5.
 * - the Calendar reads `GET /projects/calendar?scope=mine`.
 * - the Overview reads `GET /projects/my/summary` and the four Analytics
 *   reads with `subject=me`. The Outlook refuses a subject, so it is not read.
 * - the lens reads `GET /projects/needs-attention?mine=true`.
 *
 * There are no statuses, fields, settings or grants here: My tasks owns none.
 * A quick add files to the member's personal root, where it reads as INBOX.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import { LayoutBoundary } from "@/components/LayoutBoundary";
import { type ModeOption, ModeSwitch } from "@/components/ModeSwitch";
import ScrollStrip from "@/components/ScrollStrip";
import Button from "@/components/ui/Button";
import { SkeletonBoard, SkeletonRows } from "@/components/ui/Skeleton";
import { useCachedResource } from "@/lib/useCachedResource";
import { filenameFromDisposition, saveCsv } from "@/lib/export";

import {
  type NodeSummary,
  type StatusRow,
  type TaskRow,
  attentionApi,
  projectsApi,
  projectsKey,
} from "../lib/api";
import { type AttentionPage, LENS_LABEL } from "../lib/needsAttention";
import { EMPTY_FILTERS, type Filters, type GroupBy, NO_LANES, type BoardLanes, type SubtaskMode, groupTasks, toQuery } from "../lib/grouping";
import { DEFAULT_SHOWN } from "../lib/shownFields";
import { appendTasks, nextTaskPage } from "../lib/paging";
import { loadedOfTotal } from "../lib/compactChrome";
import { type CalendarLayout, calendarGrid, calendarWindow, dayKey, shiftGrid } from "../lib/calendar";
import { EXPORT_FILENAME, exportPath } from "../lib/export";
import { useLiveRefresh } from "../lib/liveRefresh";
import type { PositionWrite } from "../lib/board";
import {
  MY_TASKS_CHIPS,
  MY_TASKS_MAX_PAGES,
  MY_TASKS_MODES,
  type MyTasksChip,
  type MyTasksMode,
  chipCount,
  chipNeedsEveryPage,
  chipRows,
  droppedStage,
  mineStatuses,
  myTasksQuery,
  stageDrop,
  stageGroups,
} from "../lib/myTasks";
import { myDayLink } from "../lib/reportEntry";
import { FilterChrome } from "./CompactFilterBar";
import { CalendarView } from "./CalendarView";
import { MoreTasksBar } from "./MoreTasksBar";
import NodeDashboard from "./NodeDashboard";
import { NeedsAttentionList } from "./NeedsAttentionList";
import { TableView } from "./TableView";
import { TaskBoard } from "./TaskBoard";
import { TaskList } from "./TaskList";

const MODE_OPTIONS: readonly ModeOption<MyTasksMode>[] = MY_TASKS_MODES;

/** What the page holds for My tasks: the tab, the lens and the chip. */
export interface MyTasksView {
  mode: MyTasksMode;
  attention: boolean;
  chip: MyTasksChip;
}

export const DEFAULT_MY_TASKS_VIEW: MyTasksView = { mode: "list", attention: false, chip: "all" };

/**
 * The read of the My tasks summary. The page reads it for the sidebar counts
 * and this space reads it for the Overview and the chips. One cache key, so
 * the two share one request.
 */
export function useMySummary(enabled: boolean) {
  return useCachedResource<NodeSummary>(
    enabled ? projectsKey("my/summary") : null,
    () => projectsApi.mySummary(),
  );
}

/**
 * The action row of My tasks, under the page's title row: the tabs, the Needs
 * attention lens and Reports on the left, Capture on the right. The same
 * controls and classes as a project's action row.
 */
export function MyTasksActionRow({
  view,
  onView,
  attentionCount,
  onReport,
  onCapture,
}: {
  view: MyTasksView;
  onView: (next: MyTasksView) => void;
  attentionCount: number | null;
  onReport: (href: string) => void;
  onCapture: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1 px-3 pb-2 pt-1.5" data-my-tasks-tabs="">
      <ModeSwitch
        modes={MODE_OPTIONS}
        mode={view.attention ? null : view.mode}
        layout="toolbar"
        onPick={(mode) => onView({ ...view, mode, attention: false })}
      />
      <Button
        variant="ghost"
        size="sm"
        icon="TriangleAlert"
        selected={view.attention}
        data-attention-toggle=""
        title={view.attention ? "Back to My tasks" : "Show my work that stopped and waits for a person"}
        onClick={() => onView({ ...view, attention: !view.attention })}
      >
        {LENS_LABEL}
        {attentionCount ? <span className="ml-1 tabular-nums opacity-70">{attentionCount}</span> : null}
      </Button>
      <Button
        variant="ghost"
        size="sm"
        icon="FileText"
        title="My day: the report about my own work"
        onClick={() => onReport(myDayLink())}
      >
        Reports
      </Button>
      <div className="ml-auto flex shrink-0 items-center gap-1">
        <Button variant="secondary" size="sm" icon="Plus" onClick={onCapture}>
          Capture
        </Button>
      </div>
    </div>
  );
}

/** The chip row: All, Today, Next, Waiting, Someday and Done, with server counts. */
export function MyTasksChips({
  chip,
  summary,
  onChip,
}: {
  chip: MyTasksChip;
  summary: NodeSummary | null | undefined;
  onChip: (next: MyTasksChip) => void;
}) {
  return (
    <div className="border-b border-border px-3 py-1.5" data-my-tasks-chips="">
      <ScrollStrip label="My tasks lists" className="gap-1">
        {MY_TASKS_CHIPS.map((entry) => {
          const count = chipCount(summary, entry.id);
          return (
            <Button
              key={entry.id}
              variant="ghost"
              size="sm"
              selected={chip === entry.id}
              aria-pressed={chip === entry.id}
              data-chip={entry.id}
              onClick={() => onChip(entry.id)}
            >
              {entry.label}
              {count ? <span className="ml-1 tabular-nums opacity-70">{count}</span> : null}
            </Button>
          );
        })}
      </ScrollStrip>
    </div>
  );
}

interface Props {
  view: MyTasksView;
  onView: (next: MyTasksView) => void;
  /** A phone, with the compact chrome. The tabs then live in the View sheet. */
  phone: boolean;
  compactChrome: boolean;
  me: string;
  people: readonly string[];
  personLabels?: ReadonlyMap<string, string>;
  /** The path of a project, for the chip on each row. */
  projectPath: (projectId: string) => readonly string[];
  /** Where a quick add files: the member's personal root, or null. */
  personalRootId: string | null;
  /** Opens the ONE Projects task panel over My tasks. */
  onOpenTask: (task: TaskRow) => void;
  onOpenTaskId: (taskId: string) => void;
  /** An Overview row: open that space or project, filtered to my tasks. */
  onOpenProject: (projectId: string) => void;
  onReport: (href: string) => void;
  /** Bumped by the page when the panel changed a task, so the rows read again. */
  version: number;
}

const NO_ROWS: TaskRow[] = [];

export default function MyTasksSpace({
  view,
  onView,
  phone,
  compactChrome,
  me,
  people,
  personLabels,
  projectPath,
  personalRootId,
  onOpenTask,
  onOpenTaskId,
  onOpenProject,
  onReport,
  version,
}: Props) {
  const { mode, chip, attention } = view;
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [groupBy, setGroupBy] = useState<GroupBy>("category");
  const [lanes, setLanes] = useState<BoardLanes>(NO_LANES);
  const [subtasks, setSubtasks] = useState<SubtaskMode>("separate");
  const [shownFields, setShownFields] = useState<string[]>([...DEFAULT_SHOWN]);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const again = useCallback(() => setReload((n) => n + 1), []);
  useLiveRefresh(again);

  // ── The summary, the Overview and the chips ────────────────────────────
  const summary = useMySummary(true);
  const today = summary.data?.today ?? dayKey(new Date());
  const overview = mode === "overview" && !attention;
  const stuck = useCachedResource(overview ? projectsKey("analytics/stuck", { subject: "me" }) : null, () =>
    projectsApi.stuck(undefined, "me"),
  );
  const load = useCachedResource(overview ? projectsKey("analytics/load", { subject: "me" }) : null, () =>
    projectsApi.load(undefined, "me"),
  );
  const throughput = useCachedResource(
    overview ? projectsKey("analytics/throughput", { subject: "me" }) : null,
    () => projectsApi.throughput(undefined, undefined, "me"),
  );
  const finished = useCachedResource(
    overview ? projectsKey("analytics/finished", { subject: "me" }) : null,
    () => projectsApi.finished(undefined, undefined, "me"),
  );

  // ── The rows: paged like a board, every page while a chip filters ──────
  const listMode = !attention && (mode === "list" || mode === "board" || mode === "table");
  const filterQuery = useMemo(() => toQuery(filters), [filters]);
  const queryKey = JSON.stringify(filterQuery);
  const wantsAll = chipNeedsEveryPage(chip);
  const [list, setList] = useState<{ key: string; rows: TaskRow[]; total: number | null }>({
    key: "",
    rows: NO_ROWS,
    total: null,
  });
  const [loadingMore, setLoadingMore] = useState(false);
  const readSeq = useRef(0);
  useEffect(() => {
    if (!listMode) return;
    const seq = ++readSeq.current;
    void (async () => {
      try {
        let rows: TaskRow[] = [];
        let total: number | null = null;
        for (let page = 1; page <= (wantsAll ? MY_TASKS_MAX_PAGES : 1); page++) {
          const res = await projectsApi.tasks(myTasksQuery(filterQuery, page));
          if (seq !== readSeq.current) return;
          rows = appendTasks(rows, res.rows ?? []);
          total = res.total ?? rows.length;
          if (rows.length >= total || (res.rows ?? []).length === 0) break;
        }
        setList({ key: queryKey, rows, total });
        setError(null);
      } catch (err) {
        if (seq === readSeq.current) setError(String((err as Error).message));
      }
    })();
    // `filterQuery` is keyed by `queryKey`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listMode, queryKey, wantsAll, reload, version]);

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      const res = await projectsApi.tasks(myTasksQuery(filterQuery, nextTaskPage(list.rows.length)));
      setList((current) => ({
        ...current,
        rows: appendTasks(current.rows, res.rows ?? []),
        total: res.total ?? current.total,
      }));
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setLoadingMore(false);
    }
  };

  // ── The statuses of each project a row sits in ─────────────────────────
  // A cell, a drop and a lane read the statuses of the row's OWN project. One
  // read for each project, kept while My tasks is open. A project the member
  // may not open answers no statuses, and its rows keep the server's stage.
  const [statusSets, setStatusSets] = useState<Readonly<Record<string, StatusRow[]>>>({});
  const asked = useRef(new Set<string>());
  const ownStatuses = useCallback(
    async (projectId: string): Promise<StatusRow[]> => {
      if (statusSets[projectId]) return statusSets[projectId];
      try {
        const res = await projectsApi.statuses(projectId);
        setStatusSets((current) => ({ ...current, [projectId]: res.rows ?? [] }));
        return res.rows ?? [];
      } catch {
        return [];
      }
    },
    [statusSets],
  );
  useEffect(() => {
    const wanted = [...new Set(list.rows.map((r) => r.project_id))].filter(
      (id) => id && !asked.current.has(id),
    );
    for (const id of wanted.slice(0, 40)) {
      asked.current.add(id);
      void ownStatuses(id);
    }
  }, [list.rows, ownStatuses]);

  const known = useMemo(() => Object.values(statusSets).flat(), [statusSets]);
  const knownById = useMemo(() => new Map(known.map((s) => [s.id, s])), [known]);

  // ── The calendar window ─────────────────────────────────────────────────
  const [monthAnchor, setMonthAnchor] = useState<Date>(() => new Date());
  const [calLayout, setCalLayout] = useState<CalendarLayout>("month");
  const grid = useMemo(() => calendarGrid(calLayout, monthAnchor), [calLayout, monthAnchor]);
  const [month, setMonth] = useState<{ rows: TaskRow[]; undated: number; truncated: boolean }>({
    rows: NO_ROWS,
    undated: 0,
    truncated: false,
  });
  const calendarOn = !attention && mode === "calendar";
  useEffect(() => {
    if (!calendarOn) return;
    let live = true;
    const { from, to } = calendarWindow(grid);
    projectsApi
      .calendar({ from, to, scope: "mine", with_overlay: 1, ...filterQuery })
      .then((res) => {
        if (!live) return;
        setMonth({ rows: res.rows ?? [], undated: res.undated ?? 0, truncated: res.truncated ?? false });
      })
      .catch((err: unknown) => {
        if (live) setError(String((err as Error).message));
      });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [calendarOn, grid, queryKey, reload, version]);

  // ── Needs attention, over mine ─────────────────────────────────────────
  const [attentionPage, setAttentionPage] = useState<AttentionPage | null>(null);
  const [attentionError, setAttentionError] = useState<string | null>(null);
  useEffect(() => {
    if (!attention) return;
    let live = true;
    attentionApi
      .mine()
      .then((page) => {
        if (!live) return;
        setAttentionPage(page);
        setAttentionError(null);
      })
      .catch((err: unknown) => {
        if (live) setAttentionError(`Could not read the list. ${String((err as Error).message)}`);
      });
    return () => {
      live = false;
    };
  }, [attention, reload, version]);

  // ── What the canvases draw ──────────────────────────────────────────────
  const shown = useMemo(() => chipRows(list.rows, chip, today), [list.rows, chip, today]);
  const statuses = useMemo(() => mineStatuses(list.rows, known), [list.rows, known]);
  // A status is a lane of ONE project, so the roll-up groups by stage instead.
  const axis: GroupBy = groupBy === "status" ? "category" : groupBy;
  const groups = useMemo(
    () => (axis === "category" ? stageGroups(shown, knownById) : groupTasks(shown, axis, { statuses })),
    [axis, shown, knownById, statuses],
  );
  const pathOf = useCallback((task: TaskRow) => projectPath(task.project_id), [projectPath]);
  const statusesFor = useCallback(
    (task: TaskRow) => statusSets[task.project_id] ?? statuses.filter((s) => s.id === task.status_id),
    [statusSets, statuses],
  );
  const calendarRows = useMemo(() => chipRows(month.rows, chip, today), [month.rows, chip, today]);

  const quickAddHome = personalRootId ?? "";
  const reloadAll = () => {
    again();
    summary.refresh();
  };

  async function drop(task: TaskRow, writes: readonly PositionWrite[], patch: Record<string, string | number | null> | null) {
    setError(null);
    try {
      if (axis === "category") {
        const plan = stageDrop(task, droppedStage(task.id, writes), await ownStatuses(task.project_id));
        if (plan.kind === "refuse") {
          setError(plan.reason);
          return;
        }
        if (plan.kind === "none") return;
        setList((current) => ({
          ...current,
          rows: current.rows.map((r) =>
            r.id === task.id ? { ...r, status_id: plan.patch.status_id, status_category: plan.stage } : r,
          ),
        }));
        await projectsApi.patchTask(task.id, plan.patch);
      } else if (patch) {
        await projectsApi.patchTask(task.id, patch);
      }
    } catch (err) {
      setError(String((err as Error).message));
    }
    reloadAll();
  }

  async function exportCsv() {
    setError(null);
    try {
      const res = await fetch(`${exportPath({ projectId: null, filters, shownFields, sort: null })}&scope=mine`);
      if (!res.ok) {
        setError(`Export failed (${res.status})`);
        return;
      }
      saveCsv(await res.blob(), filenameFromDisposition(res.headers.get("content-disposition"), EXPORT_FILENAME));
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  const errorLine = error ? (
    <p role="alert" className="border-b border-border bg-destructive/10 px-3 py-2 text-xs text-destructive">
      {error}
    </p>
  ) : null;

  if (attention) {
    return (
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden" data-testid="my-tasks-space">
        <NeedsAttentionList
          page={attentionPage}
          error={attentionError}
          personLabels={personLabels}
          onOpenTask={onOpenTaskId}
          onBack={() => onView({ ...view, attention: false })}
          onChanged={again}
        />
      </div>
    );
  }

  const chips = <MyTasksChips chip={chip} summary={summary.data} onChip={(next) => onView({ ...view, chip: next })} />;
  const listLoaded = list.key === queryKey;
  const emptyChip =
    chip !== "all" && listLoaded && shown.length === 0 ? (
      <EmptyState
        icon="ListChecks"
        message={`Nothing in ${MY_TASKS_CHIPS.find((c) => c.id === chip)?.label ?? chip}.`}
        action={{ label: "Show all", icon: "List", onClick: () => onView({ ...view, chip: "all" }) }}
      />
    ) : null;

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden" data-testid="my-tasks-space" data-mode={mode}>
      {errorLine}
      {mode === "overview" ? null : (
        <>
          {chips}
          <FilterChrome
            compact={compactChrome}
            phone={phone}
            // On a phone the tabs live in the View sheet (OP-1).
            modes={MODE_OPTIONS}
            onMode={(next) => onView({ ...view, mode: next as MyTasksMode })}
            resultCount={list.total}
            loadedLabel={wantsAll ? null : loadedOfTotal(list.rows.length, list.total)}
            agentSummary={null}
            agentFocus={null}
            onAgentFocus={() => undefined}
            filters={filters}
            onFilters={setFilters}
            archivedCount={null}
            personLabels={personLabels}
            mode={mode}
            spansProjects
            groupBy={axis}
            onGroupBy={setGroupBy}
            lanes={lanes}
            onSubGroupBy={(next) => setLanes((current) => ({ ...current, subGroupBy: next, collapsedLanes: [] }))}
            subtasks={subtasks}
            onSubtasks={setSubtasks}
            me={me}
            people={people}
            tags={[]}
            shownFields={shownFields}
            onShownFields={setShownFields}
            fields={[]}
            // No saved views: a saved view belongs to a project.
            views={[]}
            activeViewId={null}
            onApplyView={() => undefined}
            onSaveView={() => undefined}
            onDeleteView={() => undefined}
            onUpdateView={() => undefined}
            canSave={false}
            onExport={exportCsv}
          />
        </>
      )}
      {!compactChrome && !wantsAll && listMode ? (
        <MoreTasksBar loaded={list.rows.length} total={list.total} busy={loadingMore} onLoadMore={() => void loadMore()} />
      ) : null}
      <div className="min-h-0 flex-1 overflow-auto">
        <LayoutBoundary key={`my-tasks:${mode}`} layout={`My tasks ${mode}`}>
          {mode === "overview" ? (
            summary.data ? (
              <NodeDashboard
                summary={summary.data}
                onOpen={onOpenProject}
                stuck={stuck.data ?? null}
                load={load.data ?? null}
                throughput={throughput.data ?? null}
                finished={finished.data ?? null}
                onReport={onReport}
              />
            ) : (
              <SkeletonRows count={6} />
            )
          ) : mode === "calendar" ? (
            <CalendarView
              grid={grid}
              tasks={calendarRows}
              subtasks={subtasks === "nested" ? "separate" : subtasks}
              undated={month.undated}
              truncated={month.truncated}
              today={dayKey(new Date())}
              projectId={quickAddHome}
              shownFields={shownFields}
              onCreated={reloadAll}
              onSelect={onOpenTask}
              onMove={(task, patch) => {
                void projectsApi
                  .patchTask(task.id, patch)
                  .catch((err: unknown) => setError(String((err as Error).message)))
                  .finally(reloadAll);
              }}
              onStep={(steps) => setMonthAnchor(shiftGrid(grid, steps))}
              onToday={() => setMonthAnchor(new Date())}
              onLayout={setCalLayout}
              onRefuse={setError}
            />
          ) : !listLoaded ? (
            mode === "board" ? <SkeletonBoard columns={4} /> : <SkeletonRows count={8} />
          ) : emptyChip ? (
            emptyChip
          ) : mode === "table" ? (
            <TableView
              personLabels={personLabels}
              groups={groups}
              groupBy={axis}
              subtasks={subtasks}
              filters={filters}
              statuses={statuses}
              fields={[]}
              shownFields={shownFields}
              sort={null}
              onSort={() => undefined}
              projectId={quickAddHome}
              onCreated={reloadAll}
              onSaved={(fresh) =>
                setList((current) => ({
                  ...current,
                  rows: current.rows.map((t) => (t.id === fresh.id ? { ...t, ...fresh } : t)),
                }))
              }
              onReload={reloadAll}
              onSelect={onOpenTask}
              projectPath={pathOf}
              statusesFor={statusesFor}
            />
          ) : mode === "board" ? (
            <TaskBoard
              personLabels={personLabels}
              phone={phone}
              groups={groups}
              groupBy={axis}
              subtasks={subtasks === "nested" ? "separate" : subtasks}
              filters={filters}
              onClearFilters={() => setFilters(EMPTY_FILTERS)}
              me={me}
              lanes={lanes}
              onToggleLane={(key) =>
                setLanes((current) => ({
                  ...current,
                  collapsedLanes: current.collapsedLanes.includes(key)
                    ? current.collapsedLanes.filter((k) => k !== key)
                    : [...current.collapsedLanes, key],
                }))
              }
              onShowEmptyLanes={(show) => setLanes((current) => ({ ...current, showEmptyLanes: show }))}
              statuses={statuses}
              projectId={quickAddHome}
              shownFields={shownFields}
              onCreated={reloadAll}
              onRenamed={reloadAll}
              onSelect={onOpenTask}
              onDrop={(task, writes, patch) => void drop(task, writes, patch)}
              projectPath={pathOf}
            />
          ) : (
            <TaskList
              personLabels={personLabels}
              groups={groups}
              groupBy={axis}
              subtasks={subtasks}
              filters={filters}
              onClearFilters={() => setFilters(EMPTY_FILTERS)}
              me={me}
              statuses={statuses}
              projectId={quickAddHome}
              shownFields={shownFields}
              onCreated={reloadAll}
              onSelect={onOpenTask}
              projectPath={pathOf}
            />
          )}
          {compactChrome && !wantsAll && listMode ? (
            <MoreTasksBar
              atEnd
              loaded={list.rows.length}
              total={list.total}
              busy={loadingMore}
              onLoadMore={() => void loadMore()}
            />
          ) : null}
        </LayoutBoundary>
      </div>
    </div>
  );
}
