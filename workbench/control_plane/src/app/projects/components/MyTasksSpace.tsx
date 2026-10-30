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
import { useLiveRefresh } from "@/lib/liveRefresh";
import type { PositionWrite } from "../lib/board";
import {
  MY_TASKS_CHIPS,
  MY_TASKS_MAX_PAGES,
  MY_TASKS_MODES,
  type MyTasksChip,
  type MyTasksMode,
  type OwnStatuses,
  chipCount,
  chipNeedsEveryPage,
  chipRows,
  mineStatuses,
  myTasksQuery,
  planMineDrop,
  quickAddHome,
  stageGroups,
  truncationNotice,
} from "../lib/myTasks";
import { useSubtaskComplete } from "./useSubtaskComplete";
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
 * The Needs attention read over mine. The page reads it for the count on the
 * lens button and this space reads it for the lens. One cache key, so the two
 * share one request.
 */
export function useMyAttention(enabled: boolean) {
  return useCachedResource<AttentionPage>(
    enabled ? projectsKey("needs-attention", { mine: "true" }) : null,
    () => attentionApi.mine(),
  );
}

/**
 * One read of every page a chip needs, as one promise. The load effect keeps
 * it by its key, so a second run of the effect for the same key (React's
 * StrictMode, a cleanup and a remount) waits for the SAME requests and sends
 * none of its own.
 */
async function readMine(
  filterQuery: Record<string, string>,
  wantsAll: boolean,
): Promise<{ rows: TaskRow[]; total: number | null }> {
  let rows: TaskRow[] = [];
  let total: number | null = null;
  for (let page = 1; page <= (wantsAll ? MY_TASKS_MAX_PAGES : 1); page++) {
    const res = await projectsApi.tasks(myTasksQuery(filterQuery, page));
    rows = appendTasks(rows, res.rows ?? []);
    total = res.total ?? rows.length;
    if (rows.length >= total || (res.rows ?? []).length === 0) break;
  }
  return { rows, total };
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
  /** OP-12b — true when a project is in the private space (the personal root or its folders). */
  isPrivateProject?: (projectId: string) => boolean;
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
  isPrivateProject,
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
  // Two errors, because they end differently. A READ error clears when the
  // next read succeeds. A WRITE error (a drop, a move, an export) stays
  // through the reload after it, and clears only on the member's next write
  // or on Dismiss (fix round item 7).
  const [readError, setReadError] = useState<string | null>(null);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const again = useCallback(() => setReload((n) => n + 1), []);
  useLiveRefresh(again, "projects");
  const subtaskComplete = useSubtaskComplete();

  // ── The summary, the Overview and the chips ────────────────────────────
  const summary = useMySummary(true);
  const today = summary.data?.today ?? dayKey(new Date());
  // The zone the server counted Today in, so a chip and its count agree when
  // the browser sits in another zone (fix round item 8).
  const zone = summary.data?.time_zone || undefined;
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
  // The read in flight, by its key. The effect is idempotent on mount: a
  // second run for the same key waits for this read and sends no request.
  const inflight = useRef<{ key: string; read: ReturnType<typeof readMine> } | null>(null);
  const readKey = `${queryKey}|${wantsAll}|${reload}|${version}`;
  useEffect(() => {
    if (!listMode) return;
    if (inflight.current?.key !== readKey) {
      inflight.current = { key: readKey, read: readMine(filterQuery, wantsAll) };
    }
    let live = true;
    inflight.current.read.then(
      ({ rows, total }) => {
        if (!live) return;
        setList({ key: queryKey, rows, total });
        setReadError(null);
      },
      (err: unknown) => {
        if (live) setReadError(String((err as Error).message));
      },
    );
    return () => {
      live = false;
    };
    // `filterQuery` and `wantsAll` are inside `readKey`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listMode, readKey]);

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
      setReadError(String((err as Error).message));
    } finally {
      setLoadingMore(false);
    }
  };

  // ── The statuses of each project a row sits in ─────────────────────────
  // A cell, a drop and a lane read the statuses of the row's OWN project. One
  // read for each project, kept while My tasks is open. A failed read is not
  // kept, so the next drop asks again, and it says "could not be read", never
  // "this project has no such status" (fix round item 7).
  const [statusSets, setStatusSets] = useState<Readonly<Record<string, StatusRow[]>>>({});
  const asked = useRef(new Set<string>());
  const ownStatuses = useCallback(
    async (projectId: string): Promise<OwnStatuses> => {
      if (statusSets[projectId]) return { rows: statusSets[projectId], failed: false };
      try {
        const res = await projectsApi.statuses(projectId);
        setStatusSets((current) => ({ ...current, [projectId]: res.rows ?? [] }));
        return { rows: res.rows ?? [], failed: false };
      } catch {
        asked.current.delete(projectId);
        return { rows: [], failed: true };
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
        if (live) setReadError(String((err as Error).message));
      });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [calendarOn, grid, queryKey, reload, version]);

  // ── Needs attention, over mine ─────────────────────────────────────────
  // The page reads the same key for the count on the lens button.
  const attentionRead = useMyAttention(true);
  const refreshAttention = attentionRead.refresh;
  const firstAttention = useRef(true);
  useEffect(() => {
    // The mount read is the hook's own. A later reload or version reads again.
    if (firstAttention.current) {
      firstAttention.current = false;
      return;
    }
    if (attention) refreshAttention();
  }, [attention, reload, version, refreshAttention]);
  const attentionPage = attentionRead.data ?? null;
  const attentionError =
    attentionRead.error && !attentionRead.data ? `Could not read the list. ${attentionRead.error}` : null;

  // ── What the canvases draw ──────────────────────────────────────────────
  const shown = useMemo(() => chipRows(list.rows, chip, today, zone), [list.rows, chip, today, zone]);
  const statuses = useMemo(() => mineStatuses(list.rows, known), [list.rows, known]);
  // A status is a lane of ONE project, so the roll-up groups by stage instead.
  // The same holds for the lanes: a status lane would name one project's
  // status for cards of every project.
  const axis: GroupBy = groupBy === "status" ? "category" : groupBy;
  const shownLanes: BoardLanes = lanes.subGroupBy === "status" ? { ...lanes, subGroupBy: "none" } : lanes;
  const groups = useMemo(
    () => (axis === "category" ? stageGroups(shown, knownById) : groupTasks(shown, axis, { statuses })),
    [axis, shown, knownById, statuses],
  );
  const pathOf = useCallback((task: TaskRow) => projectPath(task.project_id), [projectPath]);
  const privateOf = useCallback(
    (task: TaskRow) => isPrivateProject?.(task.project_id) ?? false,
    [isPrivateProject],
  );
  const statusesFor = useCallback(
    (task: TaskRow) => statusSets[task.project_id] ?? statuses.filter((s) => s.id === task.status_id),
    [statusSets, statuses],
  );
  const calendarRows = useMemo(
    () => chipRows(month.rows, chip, today, zone),
    [month.rows, chip, today, zone],
  );

  // A quick add files to the personal root, or nowhere: never "" (item 10).
  const home = quickAddHome(personalRootId);
  const reloadAll = () => {
    again();
    summary.refresh();
  };

  /**
   * Every write of the board: a drag, the Mark-done tick and the Status menu
   * (`planMineDrop`). A move that completes a parent with open subtasks asks
   * first, and its receipt carries Undo and a Ctrl+Z entry, through the SAME
   * `useSubtaskComplete` path the project board takes (D-PM-38).
   */
  async function drop(task: TaskRow, writes: readonly PositionWrite[], patch: Record<string, string | number | null> | null) {
    setWriteError(null);
    try {
      const own = await ownStatuses(task.project_id);
      const plan = planMineDrop(task, writes, patch, axis, own);
      if (plan.kind === "refuse") {
        setWriteError(plan.reason);
        return;
      }
      if (plan.kind === "none") return;
      setList((current) => ({
        ...current,
        rows: current.rows.map((r) =>
          r.id === task.id
            ? { ...r, ...plan.patch, ...(plan.stage ? { status_category: plan.stage } : {}) }
            : r,
        ),
      }));
      const status = typeof plan.patch.status_id === "string" ? plan.patch.status_id : null;
      if (status && Object.keys(plan.patch).length === 1) {
        const asked = await subtaskComplete.changeStatus(task, status, own.rows, () => reloadAll());
        if (asked) return;
      }
      await projectsApi.patchTask(task.id, plan.patch);
    } catch (err) {
      setWriteError(String((err as Error).message));
    }
    reloadAll();
  }

  async function exportCsv() {
    setWriteError(null);
    try {
      const res = await fetch(`${exportPath({ projectId: null, filters, shownFields, sort: null })}&scope=mine`);
      if (!res.ok) {
        setWriteError(`Export failed (${res.status})`);
        return;
      }
      saveCsv(await res.blob(), filenameFromDisposition(res.headers.get("content-disposition"), EXPORT_FILENAME));
    } catch (err) {
      setWriteError(String((err as Error).message));
    }
  }

  const errorLine =
    writeError || readError ? (
      <div
        role="alert"
        className="flex items-center gap-2 border-b border-border bg-destructive/10 px-3 py-2 text-xs text-destructive"
        data-my-tasks-error={writeError ? "write" : "read"}
      >
        <span className="min-w-0 flex-1">{writeError ?? readError}</span>
        {writeError ? (
          <Button variant="ghost" size="sm" onClick={() => setWriteError(null)}>
            Dismiss
          </Button>
        ) : (
          <Button variant="ghost" size="sm" icon="RotateCcw" onClick={again}>
            Retry
          </Button>
        )}
      </div>
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
  // A chip other than All reads at most MY_TASKS_MAX_ROWS rows. Past that it
  // says so, in the place of the board's "Load more" bar (item 5).
  const truncated = wantsAll && listMode && listLoaded ? truncationNotice(list.rows.length, list.total) : null;
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
      {/* D-PM-38: the "close the subtasks too?" question of a board write. */}
      {subtaskComplete.dialog}
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
            loadedLabel={loadedOfTotal(list.rows.length, list.total)}
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
            lanes={shownLanes}
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
            savedViews={false}
            // On a phone the action row does not draw, so its two other
            // doors sit beside the tabs in the View sheet (item 11).
            sheetLenses={
              phone
                ? [
                    {
                      id: "attention",
                      label: LENS_LABEL,
                      icon: "TriangleAlert",
                      count: attentionPage?.total ?? null,
                      title: "Show my work that stopped and waits for a person",
                      onPick: () => onView({ ...view, attention: true }),
                    },
                    {
                      id: "reports",
                      label: "Reports",
                      icon: "FileText",
                      title: "My day: the report about my own work",
                      onPick: () => onReport(myDayLink()),
                    },
                  ]
                : undefined
            }
            onExport={exportCsv}
          />
        </>
      )}
      {!compactChrome && !wantsAll && listMode ? (
        <MoreTasksBar loaded={list.rows.length} total={list.total} busy={loadingMore} onLoadMore={() => void loadMore()} />
      ) : null}
      {truncated ? (
        // The banner of `MoreTasksBar`, with no button: past the cap a chip
        // cannot load more, so it names the two ways out.
        <div
          className="border-b border-border bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground"
          data-my-tasks-truncated=""
        >
          {truncated}
        </div>
      ) : null}
      <div className="min-h-0 flex-1 overflow-auto">
        <LayoutBoundary key={`my-tasks:${mode}`} layout={`My tasks ${mode}`}>
          {mode === "overview" ? (
            !summary.data && summary.error ? (
              // The read failed: say so, and offer it again. The other tabs
              // do not read the summary, so they stay usable (item 6).
              <EmptyState
                icon="TriangleAlert"
                message="My tasks could not be loaded just now."
                hint="The List, Board and Table read the tasks on their own, so they still work."
                action={{ label: "Retry", icon: "RotateCcw", onClick: summary.refresh }}
              />
            ) : summary.data ? (
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
              // A day add files to the personal root with that due date. With
              // no root yet there is no home, so the add is not drawn.
              projectId={home ?? ""}
              canQuickAdd={home !== null}
              shownFields={shownFields}
              onCreated={reloadAll}
              onSelect={onOpenTask}
              onMove={(task, patch) => {
                setWriteError(null);
                void projectsApi
                  .patchTask(task.id, patch)
                  .catch((err: unknown) => setWriteError(String((err as Error).message)))
                  .finally(reloadAll);
              }}
              onStep={(steps) => setMonthAnchor(shiftGrid(grid, steps))}
              onToday={() => setMonthAnchor(new Date())}
              onLayout={setCalLayout}
              onRefuse={setWriteError}
            />
          ) : !listLoaded ? (
            readError ? null : mode === "board" ? <SkeletonBoard columns={4} /> : <SkeletonRows count={8} />
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
              projectId={home ?? ""}
              canQuickAdd={false}
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
              projectPrivate={privateOf}
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
              lanes={shownLanes}
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
              projectId={home ?? ""}
              canQuickAdd={false}
              shownFields={shownFields}
              onCreated={reloadAll}
              onRenamed={reloadAll}
              onSelect={onOpenTask}
              onDrop={(task, writes, patch) => void drop(task, writes, patch)}
              projectPath={pathOf}
              projectPrivate={privateOf}
              statusesFor={statusesFor}
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
              projectId={home ?? ""}
              canQuickAdd={false}
              shownFields={shownFields}
              onCreated={reloadAll}
              onSelect={onOpenTask}
              projectPath={pathOf}
              projectPrivate={privateOf}
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
