"use client";

/**
 * Projects — departments, projects, subprojects, tasks and subtasks.
 *
 * Spec: `project-docs/specs/project_management_app.md` §5 · ticket WS-27d.
 *
 * ONE app, projected into every Center. `?center=<slug>` pre-filters the tree
 * to that Center's granted departments — **presentation only**: the server's
 * grant model already decided which projects came back at all, so a
 * hand-edited slug shows nothing the caller could not already reach (R9, and
 * `lib/tree.filterByCenter`'s own test says so).
 */
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { PROJECT_STATES } from "@/lib/statusAccent";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";
import { LayoutBoundary } from "@/components/LayoutBoundary";
import { useMobileDrawer } from "@/components/AppShell";
import { useViewMode } from "@/components/ViewModeProvider";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import {
  type GrantRow,
  type ProjectRow,
  type StatusRow,
  type TaskRow,
  type FieldRow,
  type TagRow,
  type NodeSummary,
  type ViewRow,
  projectsApi,
  projectsKey,
} from "./lib/api";
import { FieldManager } from "./components/FieldManager";
import { MoveDialog } from "./components/MoveDialog";
import { LifecyclePolicy } from "./components/LifecyclePolicy";
import { TagManager } from "./components/TagManager";
import { BulkBar } from "./components/BulkBar";
import { FilterBar } from "./components/FilterBar";
import { NotificationBell } from "./components/NotificationBell";
import { type CreatingDraft, ProjectTree } from "./components/ProjectTree";
import type { ProjectMenuHandlers } from "./lib/projectMenu";
import { CalendarView } from "./components/CalendarView";
import { SearchPalette } from "./components/SearchPalette";
import { TimelineView } from "./components/TimelineView";
import { TableView } from "./components/TableView";
import { TaskBoard } from "./components/TaskBoard";
import { TaskList } from "./components/TaskList";
import { TaskPanel } from "./components/TaskPanel";
import { ShortcutsSheet } from "./components/ShortcutsSheet";
import { TriageRail } from "./components/TriageRail";
import { SAVED_VIEW_POSITION, orderBearingView, type planDrop } from "./lib/board";
import {
  type CalendarLayout,
  calendarGrid,
  calendarWindow,
  dayKey,
  shiftGrid,
} from "./lib/calendar";
import {
  type CommandActions,
  type CommandContext,
  SEQUENCE_TIMEOUT_MS,
  VIEW_MODES,
  type ViewMode,
  availableCommands,
  isSequenceKey,
  isTypingTarget,
  stepSequence,
} from "./lib/commands";
import {
  DEFAULT_PANEL_MODE,
  type PanelMode,
  isOverlayMode,
  readPanelMode,
  writePanelMode,
} from "./lib/panelMode";
import { isOpenShortcut } from "./lib/search";
import type { Edge, TimelineWindow, TimelineZoom } from "./lib/timeline";
import { windowCentre, windowFor, windowIncluding } from "./lib/timeline";
import {
  type BoardLanes,
  EMPTY_FILTERS,
  type Filters,
  type GroupBy,
  NO_LANES,
  assigneesIn,
  fromConfig,
  groupTasks,
  isFiltered,
  mergeAssignees,
  toConfig,
  toQuery,
} from "./lib/grouping";
import { filenameFromDisposition, saveCsv } from "@/lib/export";
import { inversePatch } from "@/lib/undo";
import { peek, read } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";
import { SkeletonBoard, SkeletonTree } from "@/components/ui/Skeleton";
import { useUndoScope } from "@/components/UndoProvider";
import UndoControls from "@/components/UndoControls";
import { EXPORT_FILENAME, exportPath } from "./lib/export";
import { DEFAULT_SHOWN } from "./lib/shownFields";
import { toggleLane } from "./lib/swimlanes";
import { type TableSort, sortQuery } from "./lib/table";
import {
  allSelected as everySelected,
  buildRequest,
  clickSelect,
  describeOutcome,
  prune,
  visibleIds,
} from "./lib/selection";
import { fetchAccess } from "@/lib/access";
import {
  type ChildOption,
  filterByCenter,
  flatten,
  levelOf,
  pathTo,
  spansMultipleProjects,
  type NodeKind,
  type NodeLevel,
  nodeKind,
  showsDashboard,
} from "./lib/tree";
import AnalyticsView from "./components/AnalyticsView";
import NodeDashboard from "./components/NodeDashboard";
import SpaceSettings from "./components/SpaceSettings";
import {
  PROJECT_APP_SECTIONS,
  type ProjectAppId,
  SPACES_SECTION_LABEL,
} from "./lib/projectApps";

/**
 * Five modes, not Tasks' two, because the domain genuinely has five — the
 * chrome around them is what gets unified, never the count.
 *
 * `ViewMode` and `VIEW_MODES` moved to `lib/commands.ts` (WS-27ab): the
 * palette offers the same five, and a second list here is how the toolbar and
 * the palette come to disagree about what exists.
 */

/** An empty calendar window — the shape before anything has been fetched, and
 *  the shape after a failure, so the view never renders a stale month. */
const NO_MONTH = {
  rows: [] as TaskRow[],
  links: [] as Edge[],
  undated: 0,
  truncated: false,
};

/** Which sheet the phone's bottom bar has pushed into the shell drawer. */
type Sheet = "tree" | "views" | null;

const LOADING_COPY = "Loading projects…";

/**
 * Stable empties.
 *
 * A fresh `[]` is a new identity every render, and both of these are effect
 * dependencies — a literal here re-runs the grants fan-out on every render,
 * which is one request per root project per keystroke.
 */
const NO_ROOTS: ProjectRow[] = [];
const NO_GRANTS: GrantRow[] = [];

/**
 * ── SEAM (WS-27ag) ─────────────────────────────────────────────────────────
 * The ONE place this page renders a non-canvas state. Loading, "nothing here
 * yet" and a failed fetch were three inline paragraphs in three places, two of
 * them carrying the same string and one of them dressed as calm: a failure on
 * `bg-muted`, which is the token for *quiet*, not for *this did not work*.
 *
 * The slice that follows replaces this body with the shared `EmptyState`
 * component and touches nothing else — the four call sites already funnel
 * through here. **Advisory:** this tree has no structural or layout test, so
 * nothing fails if a fifth state is written inline instead of added here.
 */
function renderState(
  kind: "loading" | "empty" | "error",
  message: string,
  /**
   * Which shape to draw while waiting. `text` stays the old paragraph, for the
   * small in-place waits ("Counting the work below…") where a skeleton would
   * be louder than the thing it stands in for.
   */
  shape: "page" | "board" | "tree" | "text" = "text"
) {
  if (kind === "error") {
    return (
      <p
        role="alert"
        className="border-b border-border bg-destructive/10 px-3 py-2 text-xs text-destructive"
      >
        {message}
      </p>
    );
  }
  /**
   * ⚠️ A skeleton is a PERFORMANCE feature, not decoration — it reads as
   * roughly twice as fast as this paragraph at identical latency, because the
   * eye gets structure to settle on and nothing jumps when the rows land.
   * `message` still travels, for the screen reader, on the primitive's
   * `role="status"`.
   */
  if (kind === "loading" && shape === "page") {
    /**
     * The COLD load, which replaces the whole page — so it has to carry the
     * rail as well as the canvas. A skeleton at the wrong geometry is worse
     * than none: it promises one layout and then hands over another, and the
     * jump is the thing that reads as slow.
     */
    return (
      <div className="flex h-full overflow-hidden">
        <div className="hidden w-64 shrink-0 border-r border-border md:block">
          <SkeletonTree />
        </div>
        <SkeletonBoard columns={4} className="flex-1" />
      </div>
    );
  }
  if (kind === "loading" && shape === "board") return <SkeletonBoard columns={4} />;
  if (kind === "loading" && shape === "tree") return <SkeletonTree />;
  return <p className="p-6 text-sm text-muted-foreground">{message}</p>;
}

/**
 * The project nav — the collapsible rail on desktop, the drawer sheet on a
 * phone. ONE component, so tree-vs-drawer cannot drift into two navigations
 * with two active states; `onPicked` is only how the drawer closes itself.
 */
function ProjectNav({
  roots,
  selectedId,
  app,
  onApp,
  onSelect,
  onAddChild,
  onOpenSettings,
  onMove,
  onNewSpace,
  creating,
  onCommitCreate,
  onCancelCreate,
  onPicked,
  actions,
}: {
  roots: ProjectRow[];
  selectedId: string | null;
  /** The app-level destination, or null when a space/project is selected. */
  app: ProjectAppId | null;
  onApp: (id: ProjectAppId) => void;
  onSelect: (project: ProjectRow) => void;
  onAddChild: (parent: ProjectRow, option: ChildOption) => void;
  /** Open Space Settings for a space (migration 194). */
  onOpenSettings: (space: ProjectRow) => void;
  /** WS-27bk §9.12.4 — open the "Move to…" picker for a row. */
  onMove: (node: ProjectRow) => void;
  /** The + on the Spaces heading. */
  onNewSpace: () => void;
  /** The row being named, drawn in place by the tree. */
  creating?: CreatingDraft | null;
  onCommitCreate: (name: string) => void;
  onCancelCreate: () => void;
  /** Called after any navigation, so the phone's drawer can close. */
  onPicked?: () => void;
  /** WS-27bg — the run-state / archive menu. */
  actions?: ProjectMenuHandlers;
}) {
  return (
    <>
      {/* The app's own destinations, in the main sidebar's grammar (owner
          directive 2026-08-31). "My work" is deliberately NOT here — /tasks
          is the personal lens over the one store (D52-D54), and a second
          door to it inside Projects was removed the same day. */}
      {PROJECT_APP_SECTIONS.map((section) => (
        <div key={section.id} className="mb-2">
          {section.label ? (
            <p className="px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              {section.label}
            </p>
          ) : null}
          <div className="flex flex-col gap-0.5">
            {section.items.map((item) => {
              const preview = item.launch === "preview";
              const active = !preview && app === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  aria-pressed={active}
                  disabled={preview}
                  title={preview ? `${item.label} — not built yet` : item.note}
                  onClick={() => {
                    if (preview) return;
                    onApp(item.id);
                    onPicked?.();
                  }}
                  className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm tech-transition ${
                    active
                      ? "bg-primary/15 text-primary"
                      : preview
                        ? "cursor-not-allowed text-muted-foreground/50"
                        : "text-foreground hover:bg-muted"
                  }`}
                >
                  <Icon name={item.icon} className="h-4 w-4 shrink-0" />
                  <span className="min-w-0 flex-1 truncate">{item.label}</span>
                  {/* `preview` is "not built", never "hidden by permission"
                      — so it says so rather than disappearing. */}
                  {preview ? (
                    <span className="shrink-0 text-[10px] uppercase tracking-wider">
                      Soon
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
        </div>
      ))}

      {/* The Spaces section — its own heading, with the + that creates one. */}
      <div className="mb-1 flex items-center gap-1 px-2 py-1.5">
        <p className="min-w-0 flex-1 truncate text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          {SPACES_SECTION_LABEL}
        </p>
        <button
          type="button"
          aria-label="New space"
          title="New space"
          onClick={() => {
            onNewSpace();
            onPicked?.();
          }}
          className="shrink-0 rounded p-1 text-muted-foreground hover:bg-muted"
        >
          <Icon name="Plus" className="h-4 w-4" />
        </button>
      </div>

      <ProjectTree
        roots={roots}
        selectedId={app ? null : selectedId}
        onSelect={(project) => {
          onSelect(project);
          onPicked?.();
        }}
        onAddChild={(parent, option) => {
          onAddChild(parent, option);
          onPicked?.();
        }}
        onOpenSettings={(space) => {
          onOpenSettings(space);
          onPicked?.();
        }}
        onMove={(node) => {
          onMove(node);
          onPicked?.();
        }}
        creating={creating}
        onCommitCreate={onCommitCreate}
        onCancelCreate={onCancelCreate}
        actions={actions}
      />
    </>
  );
}

/**
 * The five modes as a toolbar control (desktop) and as a drawer sheet (phone).
 *
 * Deliberately NOT the shared `<Tabs>`: that is a page-level bar carrying its
 * own `px-4 sm:px-6 py-3` and bottom border, and this sits *inside* one such
 * header. Same active token as every other nav in the house.
 */
function ModeSwitch({
  mode,
  onPick,
  layout,
}: {
  mode: ViewMode;
  onPick: (next: ViewMode) => void;
  layout: "toolbar" | "sheet";
}) {
  const sheet = layout === "sheet";
  return (
    <div
      className={sheet ? "flex flex-col gap-0.5" : "flex shrink-0 items-center gap-1"}
      role="group"
      aria-label="View mode"
    >
      {VIEW_MODES.map((entry) => (
        <button
          key={entry.id}
          type="button"
          aria-pressed={mode === entry.id}
          onClick={() => onPick(entry.id)}
          className={`tech-transition flex items-center gap-2 rounded-md capitalize ${
            sheet ? "px-3 py-2.5 text-sm" : "px-2 py-1 text-xs"
          } ${
            mode === entry.id
              ? "bg-primary/10 text-primary"
              : "text-muted-foreground hover:bg-muted"
          }`}
        >
          <Icon name={entry.icon} className={sheet ? "h-4 w-4" : "h-3.5 w-3.5"} />
          {entry.id}
        </button>
      ))}
    </div>
  );
}

function ProjectsWorkspace() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const center = searchParams.get("center");
  // WS-28e §6.4 — "Assign work" from the People Center lands here with the
  // assignee pre-filled. Held in STATE seeded from the param rather than read
  // live, so the ✕ can dismiss it without a navigation.
  const [prefillAssignee, setPrefillAssignee] = useState<string | null>(
    searchParams.get("assignee")
  );

  // WS-27ag — the shell. `/projects` had no mobile branch at all: a 240px nav
  // beside a five-mode canvas, plus a third column when a task opened, inside
  // the shell's `pb-nav` scroller. Every other app in the tree decides its
  // layout here.
  const { isMobile } = useViewMode();
  const { open: openDrawer, close: closeDrawer, isOpen: drawerOpen } = useMobileDrawer();
  /** Desktop only: the left rail collapses, at Tasks' width. */
  const [railOpen, setRailOpen] = useState(true);
  /** Phone only: which sheet the bottom bar has pushed into the shell drawer. */
  const [sheet, setSheet] = useState<Sheet>(null);

  /**
   * ── The tree, read through the shared cache ────────────────────────────
   *
   * `useCachedResource` paints the last known tree on the FIRST frame when we
   * have been here before, then revalidates underneath. Navigating away and
   * back used to re-run the whole waterfall from zero against a database a
   * ~124 ms round trip away, behind "Loading projects…" the entire time.
   *
   * `roots` is memoised off `tree.data` because the grants effect below takes
   * it as a dependency, and a fresh `[]` on every render would re-fetch every
   * root's grants on every render.
   */
  const tree = useCachedResource(projectsKey("tree"), () => projectsApi.tree());
  const roots = useMemo(() => tree.data?.rows ?? NO_ROOTS, [tree.data]);
  const [grants, setGrants] = useState<GrantRow[]>([]);
  const [selected, setSelected] = useState<ProjectRow | null>(null);
  const [statuses, setStatuses] = useState<StatusRow[]>([]);
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [openTask, setOpenTask] = useState<TaskRow | null>(null);
  // WS-27ab — peek · side · full, persisted per user (`lib/panelMode.ts`).
  // Read in an effect rather than a lazy initialiser: `localStorage` does not
  // exist while this renders on the server, and a first paint that disagreed
  // with the second is a hydration mismatch.
  const [panelMode, setPanelModeState] = useState<PanelMode>(DEFAULT_PANEL_MODE);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPanelModeState(readPanelMode());
  }, []);
  const setPanelMode = useCallback((next: PanelMode) => {
    setPanelModeState(next);
    writePanelMode(next);
  }, []);
  // The panel's statuses are held apart from the selected project's, because a
  // task opened from a deep link can belong to a project that is not selected —
  // and a panel offering another project's statuses would offer transitions
  // that do not exist.
  const [panelStatuses, setPanelStatuses] = useState<StatusRow[]>([]);
  /**
   * The app-level destination, or null when a space/project is selected
   * (owner directive 2026-08-31 — the Projects app has its own sidebar
   * sections now).
   */
  const [app, setApp] = useState<ProjectAppId | null>(null);
  // `null` = nobody has chosen yet, which is a different state from "board":
  // the right default depends on the viewport, and a board of fixed-width
  // columns is the wrong first screen on a 390px one. An explicit pick wins on
  // both, and survives a resize.
  const [chosenMode, setChosenMode] = useState<ViewMode | null>(null);
  const mode: ViewMode = chosenMode ?? (isMobile ? "list" : "board");
  /**
   * The Overview canvas (owner ask 2026-08-31): the SAME dashboard a space
   * shows, offered beside a project's task views. It reads `summary`, which
   * is already fetched for every selected node, so choosing it costs no
   * extra request. The filter bar, composer, bulk bar and triage rail all
   * hide — none of them acts on a roll-up.
   */
  const overview = mode === "overview";
  /**
   * ⚠️ `loading` means NOTHING TO SHOW — it is not "a request is running".
   *
   * That distinction is the whole fix. The old flag went true on every mount,
   * so a revisit blanked a page that already had its answer. `tree.refreshing`
   * is the other half: a read in flight OVER content, which must never blank
   * anything.
   */
  const loading = tree.loading;
  const [error, setError] = useState<string | null>(null);
  /**
   * The one error line, from either source.
   *
   * DERIVED, not copied into state by an effect — a copy is a second place the
   * truth lives, and it goes stale the moment one of the two clears. The
   * page's own failures still win: `error` is what the actions set, and it is
   * the more specific message when both are present.
   *
   * ⚠️ `tree.error` never replaces the rows. A failed revalidation over a good
   * tree leaves the tree on screen and puts the message beside it — see
   * `useCachedResource`'s `applyError`.
   */
  const shownError = error ?? tree.error;

  // Creating a node: `undefined` = not creating; otherwise the parent row
  // (`null` = a new space at the root) plus WHAT to create there — the
  // grammar's two questions, held together so they cannot disagree
  // (migration 193: folders exist, and a + may offer either kind).
  const [creating, setCreating] = useState<
    | {
        parent: ProjectRow | null;
        kind: NodeKind;
        label: string;
        /** The level the new node will occupy — it picks the row's glyph. */
        level: NodeLevel;
      }
    | undefined
  >(undefined);
  const [newTask, setNewTask] = useState("");
  const [treeKey, setTreeKey] = useState(0);
  // The subtree roll-up for the current selection, and the space whose
  // settings dialog is open (migration 194). Both null when not applicable.
  const [summary, setSummary] = useState<NodeSummary | null>(null);
  const [settingsFor, setSettingsFor] = useState<ProjectRow | null>(null);
  /** WS-27bk §9.12.4 — the node whose "Move to…" picker is open. */
  const [movingNode, setMovingNode] = useState<ProjectRow | null>(null);
  const [moving, setMoving] = useState(false);
  // Analytics reads the portfolio roll-up — the same shape as a node's, so
  // one dashboard component draws both.
  const [portfolio, setPortfolio] = useState<NodeSummary | null>(null);
  const toast = useToast();

  // WS-27k — filters go to the server, grouping is applied here. `activeView`
  // is only a highlight: applying a view copies its config into these two, so
  // editing a filter afterwards leaves the chip lit but the board honest, and
  // the chip clears the moment the state stops matching what was saved.
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [groupBy, setGroupBy] = useState<GroupBy>("status");
  // WS-27y — the board's second axis plus its lane state; saved with a view.
  const [lanes, setLanes] = useState<BoardLanes>(NO_LANES);
  // WS-27x — the view's shown fields (table columns AND the chip gate), saved
  // with a view; and the table's header sort, which travels to the server as
  // the existing `sort`/`direction` parameters (`TASK_SORTS` keys).
  const [shownFields, setShownFields] = useState<string[]>([...DEFAULT_SHOWN]);
  const [tableSort, setTableSort] = useState<TableSort | null>(null);
  const [views, setViews] = useState<ViewRow[]>([]);
  const [activeViewId, setActiveViewId] = useState<string | null>(null);
  const [me, setMe] = useState("");

  // WS-27l — the selected node's custom field definitions. Root-scoped, so the
  // whole subtree shares one set; held here rather than in the panel because
  // the panel opens and closes far more often than these change.
  const [fields, setFields] = useState<FieldRow[]>([]);
  const [managingFields, setManagingFields] = useState(false);

  // WS-27m — the selected node's tag registry. Root-scoped like the fields, and
  // held here for the same reason: the filter bar, the panel's picker and the
  // manager all read it, and three fetches of one list would disagree.
  const [tags, setTags] = useState<TagRow[]>([]);
  const [managingTags, setManagingTags] = useState(false);

  // WS-27z — the lifecycle-policy dialog. Root projects only: the policy is a
  // root setting the whole subtree inherits, and the gateway 422s a child.
  const [managingLifecycle, setManagingLifecycle] = useState(false);
  // The header's one overflow menu (owner ask 2026-08-31, Plane's header
  // discipline): management dialogs open from HERE, not from a row of
  // always-visible buttons beside the view switcher.
  const [manageOpen, setManageOpen] = useState(false);
  const manageRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!manageOpen) return;
    // NotificationBell's exact dismissal wiring — the one popover walker.
    const away = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (shouldDismiss(target, domClickWalk(manageRef.current))) {
        setManageOpen(false);
      }
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [manageOpen]);

  // WS-27n — multi-select. `anchor` is the last card clicked without shift,
  // which is what a shift-click measures its range from.
  // WS-27q — the calendar is a WINDOW, not the paged task list, so it holds
  // its own rows. Sharing `tasks` would mean either paginating the calendar
  // (a month with silently missing days) or unpaginating the board.
  // WS-27r — the search palette. Held at the page rather than in a view,
  // because the whole point is that it works from wherever you already are.
  const [searching, setSearching] = useState(false);
  // WS-27ab — the `?` sheet, printed from the same command registry the
  // palette and the key sequences read.
  const [showingShortcuts, setShowingShortcuts] = useState(false);

  // WS-27ac — the calendar's own anchor and layout. The TIMELINE reads the same
  // window and is always the month's, so the layout only reaches the calendar.
  const [monthAnchor, setMonthAnchor] = useState<Date>(() => new Date());
  const [calLayout, setCalLayout] = useState<CalendarLayout>("month");
  /**
   * The timeline's zoom, and the span of dates it loads (WS-27t S3).
   *
   * Held here rather than inside the view because the zoom decides the FETCH,
   * not just the layout — same shape as `calLayout` above. The timeline used to
   * borrow the calendar's one-month window, so dragging a task past the end of
   * the month made it disappear on the reload that followed.
   */
  /**
   * WS-27af — who the assignee filter offers.
   *
   * **Accumulated, never recomputed from what is on screen.** The obvious
   * version reads the assignees off the loaded tasks — but those tasks are
   * already filtered, so choosing "Priya" reloads to Priya's tasks only, and
   * the dropdown collapses to Priya. The filter becomes one you cannot leave
   * except by clearing it, which is the kind of trap that reads as a bug in
   * the data.
   *
   * So the set only ever GROWS while you are in a project, and is emptied when
   * you leave it. The cost is that somebody whose last task closed stays in the
   * list until you switch projects, which is the harmless direction to be
   * wrong in.
   */
  const [people, setPeople] = useState<string[]>([]);
  /**
   * Undo history, scoped to the open project.
   *
   * Switching project clears it, which is the point of the scope: an entry
   * holds the values needed to revert a row in a project you have navigated
   * away from, and pressing Ctrl+Z there must not reach back into it.
   */
  const undoApi = useUndoScope(`projects:${selected?.id ?? "none"}`);
  const [zoom, setZoom] = useState<TimelineZoom>("month");
  const [timeWindow, setTimeWindow] = useState<TimelineWindow>(() =>
    windowFor("month", dayKey(new Date()))
  );
  const [month, setMonth] = useState(NO_MONTH);

  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  const [anchor, setAnchor] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkNotice, setBulkNotice] = useState<string | null>(null);

  // WS-27ag — the phone's bottom bar (AppShell's `isProjectsPage` branch) talks
  // to this page over the same `cc-mobile-nav` channel Tasks, Notes and the App
  // Workshop use. Tapping the tab that is already showing closes its sheet.
  useEffect(() => {
    const handler = (event: Event) => {
      const tab = (event as CustomEvent<string>).detail;
      if (tab === "projects-tree") setSheet((s) => (s === "tree" ? null : "tree"));
      else if (tab === "projects-views") setSheet((s) => (s === "views" ? null : "views"));
      else if (tab === "projects-search") {
        // ⌘K has no phone equivalent, so the palette needs a control. It is a
        // full-screen overlay of its own — the drawer would be a second one.
        setSheet(null);
        setSearching(true);
      }
    };
    window.addEventListener("cc-mobile-nav", handler);
    return () => window.removeEventListener("cc-mobile-nav", handler);
  }, []);

  useEffect(() => {
    // Only for the "Mine" toggle. `fetchAccess` never throws, and an empty
    // address disables the button rather than filtering on nobody.
    const controller = new AbortController();
    void fetchAccess(controller.signal).then((access) => setMe(access.email));
    return () => controller.abort();
  }, []);

  // The tree, plus every root's grants — the grants are what the Center filter
  // reads, and fetching them per root keeps `filterByCenter` a pure function
  // over data the page already holds.
  /**
   * ⚠️ GRANTS NO LONGER BLOCK THE FIRST PAINT.
   *
   * This used to be the tail of the tree read: `await` the tree, then `await`
   * one `grants` call PER ROOT, and only then drop the loading flag. Two
   * serial waves against a database ~124 ms away, with the whole page held
   * behind both — to decorate rows with permission chips.
   *
   * The tree is what the page IS. Grants are an annotation on it. So the tree
   * paints as soon as it lands and the chips fill in when they arrive, which
   * is roughly half the wait for the thing the reader actually came for.
   *
   * Each call still swallows its own failure: one unreadable root's grants
   * must not cost the other roots theirs.
   */
  /**
   * ⚠️ Keyed on the root IDS, not on `roots`.
   *
   * Every revalidation of the tree hands back a NEW object — same projects,
   * new identity — and an effect that depended on the array would re-fan-out
   * one request per root each time, including on every window focus. What
   * this fan-out actually depends on is WHICH roots exist, and that is a
   * string.
   */
  const rootIds = useMemo(() => roots.map((root) => root.id).join(","), [roots]);
  useEffect(() => {
    // No roots, nothing to ask for. The empty case is DERIVED below rather
    // than written into state here — an effect that only sets state is a
    // render the component could have done itself.
    if (rootIds === "") return;
    let live = true;
    (async () => {
      const all = await Promise.all(
        rootIds.split(",").map((id) =>
          projectsApi
            .grants(id)
            .then((res) => res.rows)
            .catch(() => [] as GrantRow[])
        )
      );
      if (live) setGrants(all.flat());
    })();
    return () => {
      live = false;
    };
  }, [rootIds]);

  /**
   * `treeKey` is the page's explicit "read it again" signal.
   *
   * Kept, although a write now invalidates the cache on its own (see
   * `projectsApi`'s `call`): the seven call sites all follow a mutation, and
   * an explicit refresh that costs one deduped read is cheaper than auditing
   * every one of them.
   *
   * `tree.refresh` is safe as a dependency although `tree` itself is a new
   * object every render — it is a `useCallback` over `[key, ttl]`, and this
   * page's key is a constant string.
   */
  const refreshTree = tree.refresh;
  useEffect(() => {
    // 0 is the initial render, which the hook has already read for.
    if (treeKey === 0) return;
    refreshTree();
  }, [treeKey, refreshTree]);

  /**
   * WS-27bg — the project run-state and archive actions.
   *
   * Every one of them re-reads the tree (`treeKey`) rather than patching
   * `roots` in place. Optimism would be wrong here for a reason specific to
   * this feature: archiving stamps a whole SUBTREE server-side, and a state
   * change alters what every DESCENDANT effectively is — so the set of rows a
   * write touches is not knowable from the row that was clicked.
   *
   * The archive toast reports `open_tasks`. That count is the warning D-PM-26
   * asks for and the reason the endpoint returns it: filing a project with
   * unfinished work in it is allowed, and the user should know they did it.
   */
  const projectMenuActions: ProjectMenuHandlers = useMemo(
    () => ({
      onSetState: (project, state) => {
        // Re-selecting the state you are already in writes nothing and raises
        // no toast: the menu keeps the current state visible (so the list does
        // not jump between openings), which means clicking it is a normal
        // gesture rather than a mistake worth reporting.
        if ((project.status ?? "active") === state) return;
        void toast.promise(
          projectsApi
            .patchProject(project.id, { status: state })
            .then((res) => {
              setTreeKey((k) => k + 1);
              return res;
            }),
          {
            key: `project-state:${project.id}`,
            loading: `Updating ${project.name}…`,
            success: () =>
              `${project.name} is now ${
                PROJECT_STATES[state]?.label ?? state
              }`,
            error: "Couldn't change the project state",
          }
        );
      },
      onArchive: (project) => {
        void toast.promise(
          projectsApi.archiveProject(project.id).then((res) => {
            setTreeKey((k) => k + 1);
            return res;
          }),
          {
            key: `project-archive:${project.id}`,
            loading: `Archiving ${project.name}…`,
            // `open_tasks` is the warning D-PM-26 asks for and the reason the
            // endpoint reports it: filing a project with unfinished work in it
            // is ALLOWED, and the person who just did it should know.
            success: (res) =>
              res.open_tasks > 0
                ? `Archived ${project.name} — ${res.open_tasks} task(s) still open`
                : `Archived ${project.name}`,
            error: "Couldn't archive the project",
          }
        );
      },
      onUnarchive: (project) => {
        void toast.promise(
          projectsApi.unarchiveProject(project.id).then((res) => {
            setTreeKey((k) => k + 1);
            return res;
          }),
          {
            key: `project-archive:${project.id}`,
            loading: `Restoring ${project.name}…`,
            success: () => `Restored ${project.name}`,
            error: "Couldn't restore the project",
          }
        );
      },
      onRename: (project, name) => {
        void toast.promise(
          projectsApi.patchProject(project.id, { name }).then((res) => {
            setTreeKey((k) => k + 1);
            // `selected` is a SNAPSHOT, and the resync effect below only checks
            // that its id is still present — it never refreshes the object. The
            // tree redraws from `roots` and looks right, while `selected.name`
            // keeps the old value in the quick-add placeholder and the two
            // `projectName={selected.name}` panels until you click away and
            // back. Unreachable before this ticket, because a project's name
            // could not change; reachable now, so it is fixed here.
            //
            // MERGED, not replaced: the PATCH response is a bare project row
            // with no `children`, and `selected` is read for its subtree
            // elsewhere. Taking only the field that changed keeps the rest.
            setSelected((prev) =>
              prev && prev.id === project.id ? { ...prev, name: res.name } : prev
            );
            return res;
          }),
          {
            key: `project-rename:${project.id}`,
            loading: `Renaming ${project.name}…`,
            // The name is read back off the RESPONSE, not echoed from the
            // field: the server owns what it stored, and a toast that reports
            // the request rather than the result is how a silently-trimmed or
            // truncated value gets confirmed as something it is not.
            success: (res) => `Renamed to “${res.name}”`,
            error: "Couldn't rename the project",
          }
        );
      },
    }),
    [toast]
  );

  /**
   * Commit Space Settings — name, icon and ramp slot in ONE patch
   * (migration 194).
   *
   * One request, not three: the three fields are what the dialog is, so a
   * partial apply would leave a space wearing half of what was chosen and
   * no way to tell which half. `selected` is merged rather than replaced,
   * for `onRename`'s reason above — the response is a bare row and the
   * snapshot is read elsewhere for its subtree.
   */
  async function saveSpaceSettings(
    space: ProjectRow,
    values: { name: string; icon: string; icon_slot: number }
  ) {
    setSettingsFor(null);
    await toast.promise(
      projectsApi.patchProject(space.id, values).then((res) => {
        setTreeKey((k) => k + 1);
        setSelected((prev) =>
          prev && prev.id === space.id ? { ...prev, ...res } : prev
        );
        return res;
      }),
      {
        key: `space-settings:${space.id}`,
        loading: `Saving ${space.name}…`,
        success: (res) => `Saved “${res.name}”`,
        error: "Couldn't save the space",
      }
    );
  }

  const visibleRoots = useMemo(
    // ⚠️ `rootIds` gates the grants, so an empty tree can never be filtered by
    // the PREVIOUS tree's grants while the fan-out below has not run yet.
    () => filterByCenter(roots, rootIds === "" ? NO_GRANTS : grants, center),
    [roots, rootIds, grants, center]
  );

  // Which LEVEL the selection occupies, derived from the tree rather than
  // stored (owner directive 2026-08-31). It decides the whole surface: a
  // space or a folder shows a dashboard and no views, a project shows its
  // views with the subtree folded in, a subproject shows only itself.
  const selectedLevel = useMemo(
    () => (selected ? levelOf(visibleRoots, selected.id) : "space"),
    [visibleRoots, selected]
  );
  /**
   * Does the selected node's board span more than one project?
   *
   * Read from the TREE rather than from `summary.projects`, though both
   * answer it: the tree is already in memory, so the "Project" axis is
   * offered or withheld on the first paint instead of appearing a moment
   * later when the roll-up lands.
   */
  const spansProjects = useMemo(() => {
    if (!selected) return false;
    const row = flatten(visibleRoots).find((e) => e.node.id === selected.id);
    return row ? spansMultipleProjects(row.node) : false;
  }, [visibleRoots, selected]);
  const dashboardOnly =
    !app && Boolean(selected) && showsDashboard(selectedLevel);
  /** Any surface that is not a project's board — no views, no composer. */
  const noProjectChrome = dashboardOnly || app === "analytics";

  // The roll-up behind the dashboard AND behind a parent project's
  // aggregate header. Fetched for every level: a project with subprojects
  // needs the same numbers, and one endpoint answering both is what keeps
  // the two from disagreeing.
  useEffect(() => {
    if (!selected) {
      setSummary(null);
      return;
    }
    let cancelled = false;
    setSummary(null);
    projectsApi
      .summary(selected.id)
      .then((next) => {
        if (!cancelled) setSummary(next);
      })
      .catch(() => {
        // A failed roll-up must not blank the board underneath it. The
        // dashboard shows its own empty state; an aggregate header simply
        // does not draw.
        if (!cancelled) setSummary(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selected?.id, treeKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Analytics' own read. Separate from `summary` because the two answer
  // different questions and are on screen at different times — sharing one
  // slot would make switching between them flash the wrong numbers.
  useEffect(() => {
    if (app !== "analytics") return;
    let cancelled = false;
    setPortfolio(null);
    projectsApi
      .portfolio()
      .then((next) => {
        if (!cancelled) setPortfolio(next);
      })
      .catch(() => {
        if (!cancelled) setPortfolio(null);
      });
    return () => {
      cancelled = true;
    };
  }, [app, treeKey]);

  // Selecting nothing is a real state (an empty portfolio), so the default is
  // applied only when the current selection has fallen out of the filtered set.
  useEffect(() => {
    if (visibleRoots.length === 0) {
      setSelected(null);
      return;
    }
    const stillVisible =
      selected &&
      JSON.stringify(visibleRoots).includes(`"${selected.id}"`);
    if (!stillVisible) setSelected(visibleRoots[0]);
  }, [visibleRoots, selected]);

  // The drawer holds a SNAPSHOT — AppShell keeps injected content in its own
  // state, so a sheet handed over once keeps rendering the props it was built
  // with. This re-injects whenever what it draws changes; everything it reads
  // is in the dependency list, and every callback inside it is a `useState`
  // setter, so this cannot become a render loop through the drawer's context.
  useEffect(() => {
    if (!isMobile) return;
    if (!sheet) {
      closeDrawer();
      return;
    }
    openDrawer(
      <div className="p-2">
        <div className="mb-2 flex items-center gap-1 px-2">
          <p className="min-w-0 flex-1 truncate text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            {sheet === "tree" ? "Spaces" : "View"}
          </p>
          {sheet === "tree" ? (
            // The rail's + button has to exist here too, or a new space is
            // a thing you can only create on a desktop. The field itself
            // opens as a ROW in the tree below — see `DraftRow`.
            <button
              type="button"
              aria-label="New space"
              onClick={() => {
                setCreating({ parent: null, kind: "project", label: "New space", level: "space" });
                setSheet(null);
              }}
              className="shrink-0 rounded p-1 text-muted-foreground hover:bg-muted"
            >
              <Icon name="Plus" className="h-4 w-4" />
            </button>
          ) : null}
        </div>
        {sheet === "tree" ? (
          <ProjectNav
            roots={visibleRoots}
            selectedId={selected?.id ?? null}
            app={app}
            onApp={setApp}
            onSelect={(project) => {
              setApp(null);
              setSelected(project);
            }}
            onAddChild={(parent, option) => {
              setCreating({ parent, kind: option.kind, label: option.label, level: option.level });
            }}
            onOpenSettings={setSettingsFor}
            onMove={setMovingNode}
            onNewSpace={() => {
              setCreating({
                parent: null, kind: "project",
                label: "New space", level: "space",
              });
            }}
            creating={treeDraft}
            onCommitCreate={(name) => void submitProject(name)}
            onCancelCreate={() => setCreating(undefined)}
            onPicked={() => setSheet(null)}
            actions={projectMenuActions}
          />
        ) : (
          <ModeSwitch
            mode={mode}
            layout="sheet"
            onPick={(next) => {
              setChosenMode(next);
              setSheet(null);
            }}
          />
        )}
      </div>,
    );
  }, [isMobile, sheet, mode, selected, visibleRoots, openDrawer, closeDrawer]);

  // Dismissing the drawer from the outside (the backdrop, or the Menu tab
  // replacing the content) has to clear `sheet`, or the effect above reopens
  // what the user just closed the next time the tree or the mode changes.
  //
  // `set-state-in-effect` is suppressed rather than worked around: the drawer
  // IS an external system — it is AppShell's state, reached through context —
  // and this is the subscribe half. The alternatives all reintroduce the bug
  // (a re-tap after a backdrop dismissal sets the same value, so React bails
  // out and the drawer never reopens).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!drawerOpen) setSheet(null);
  }, [drawerOpen]);

  const loadProject = useCallback(
    async (project: ProjectRow) => {
      setError(null);
      // Filters travel to the server, never applied to the page after it
      // arrives: paging happens in SQL, so a filter applied here would return
      // short pages and hide work that is genuinely there.
      const taskParams = {
        project_id: project.id,
        include_subtree: true,
        page_size: 100,
        ...toQuery(filters),
        // WS-27x — the table's header sort; {} when none, so every other
        // surface keeps the endpoint's default ordering.
        ...sortQuery(tableSort),
      };
      const statusesKey = projectsKey(`nodes/${project.id}/statuses`);
      const tasksKey = projectsKey("tasks", taskParams);

      /**
       * ── Paint what we already know, before asking ──────────────────────
       *
       * Switching project, view or filter and coming back is the same
       * question we asked a moment ago, and the answer is a ~124 ms round
       * trip away. `peek` is what makes that return instant: the rows go up
       * on this frame, and the read below replaces them when it lands.
       *
       * The key carries EVERY parameter (`cacheKey` sorts them), so a
       * different filter is a different question and can never be answered
       * with another filter's rows.
       */
      const heldStatuses = peek<{ rows: StatusRow[] }>(statusesKey);
      const heldTasks = peek<{ rows: TaskRow[] }>(tasksKey);
      if (heldStatuses) setStatuses(heldStatuses.data.rows);
      if (heldTasks) setTasks(heldTasks.data.rows);

      try {
        const [statusRes, taskRes] = await Promise.all([
          read(statusesKey, () => projectsApi.statuses(project.id)),
          read(tasksKey, () => projectsApi.tasks(taskParams)),
        ]);
        setStatuses(statusRes.rows);
        setTasks(taskRes.rows);
      } catch (err) {
        setError(String((err as Error).message));
        // ⚠️ Only blank what we had nothing for. Clearing rows we are already
        // showing turns a failed refresh into an empty board — the screen goes
        // blank at the moment the reader most needs to see something.
        if (!heldStatuses) setStatuses([]);
        if (!heldTasks) setTasks([]);
      }
    },
    [filters, tableSort]
  );

  useEffect(() => {
    if (selected) void loadProject(selected);
  }, [selected, loadProject]);

  // WS-27ae — export the filter that is on screen, with the columns it shows.
  //
  // ⚠️ Fetched rather than navigated to. The endpoint REFUSES a filter wider
  // than its row cap (422 naming the matched count) rather than handing back a
  // partial file, and `window.location = …` would turn that refusal into a tab
  // full of JSON. Fetching is what lets the refusal arrive as a sentence on the
  // board — which is the whole reason the server refuses instead of truncating.
  const exportCsv = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch(
        exportPath({
          projectId: selected?.id ?? null,
          filters,
          shownFields,
          sort: tableSort,
        })
      );
      if (!res.ok) {
        const body = await res.text();
        let detail = `Export failed (${res.status})`;
        try {
          detail = (JSON.parse(body) as { detail?: string }).detail ?? detail;
        } catch {
          // A non-JSON error body came from the proxy, not the gateway.
        }
        setError(detail);
        return;
      }
      // ⚠️ `blob()`, never `text()`: decoding to a string strips the UTF-8 BOM
      // the gateway emits so Excel reads non-ASCII titles correctly, and the
      // saved file would then differ from the bytes the endpoint produced.
      saveCsv(
        await res.blob(),
        filenameFromDisposition(
          res.headers.get("content-disposition"),
          EXPORT_FILENAME
        )
      );
    } catch (err) {
      setError(String((err as Error).message));
    }
  }, [selected, filters, shownFields, tableSort]);

  // WS-27q — the calendar's own fetch, because it reads a WINDOW rather than a
  // page. `grid` is derived so the effect re-runs when the period steps, and
  // `calendarWindow` adds the day of slack the endpoint's UTC reading needs.
  //
  // WS-27ac — the grid, not the window, is what the week layout changes: the
  // SAME `calendarWindow` reads whatever days the grid drew, so a week asks the
  // same endpoint for ten days instead of forty-three. The timeline is always
  // the month's — a Gantt of seven days is a list — so the layout reaches the
  // grid only while the calendar is the view on screen.
  const grid = useMemo(
    () => calendarGrid(mode === "calendar" ? calLayout : "month", monthAnchor),
    [mode, calLayout, monthAnchor]
  );

  const loadMonth = useCallback(async () => {
    if (!selected) {
      setMonth(NO_MONTH);
      return;
    }
    // ⚠️ Two views, two windows. The calendar's resource is the month it is
    // drawing; the timeline's is the work, and a timeline fetched a month at a
    // time loses any task dragged past the month's edge.
    const { from, to } =
      mode === "timeline" ? timeWindow : calendarWindow(grid);
    try {
      const res = await projectsApi.calendar({
        project_id: selected.id,
        include_subtree: true,
        from,
        to,
        // WS-27t — only the timeline draws arrows, and the calendar would pay
        // for a query it never reads.
        include_links: mode === "timeline",
        ...toQuery(filters),
      });
      setMonth({
        rows: res.rows,
        links: res.links,
        undated: res.undated,
        truncated: res.truncated,
      });
    } catch (err) {
      setError(String((err as Error).message));
      // Cleared rather than left as it was: a stale month drawn under a new
      // heading is a calendar confidently showing the wrong dates.
      setMonth(NO_MONTH);
    }
  }, [selected, grid, filters, mode, timeWindow]);

  useEffect(() => {
    // Both date views read the same window endpoint — the WINDOW is the
    // resource, and calendar and timeline are two renderings of it.
    if (mode === "calendar" || mode === "timeline") void loadMonth();
  }, [mode, loadMonth]);

  // Always the CURRENT reload, for undo steps that outlive the render that
  // recorded them. See `rewriteTask`.
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  refreshRef.current = loadMonth;

  // WS-27af — the assignee filter's options, accumulated from whatever has
  // been loaded. `mergeAssignees` returns the same array when nothing is new,
  // so this settles after the first load instead of re-rendering the bar.
  useEffect(() => {
    const found = assigneesIn([...tasks, ...month.rows]);
    setPeople((current) => mergeAssignees(current, found));
  }, [tasks, month.rows]);

  // A different project is a different set of people. Emptied rather than
  // carried, so one project's members never appear in another's filter.
  useEffect(() => {
    setPeople([]);
  }, [selected?.id]);

  useEffect(() => {
    if (!selected) {
      setFields([]);
      setTags([]);
      return;
    }
    let live = true;
    projectsApi
      .fields(selected.id)
      .then((res) => {
        if (live) setFields(res.rows);
      })
      // A board that works without its custom columns beats a board that
      // refuses to load because their definitions did not arrive.
      .catch(() => {
        if (live) setFields([]);
      });
    projectsApi
      .tags(selected.id)
      .then((res) => {
        if (live) setTags(res.rows);
      })
      .catch(() => {
        if (live) setTags([]);
      });
    return () => {
      live = false;
    };
  }, [selected, treeKey]);

  // Saved views belong to the selected node, and are re-read whenever it
  // changes — a chip from the previous project would apply filters that make
  // sense but claim a name that does not.
  useEffect(() => {
    if (!selected) {
      setViews([]);
      return;
    }
    let live = true;
    projectsApi
      .views(selected.id)
      .then((res) => {
        if (live) setViews(res.rows);
      })
      .catch(() => {
        // A board that works without its chips beats a board that refuses to
        // load because its view list did.
        if (live) setViews([]);
      });
    return () => {
      live = false;
    };
  }, [selected]);

  const projectName = useCallback(
    (id: string) =>
      flatten(roots).find((entry) => entry.node.id === id)?.node.name ?? "Project",
    [roots]
  );

  const groups = useMemo(
    () => groupTasks(tasks, groupBy, { statuses, projectName }),
    [tasks, groupBy, statuses, projectName]
  );

  const onScreen = useMemo(() => visibleIds(groups), [groups]);

  /**
   * The same grouping, over the TIMELINE's rows (WS-27t S5).
   *
   * A separate memo rather than reusing `groups`, because the two canvases
   * load different windows — the board holds `tasks`, the timeline holds
   * `month.rows` over its own date span. Grouping the timeline by the board's
   * list would silently drop every task outside the board's window.
   */
  const monthGroups = useMemo(
    () => groupTasks(month.rows, groupBy, { statuses, projectName }),
    [month.rows, groupBy, statuses, projectName]
  );

  // A selection that outlives its filter is how a bulk edit hits tasks nobody
  // can see any more: select forty, narrow to three, press Done believing you
  // are acting on the three in front of you.
  useEffect(() => {
    setPicked((current) => {
      const pruned = prune(current, onScreen);
      return pruned.size === current.size ? current : pruned;
    });
  }, [onScreen]);

  // WS-27ad — one transition, shared with /tasks (`@/lib/selection`): a plain
  // click toggles and becomes the anchor, a shift-click adds the range and
  // leaves the anchor put, and shift never removes. Inlining the three
  // branches here is what let the two apps drift apart in the first place.
  function toggleSelection(id: string, shift: boolean) {
    setBulkNotice(null);
    const next = clickSelect({ selected: picked, anchor }, onScreen, id, shift);
    setPicked(next.selected);
    setAnchor(next.anchor);
  }

  // WS-27y — the keyboard's Shift+Arrow grew the selection; `stepCursor` only
  // ever adds, so replacing with its superset is the union.
  function extendSelection(ids: string[]) {
    setBulkNotice(null);
    setPicked(new Set(ids));
  }

  async function applyBulk(request: ReturnType<typeof buildRequest>) {
    if (!request) return;
    setBulkBusy(true);
    setBulkNotice(null);
    try {
      const outcome = await projectsApi.bulkEdit({
        ...request,
        task_ids: [...picked],
      });
      setBulkNotice(describeOutcome(outcome));
      // The selection is KEPT: a sweep is usually several passes over the same
      // set ("these fifty: status, then owner, then tag"), and clearing after
      // each would make the second pass a re-selection.
      if (selected) await loadProject(selected);
    } catch (err) {
      setBulkNotice(String((err as Error).message));
    } finally {
      setBulkBusy(false);
    }
  }

  function applyView(view: ViewRow) {
    const {
      filters: next,
      groupBy: nextGroup,
      lanes: nextLanes,
      shownFields: nextShown,
    } = fromConfig(view.config);
    setFilters(next);
    setGroupBy(nextGroup);
    setLanes(nextLanes);
    setShownFields(nextShown);
    setActiveViewId(view.id);
  }

  async function saveView(name: string) {
    if (!selected) return;
    try {
      const created = await projectsApi.createView(selected.id, {
        name,
        // Clamped: the gateway (and migration 146's CHECK) accept only
        // 'list' and 'board', so saving from table/calendar/timeline sent a
        // view_type the server refused — a 422 on a working Save button. A
        // saved view stores FILTERS; the canvas it was saved from is not
        // part of what it restores, so 'list' is the honest fallback.
        view_type: mode === "board" ? "board" : "list",
        config: toConfig(filters, groupBy, lanes, shownFields),
        // Above the seeded pair, so the drag handler keeps writing its order
        // into the project's original board rather than into a saved filter.
        position: SAVED_VIEW_POSITION + views.length,
      });
      setViews((current) => [...current, created]);
      setActiveViewId(created.id);
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  /**
   * WS-27ab — write what is on screen into the view that is applied.
   *
   * The same `toConfig` a create uses, so an updated view and a freshly saved
   * one are byte-identical for the same board. The returned row replaces the
   * stored one rather than being merged: the gateway's `normalise_view_config`
   * may have dropped a key it does not know, and keeping the local copy would
   * leave the bar comparing against a config the server never stored — which
   * is a dirty marker that never clears.
   */
  async function updateView(view: ViewRow) {
    try {
      const saved = await projectsApi.patchView(view.id, {
        config: toConfig(filters, groupBy, lanes, shownFields),
      });
      setViews((current) => current.map((v) => (v.id === view.id ? saved : v)));
      setActiveViewId(saved.id);
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function deleteView(view: ViewRow) {
    try {
      await projectsApi.deleteView(view.id);
      setViews((current) => current.filter((v) => v.id !== view.id));
      setActiveViewId((current) => (current === view.id ? null : current));
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  /**
   * WS-27ab — editing a filter no longer DROPS the view.
   *
   * `setActiveViewId(null)` used to run here (and in the group, lane and
   * shown-field handlers), so the association died on the first keystroke and
   * there was no way to say *keep this*. The chip now stays lit, `FilterBar`
   * marks it edited from `viewDivergence`, and the row it grows offers the
   * three real answers: update, save as new, reset.
   */
  function changeFilters(next: Filters) {
    setFilters(next);
  }

  // WS-27x — same rule for the shown-fields set: it is part of a view.
  function changeShownFields(next: string[]) {
    setShownFields(next);
  }

  // Opening a task always resolves ITS project's statuses. From the board that
  // is the set already loaded; from a deep link it may be any project the
  // member can reach, so it is fetched.
  const openWithStatuses = useCallback(
    async (task: TaskRow) => {
      setOpenTask(task);
      if (selected && task.root_project_id === selected.id) {
        setPanelStatuses(statuses);
        return;
      }
      try {
        const res = await projectsApi.statuses(task.root_project_id);
        setPanelStatuses(res.rows);
      } catch {
        // A panel with no status options is degraded but usable; failing to
        // open the task at all because its lanes could not be listed is not.
        setPanelStatuses([]);
      }
    },
    [selected, statuses]
  );

  /**
   * Open one task by id — what a notification, and the People Center's "Open
   * work" list, both link to.
   *
   * Those links have been generating `/projects?task=<id>` since WS-28b and
   * landing on an unchanged board, because nothing here read the parameter.
   */
  const openTaskById = useCallback(
    async (taskId: string) => {
      try {
        await openWithStatuses(await projectsApi.task(taskId));
      } catch (err) {
        setError(String((err as Error).message));
      }
    },
    [openWithStatuses]
  );

  const deepLink = searchParams.get("task");
  useEffect(() => {
    // Keyed on the id alone, deliberately: `openTaskById` closes over the
    // selected project, so depending on it would reopen the task every time
    // the board reloaded — including right after somebody closed the panel.
    if (!deepLink) return;
    let live = true;
    (async () => {
      try {
        const task = await projectsApi.task(deepLink);
        if (live) await openWithStatuses(task);
      } catch (err) {
        if (live) setError(String((err as Error).message));
      }
    })();
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deepLink]);

  /**
   * ── The keyboard, and what it can reach (WS-27ab item 3) ────────────────
   *
   * Everything a key sequence or the palette can do is declared in
   * `lib/commands.ts`; this is the half that only the page can supply — the
   * state each `run` moves. Keeping the two apart is what lets the shortcuts
   * sheet be *generated* rather than written: the registry knows every action
   * and its keys, and knows nothing about React.
   */
  const actions: CommandActions = useMemo(
    () => ({
      navigate: (href) => router.push(href),
      setMode: (next) => setChosenMode(next),
      setPanelMode,
      clearFilters: () => setFilters(EMPTY_FILTERS),
      toggleRail: () => setRailOpen((open) => !open),
      manage: (what) => {
        if (what === "fields") setManagingFields(true);
        else if (what === "tags") setManagingTags(true);
        else if (what === "lifecycle") setManagingLifecycle(true);
      },
      showShortcuts: () => setShowingShortcuts(true),
    }),
    [router, setPanelMode],
  );

  const commandCtx: CommandContext = {
    mode,
    hasProject: Boolean(selected),
    isRoot: Boolean(selected && !selected.parent_project_id),
    filtered: isFiltered(filters),
    panelOpen: Boolean(openTask),
    panelMode,
    // A phone reaches the tree through the shell drawer; there is no rail to
    // toggle, so the command is not offered rather than being a dead entry.
    canToggleRail: !isMobile,
  };

  // Anything modal is up. Sequences are suppressed under it: `g` while a
  // dialog is open must not navigate the page out from under a half-filled
  // form.
  const overlayOpen =
    searching ||
    showingShortcuts ||
    managingFields ||
    managingTags ||
    managingLifecycle;

  // The listener is attached ONCE and reads through this, rather than being
  // re-subscribed on every filter keystroke. Written from an effect rather
  // than during render — a ref is not a render input, and a keydown cannot
  // arrive before the commit that would have updated it.
  const live = useRef({ actions, ctx: commandCtx, overlayOpen });
  useEffect(() => {
    live.current = { actions, ctx: commandCtx, overlayOpen };
  });

  useEffect(() => {
    let pending: string[] = [];
    let timer: ReturnType<typeof setTimeout> | null = null;
    const forget = () => {
      pending = [];
      if (timer) clearTimeout(timer);
      timer = null;
    };
    function onKey(event: KeyboardEvent) {
      // ⌘K from anywhere in Projects. `preventDefault` because the browser's
      // own ⌘K is the address bar's search on some, and losing the app to it
      // is a shortcut that works once.
      if (isOpenShortcut(event)) {
        event.preventDefault();
        forget();
        setSearching(true);
        return;
      }
      if (live.current.overlayOpen) return;
      // Escape closes the task panel from the BOARD as well as from inside it.
      // Opening a task leaves focus on the row that was clicked (deliberately —
      // WS-27y's cursor has to keep working), so the panel's own handler never
      // sees the key. Measured in the browser: without this, Esc did nothing
      // unless you had first clicked into the panel. The panel's handler calls
      // `stopPropagation`, so when focus IS inside it this never runs and the
      // first-Escape-leaves-the-field rule survives.
      if (event.key === "Escape") {
        if (isTypingTarget(event.target as HTMLElement | null)) return;
        if (!live.current.ctx.panelOpen) return;
        event.preventDefault();
        setOpenTask(null);
        return;
      }
      if (!isSequenceKey(event)) return;
      // A bare letter and a text field are the classic collision: without
      // this, typing "go" into the quick-add box navigates away mid-word.
      if (isTypingTarget(event.target as HTMLElement | null)) return;
      const step = stepSequence(
        pending,
        event.key,
        availableCommands(live.current.ctx),
      );
      if (timer) clearTimeout(timer);
      timer = null;
      pending = step.pending;
      // A half-typed prefix is forgotten rather than waiting forever: `g`
      // pressed by accident must not turn the next `p` into a navigation
      // minutes later.
      if (pending.length > 0)
        timer = setTimeout(forget, SEQUENCE_TIMEOUT_MS);
      if (!step.claimed) return;
      event.preventDefault();
      step.command?.run(live.current.actions, live.current.ctx);
    }
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (timer) clearTimeout(timer);
    };
  }, []);

  /**
   * Commit the row being named IN the tree (owner directive 2026-08-31).
   *
   * The name arrives from the draft row rather than from page state: the
   * field lives on the row now, so the page has no business holding its
   * keystrokes — and a shared `newName` was what let the old detached form
   * keep a half-typed value after a cancel.
   */
  async function submitProject(name: string) {
    if (!creating) return;
    setError(null);
    try {
      const created = await projectsApi.createProject({
        name,
        parent_project_id: creating.parent ? creating.parent.id : null,
        // Sent only when it says something: omitted = 'project', and an old
        // gateway mid-deploy (R6) never sees a field it does not know.
        ...(creating.kind === "folder" ? { kind: "folder" } : {}),
      });
      setCreating(undefined);
      setTreeKey((k) => k + 1);
      // A child is not selectable until the refreshed tree carries it, so
      // only a new root is selected here — selecting a stale row would show
      // an empty board and read as a failed create.
      if (!creating.parent) {
        setApp(null);
        setSelected(created);
      }
    } catch (err) {
      // The draft row is KEPT on failure, so the typing is not lost and the
      // refusal is visible beside the thing it refused.
      setError(String((err as Error).message));
    }
  }

  async function submitTask(event: React.FormEvent) {
    event.preventDefault();
    const title = newTask.trim();
    if (!title || !selected) return;
    setNewTask("");
    setError(null);
    try {
      // Status is deliberately not sent: the API picks the project's default,
      // so the browser never has to know which lane a new task starts in.
      const created = await projectsApi.createTask({
        project_id: selected.id,
        title,
      });
      // WS-28e §6.4 — the pre-filled assignee, applied through the SAME
      // assignees PUT the panel uses. Not a create-payload field: one write
      // path for assignment, and the ordinary flow is the point.
      if (prefillAssignee) {
        await projectsApi.setAssignees(created.id, [prefillAssignee]);
      }
      await loadProject(selected);
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  /**
   * WS-27q — a task dragged to another day.
   *
   * A plain `PATCH`, deliberately: the same validation, the same
   * `field_change` activity and the same revert as an edit typed into the
   * panel. A dedicated "move" endpoint would be a second write path, which is
   * how two paths start disagreeing about what is allowed.
   *
   * Optimistic like the board's drop, and for the same reason — a drag that
   * waits for a round trip feels broken even when it is correct. `rescheduleTo`
   * has already refused a no-op, so this never posts an activity saying a task
   * moved to where it already was.
   */
  /**
   * WS-27t — a dependency drawn on the timeline.
   *
   * The SAME endpoint the task panel's dropdown posts to, so the cycle guard,
   * the activity row and the permission check are one implementation. The
   * refusal shown is the gateway's own message — `assert_no_block_cycle`
   * explains a loop better than anything this component could invent, and a
   * second wording would be a second rule to keep in step.
   *
   * **Nothing is rescheduled (D-PM-12).** Creating the link may make the arrow
   * red; that is the whole intended effect.
   */
  /**
   * WS-27bk §9.12.4 — re-parent a node.
   *
   * ⚠️ **A move re-stamps `root_project_id` across the whole subtree**, which
   * is what scopes every task's statuses, types and counter. So this refetches
   * the tree rather than patching it in place — an optimistic edit here would
   * leave the board drawing lanes from the OLD root's status set.
   *
   * Undoable. The inverse is the parent it came from, read before the write.
   */
  async function moveNodeTo(node: ProjectRow, parentId: string | null) {
    const path = pathTo(roots, node.id);
    const from = path.length > 1 ? path[path.length - 2].id : null;
    setMoving(true);
    setError(null);
    try {
      await projectsApi.moveNode(node.id, parentId);
      setMovingNode(null);
      setTreeKey((k) => k + 1);
      undoApi.record({
        label: `moved ${node.name}`,
        undo: async () => {
          await projectsApi.moveNode(node.id, from);
          setTreeKey((k) => k + 1);
        },
        redo: async () => {
          await projectsApi.moveNode(node.id, parentId);
          setTreeKey((k) => k + 1);
        },
      });
    } catch (err) {
      // The server owns the grammar, and its refusal is the one worth
      // showing. The dialog's greying is a courtesy in front of it, never a
      // replacement. So the dialog stays OPEN on a refusal, with the reason
      // beside the board and the choice still made.
      setError(String((err as Error).message));
    } finally {
      setMoving(false);
    }
  }

  async function linkTasks(blockerId: string, blockedId: string) {
    try {
      await projectsApi.createLink(blockerId, blockedId, "blocks");
    } catch (err) {
      setError(String((err as Error).message));
    }
    await loadMonth();
  }

  /**
   * Re-apply a patch and refresh — the body of every undo and redo step.
   *
   * Errors are THROWN rather than swallowed into `setError`, because the undo
   * provider needs the rejection: it puts the step back on the stack so the
   * next Ctrl+Z retries instead of skipping silently past a revert that never
   * landed.
   *
   * The refresh goes through a ref rather than the captured `loadMonth`. These
   * closures can outlive several renders, and a captured one would refetch with
   * whatever window and filters were live when the drag happened.
   */
  async function rewriteTask(
    taskId: string,
    patch: Record<string, unknown>
  ): Promise<void> {
    await projectsApi.patchTask(taskId, patch);
    await refreshRef.current();
  }

  /** The window a date patch needs, or the one we already have. */
  function grownWindow(
    current: TimelineWindow,
    patch: Record<string, string | null>
  ): TimelineWindow {
    let next = current;
    if (patch.start_date) next = windowIncluding(next, patch.start_date);
    if (patch.due_at) next = windowIncluding(next, dayKey(new Date(patch.due_at)));
    return next;
  }

  async function moveTask(task: TaskRow, patch: Record<string, string | null>) {
    setMonth((current) => ({
      ...current,
      rows: current.rows.map((t) => (t.id === task.id ? { ...t, ...patch } : t)),
    }));
    try {
      await projectsApi.patchTask(task.id, patch);
      // Undoable from here on. Recorded only on SUCCESS — a stack entry for a
      // write the server refused would offer to revert a change that never
      // happened. The inverse is captured from the row as it was BEFORE the
      // optimistic edit above, which is why `task` is read and not `month`.
      undoApi.record({
        label: `rescheduled ${task.title}`,
        undo: () => rewriteTask(task.id, inversePatch(task, patch)),
        redo: () => rewriteTask(task.id, patch),
      });
    } catch (err) {
      setError(String((err as Error).message));
    }
    // The timeline's window follows what you schedule. Drag a bar past the
    // window's edge and the next fetch would not return it, so the row
    // disappears for having been moved somewhere the last fetch did not cover.
    // Widening first means the reload includes it.
    if (mode === "timeline") {
      const widened = grownWindow(timeWindow, patch);
      if (widened !== timeWindow) {
        // `loadMonth` is keyed on the window, so setting it IS the reload.
        // Calling both would fire two fetches and let the stale one win.
        setTimeWindow(widened);
        return;
      }
    }
    // Reloaded either way: on success to pick up anything the server derived,
    // on failure to replace the optimistic move with the truth.
    await loadMonth();
  }

  async function handleDrop(
    task: TaskRow,
    writes: ReturnType<typeof planDrop>,
    patch: Record<string, string | number | null> | null
  ) {
    // Optimistic: the card moves now and the truth arrives on reload. A drag
    // that waits for a round trip feels broken even when it is correct. The
    // WHOLE patch applies — a lane-cell drop moves two axes at once (WS-27y).
    if (patch) {
      setTasks((current) =>
        current.map((t) =>
          t.id === task.id ? { ...t, ...(patch as Partial<TaskRow>) } : t
        )
      );
    }
    try {
      if (patch) await projectsApi.patchTask(task.id, patch);
      const rootViews = await projectsApi.views(task.root_project_id);
      const board = orderBearingView(rootViews.rows);
      if (board) await projectsApi.setPositions(board.id, writes);
      if (selected) await loadProject(selected);
    } catch (err) {
      setError(String((err as Error).message));
      if (selected) await loadProject(selected);
    }
  }

  if (loading) return renderState("loading", LOADING_COPY, "page");

  // ── The parts both layouts render ────────────────────────────────────────
  // Built once here rather than twice in the two branches below: a phone and a
  // desktop showing two different apps is how "responsive" turns into two
  // codebases. What genuinely differs is chrome — a rail versus a drawer, a
  // docked panel versus a full-screen one — and only that is written twice.

  /**
   * The draft handed to the tree, which draws it AS A ROW at the position
   * the new node will occupy (owner directive 2026-08-31).
   *
   * ⚠️ There used to be a `projectForm` here — a detached input pinned
   * above the tree, saying "New folder in Firmware" because it sat four
   * rows away from Firmware and had to name the parent in words. The row
   * knows its own parent by being indented under it, so the sentence is
   * unnecessary, and the field belongs where the thing will be.
   */
  const treeDraft = creating
    ? {
        parentId: creating.parent?.id ?? null,
        kind: creating.kind,
        label: creating.label,
        level: creating.level,
      }
    : null;

  /** What the *selected project* offers — the action half of the old header.
   *  `compact` drops the labels for the phone's title row; the set is the same
   *  on both, so nothing is quietly unreachable on a phone. */
  /**
   * The header's action cluster — ONE overflow menu (owner ask 2026-08-31).
   *
   * Plane's header keeps management out of the view chrome entirely: its
   * topbar is breadcrumb, layout switcher, filters, display, one primary
   * action — Fields/Tags/Lifecycle-style dialogs live behind menus and
   * settings (`apps/web/core/components/issues/header.tsx` at effd0c5 is
   * the pattern). Three always-visible ghost buttons beside the view
   * switcher were the junk drawer that rule exists to prevent. All three
   * remain one palette command away (`project.fields` / `project.tags` /
   * `project.lifecycle`).
   *
   * ⚠️ The "Import from ClickUp" action was REMOVED 2026-08-24 (D52, board
   * WS-39 S1). Metorite is the system of record — nothing to import from.
   */
  const projectActions = (compact: boolean) =>
    selected ? (
      <>
      {/* Undo/redo sits with the VIEW ACTIONS, not in the filter bar: it acts
          on the project, not on what is on screen, and the filter bar is
          where you narrow rather than where you change things. Beside the
          overflow menu it is the first thing to hand after a drag. */}
      <UndoControls />
      <div ref={manageRef} className="relative">
        <Button
          variant="ghost"
          size={compact ? "icon-sm" : "sm"}
          icon="MoreHorizontal"
          aria-label="Manage this project"
          aria-expanded={manageOpen}
          title="Custom fields, tags and lifecycle"
          onClick={() => setManageOpen((open) => !open)}
        />
        {manageOpen ? (
          <div
            className="absolute right-0 z-20 mt-1 w-48 rounded-lg border border-border bg-popover p-1 shadow-md"
            role="menu"
            onKeyDown={(e) => {
              if (e.key === "Escape") setManageOpen(false);
            }}
          >
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-foreground hover:bg-muted"
              onClick={() => {
                setManageOpen(false);
                setManagingFields(true);
              }}
            >
              <Icon name="SlidersHorizontal" className="h-3.5 w-3.5 text-muted-foreground" />
              Custom fields
            </button>
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-foreground hover:bg-muted"
              onClick={() => {
                setManageOpen(false);
                setManagingTags(true);
              }}
            >
              <Icon name="Tag" className="h-3.5 w-3.5 text-muted-foreground" />
              Tags
            </button>
            {!selected.parent_project_id ? (
              <button
                type="button"
                role="menuitem"
                className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-foreground hover:bg-muted"
                onClick={() => {
                  setManageOpen(false);
                  setManagingLifecycle(true);
                }}
              >
                <Icon name="Archive" className="h-3.5 w-3.5 text-muted-foreground" />
                Lifecycle policy
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      </>
    ) : null;

  const title = app
    ? PROJECT_APP_SECTIONS.flatMap((s) => s.items).find((i) => i.id === app)
        ?.label ?? "Projects"
    : selected?.name ?? "No project selected";
  // A parent project says what it is AGGREGATING (owner directive
  // 2026-08-31: *"when a project contains sub-projects, selecting the
  // project will aggregate the sub-project data into the project view"*).
  // The views already include the subtree — this is the line that tells the
  // reader those numbers are not this project's alone, which is otherwise
  // an invisible difference between two identical-looking boards.
  const aggregateNote =
    selectedLevel === "project" && (summary?.children.length ?? 0) > 0
      ? `Includes ${summary!.projects} subproject${
          summary!.projects === 1 ? "" : "s"
        }`
      : null;

  const subtitle = app
    ? PROJECT_APP_SECTIONS.flatMap((s) => s.items).find((i) => i.id === app)
        ?.note ?? null
    : [selected?.description, aggregateNote].filter(Boolean).join(" · ") || null;

  /**
   * WS-27am — which canvas is on screen, in the user's words. It labels the
   * error boundary's fallback, so a failure says *which* view stopped rendering
   * rather than "Projects broke".
   */
  const canvasLabel = !selected ? "Projects" : mode;

  /**
   * …and its identity, which is what actually makes the boundary recoverable.
   *
   * A boundary keyed on nothing stays broken until somebody presses Retry —
   * including after the user has already navigated to data that is fine. Keying
   * it by layout AND project means the two escapes the fallback's own copy
   * offers ("switch view or pick another project") really do clear it, because
   * either one mounts a boundary React has never seen.
   */
  const canvasKey = `${selected?.id ?? "none"}:${canvasLabel}`;

  /** Everything between the chrome and the canvas, plus the canvas. */
  const workArea = app === "ai-chat" ? (
    // Unreachable today — the sidebar disables a `preview` entry. Written
    // anyway so the destination exists the moment the flag flips, and so
    // "not built" is a surface rather than a blank pane.
    renderState("empty", "AI chat is not built yet.")
  ) : app === "analytics" ? (
    // Analytics — the portfolio roll-up in Plane's shape: a KPI strip over
    // a per-space state matrix (see AnalyticsView's header for sources).
    // Same endpoint as the dashboards, so the two cannot disagree.
    <>
      {shownError ? renderState("error", shownError) : null}
      {portfolio ? (
        <AnalyticsView
          summary={portfolio}
          onOpen={(id) => {
            const row = flatten(visibleRoots).find((e) => e.node.id === id);
            if (row) {
              setApp(null);
              setSelected(row.node as ProjectRow);
            }
          }}
        />
      ) : (
        renderState("loading", "Counting every space…")
      )}
    </>
  ) : dashboardOnly ? (
    // A SPACE IS NOT A PROJECT (owner directive 2026-08-31). It shows a
    // roll-up of everything beneath it and none of a project's machinery —
    // no filter bar, no view tabs, no task composer, no triage rail, no
    // bulk bar. A folder is the same. Returning early rather than hiding
    // each piece: six `&&`s would leave the next control somebody adds
    // showing up here by default, and the default must be "not on a space".
    <>
      {shownError ? renderState("error", shownError) : null}
      {summary ? (
        <NodeDashboard
          summary={summary}
          onOpen={(id) => {
            const row = flatten(visibleRoots).find((e) => e.node.id === id);
            if (row) setSelected(row.node as ProjectRow);
          }}
        />
      ) : (
        renderState("loading", "Counting the work below…")
      )}
    </>
  ) : (
    <>
      {shownError ? renderState("error", shownError) : null}

      {selected && !overview ? (
        <FilterBar
          filters={filters}
          onFilters={changeFilters}
          mode={mode}
          spansProjects={spansProjects}
          groupBy={groupBy}
          onGroupBy={(next) => {
            setGroupBy(next);
            // The new main axis may be the current sub-axis; lanes of the
            // board's own columns mean nothing, so they reset.
            setLanes((current) =>
              current.subGroupBy === next
                ? { ...current, subGroupBy: "none", collapsedLanes: [] }
                : current
            );
          }}
          lanes={lanes}
          onSubGroupBy={(next) => {
            // Collapsed-lane keys belong to the axis that made them.
            setLanes((current) => ({
              ...current,
              subGroupBy: next,
              collapsedLanes: [],
            }));
          }}
          me={me}
          people={people}
          tags={tags}
          shownFields={shownFields}
          onShownFields={changeShownFields}
          fields={fields}
          // The project's order-bearing board is withheld from the chips
          // entirely: it is not a saved filter, and offering its ✕ would
          // offer to delete every hand-arranged position on the project.
          views={views.filter((v) => v.id !== orderBearingView(views)?.id)}
          activeViewId={activeViewId}
          onApplyView={applyView}
          onSaveView={(name) => void saveView(name)}
          onDeleteView={(view) => void deleteView(view)}
          onUpdateView={(view) => void updateView(view)}
          canSave={Boolean(selected)}
          onExport={exportCsv}
        />
      ) : null}

      {selected && !overview && picked.size > 0 ? (
        <BulkBar
          count={picked.size}
          statuses={statuses}
          busy={bulkBusy}
          notice={bulkNotice}
          onClear={() => {
            setPicked(new Set());
            setAnchor(null);
            setBulkNotice(null);
          }}
          onApply={(request) => void applyBulk(request)}
        />
      ) : null}

      {selected &&
      !overview &&
      nodeKind(selected) !== "folder" &&
      (mode === "timeline" || prefillAssignee) ? (
        // Capture-first, but WHERE work lands (owner ask 2026-08-31,
        // Plane's discipline — no global composer above a board that
        // captures per column). Board, list, table and calendar each carry
        // their own QuickAdd, which also inherits the group it sits in, so
        // this bar was a second, worse door on those canvases. It stays on
        // TIMELINE (the one canvas with no in-place capture) and whenever
        // the People Center's "Assign work" pre-fill needs somewhere
        // visible to land. Everything else about a task — status,
        // assignee, subtasks — is set from the panel once it exists. A
        // FOLDER offers no composer at all: it holds projects, not tasks
        // (migration 193), and the server refuses the write.
        <form onSubmit={submitTask} className="border-b border-border px-3 py-2">
          {prefillAssignee ? (
            // §6.4: the pre-fill is VISIBLE and dismissible — silently
            // assigning every new task to somebody is how work lands on the
            // wrong desk with nobody able to say why.
            <p className="mb-1.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Icon name="UserPlus" className="size-3 shrink-0" />
              New tasks will be assigned to{" "}
              <span className="text-foreground">{prefillAssignee}</span>
              <button
                type="button"
                aria-label="Stop pre-assigning"
                onClick={() => setPrefillAssignee(null)}
                className="opacity-70 hover:opacity-100"
              >
                <Icon name="X" className="size-3" />
              </button>
            </p>
          ) : null}
          <input
            value={newTask}
            onChange={(e) => setNewTask(e.target.value)}
            placeholder={
              prefillAssignee
                ? `New task for ${prefillAssignee} in ${selected.name}…`
                : `New task in ${selected.name}…`
            }
            aria-label="New task title"
            className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm text-foreground"
          />
        </form>
      ) : null}

      {/* WS-27u — the front door. Renders nothing when the queue is empty;
          a ruling reloads the board because an accept just added a card. */}
      {selected && !overview ? (
        <TriageRail
          projectId={selected.id}
          statuses={statuses}
          onOpenTask={(id) => void openTaskById(id)}
          onResolved={() => {
            if (selected) void loadProject(selected);
          }}
        />
      ) : null}

      <div className="min-h-0 flex-1 overflow-auto">
        <LayoutBoundary key={canvasKey} layout={canvasLabel}>
          {!selected ? (
            renderState(
              "empty",
              "Nothing here yet. Projects appear once a space is granted to you."
            )
          ) : mode === "overview" ? (
            summary ? (
              <NodeDashboard
                summary={summary}
                onOpen={(id) => {
                  const row = flatten(visibleRoots).find((e) => e.node.id === id);
                  if (row) setSelected(row.node as ProjectRow);
                }}
              />
            ) : (
              renderState("loading", "Counting the work below…")
            )
          ) : mode === "timeline" ? (
            <TimelineView
              tasks={month.rows}
              links={month.links}
              undated={month.undated}
              truncated={month.truncated}
              today={dayKey(new Date())}
              shownFields={shownFields}
              tags={tags}
              zoom={zoom}
              window={timeWindow}
              // S5 — the same grouping the board and list read. `groups` is
              // built from the BOARD's task list, so the timeline is grouped
              // from `month.rows` instead: the two canvases load different
              // windows, and grouping one by the other's rows would silently
              // drop everything outside it.
              groupBy={groupBy}
              groups={monthGroups}
              statuses={statuses}
              onZoom={(next) => {
                // The window is re-scoped around what you are LOOKING at, not
                // around today: changing zoom to see more context should not
                // also teleport you out of the quarter you were reading.
                setZoom(next);
                setTimeWindow((current) => windowFor(next, windowCentre(current)));
              }}
              onSelect={(task) => void openWithStatuses(task)}
              onMove={(task, patch) => void moveTask(task, patch)}
              onLink={(blockerId, blockedId) => void linkTasks(blockerId, blockedId)}
              onRefuse={(reason) => setError(reason)}
            />
          ) : mode === "calendar" ? (
            <CalendarView
              grid={grid}
              tasks={month.rows}
              undated={month.undated}
              truncated={month.truncated}
              today={dayKey(new Date())}
              projectId={selected.id}
              shownFields={shownFields}
              tags={tags}
              onCreated={() => void loadMonth()}
              onSelect={(task) => void openWithStatuses(task)}
              onMove={(task, patch) => void moveTask(task, patch)}
              onStep={(steps) => setMonthAnchor(shiftGrid(grid, steps))}
              onToday={() => setMonthAnchor(new Date())}
              onLayout={setCalLayout}
              onRefuse={(reason) => setError(reason)}
            />
          ) : mode === "table" ? (
            <TableView
              groups={groups}
              groupBy={groupBy}
              statuses={statuses}
              fields={fields}
              shownFields={shownFields}
              sort={tableSort}
              onSort={setTableSort}
              projectId={selected.id}
              onCreated={() => void loadProject(selected)}
              onSaved={(fresh) =>
                setTasks((current) =>
                  current.map((t) => (t.id === fresh.id ? { ...t, ...fresh } : t))
                )
              }
              onSelect={(task) => void openWithStatuses(task)}
            />
          ) : mode === "board" ? (
            <TaskBoard
              groups={groups}
              groupBy={groupBy}
              // S4 — the empty state has to know whether the filters emptied it.
              filters={filters}
              onClearFilters={() => changeFilters(EMPTY_FILTERS)}
              lanes={lanes}
              onToggleLane={(key) =>
                setLanes((current) => ({
                  ...current,
                  collapsedLanes: toggleLane(current.collapsedLanes, key),
                }))
              }
              onShowEmptyLanes={(show) =>
                setLanes((current) => ({ ...current, showEmptyLanes: show }))
              }
              statuses={statuses}
              tags={tags}
              projectName={projectName}
              projectId={selected.id}
              shownFields={shownFields}
              onCreated={() => void loadProject(selected)}
              selected={picked}
              onToggle={toggleSelection}
              onExtendSelection={extendSelection}
              onSelect={(task) => void openWithStatuses(task)}
              onDrop={handleDrop}
            />
          ) : (
            <TaskList
              groups={groups}
              groupBy={groupBy}
              filters={filters}
              onClearFilters={() => changeFilters(EMPTY_FILTERS)}
              statuses={statuses}
              tags={tags}
              projectId={selected.id}
              shownFields={shownFields}
              onCreated={() => void loadProject(selected)}
              selected={picked}
              onToggle={toggleSelection}
              allChecked={everySelected(picked, onScreen)}
              onToggleAll={() =>
                setPicked(
                  everySelected(picked, onScreen) ? new Set() : new Set(onScreen)
                )
              }
              onExtendSelection={extendSelection}
              onSelect={(task) => void openWithStatuses(task)}
            />
          )}
        </LayoutBoundary>
      </div>
    </>
  );

  /**
   * WS-27ab — the panel, at whichever of the three stops is chosen.
   *
   * `mode`/`onMode` are passed only on desktop: a phone's panel already IS the
   * screen, and three width buttons there would be a control that changes
   * nothing. The escalation is one prop pair, not a second component — see
   * `lib/panelMode.ts`.
   */
  const taskPanel = openTask ? (
    <TaskPanel
      task={openTask}
      statuses={panelStatuses}
      fields={fields}
      tags={tags}
      mode={isMobile ? undefined : panelMode}
      onMode={isMobile ? undefined : setPanelMode}
      // WS-27p — opening a subtask or a linked task resolves ITS project's
      // statuses, which the panel has no tree to do.
      onOpenTask={(id) => void openTaskById(id)}
      onClose={() => setOpenTask(null)}
      onTaskAdded={() => {
        if (selected) void loadProject(selected);
      }}
      onChanged={(fresh) => {
        setOpenTask(fresh);
        setTasks((current) =>
          current.map((t) => (t.id === fresh.id ? { ...t, ...fresh } : t))
        );
      }}
    />
  ) : null;

  /** Everything that floats above the layout, in ONE place — so the two
   *  branches cannot end up offering different dialogs. */
  const overlays = (
    <>
      <SearchPalette
        open={searching}
        onClose={() => setSearching(false)}
        onOpenTask={(id) => void openTaskById(id)}
        actions={actions}
        context={commandCtx}
      />

      {/* WS-27ab — `?`, and the palette's own Keyboard-shortcuts command. The
          sheet is PRINTED from the registry, so it cannot advertise a key the
          keyboard does not honour. */}
      {showingShortcuts ? (
        <ShortcutsSheet onClose={() => setShowingShortcuts(false)} />
      ) : null}

      {managingTags && selected ? (
        <TagManager
          projectId={selected.id}
          projectName={selected.name}
          onClose={() => setManagingTags(false)}
          onChanged={setTags}
          // A rename or merge rewrites task rows, so the board is stale until
          // it reloads — the chips would otherwise show a name no card carries.
          onTasksTouched={() => {
            if (selected) void loadProject(selected);
          }}
        />
      ) : null}

      {managingLifecycle && selected ? (
        <LifecyclePolicy
          project={selected}
          onClose={() => setManagingLifecycle(false)}
          onSaved={(fresh) => {
            // The header's selected row keeps the fresh values; the tree
            // re-reads so its copy does not disagree on the next select.
            setSelected((current) =>
              current && current.id === fresh.id ? { ...current, ...fresh } : current
            );
            setTreeKey((k) => k + 1);
          }}
        />
      ) : null}

      {managingFields && selected ? (
        <FieldManager
          projectId={selected.id}
          projectName={selected.name}
          onClose={() => setManagingFields(false)}
          // Kept in sync while the dialog is open, so a field added here shows
          // on the next task opened without closing anything first.
          onChanged={setFields}
        />
      ) : null}

      {/* ── SEAM (WS-27ag) ────────────────────────────────────────────────
          The shared <Toast> mounts HERE, above both layouts and below every
          dialog — one mount point, so a notice raised from the phone and one
          raised from the desktop land in the same place. Nothing renders it
          yet: `bulkNotice` still goes to <BulkBar> and `error` to the strip in
          `workArea`, and the slice that owns Toast moves them. **Advisory:**
          no test fences this position — the tree has no layout test at all. */}
    </>
  );

  // ── Phone ────────────────────────────────────────────────────────────────
  // One pane. The tree and the mode picker are sheets in the shell drawer
  // (AppShell's `isProjectsPage` tabs), and an opened task is a full-screen
  // surface rather than the third column it is on desktop.
  if (isMobile) {
    return (
      <div className="flex h-full w-full flex-col overflow-hidden bg-background">
        <div className="flex h-10 shrink-0 items-center gap-1 border-b border-border bg-card px-2">
          <h1 className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
            {title}
          </h1>
          <div className="flex shrink-0 items-center gap-0.5">
            {projectActions(true)}
            <NotificationBell onOpenTask={openTaskById} />
          </div>
        </div>

        {workArea}

        {taskPanel ? (
          // The panel's own `max-w-md` is a docked-column width; on a phone the
          // surface IS the screen, so the cap is lifted here rather than in the
          // panel, which knows nothing about the shell. `z-[60]` clears the
          // bottom nav (z-50); the panel closes from its own ✕.
          <div className="fixed inset-0 z-[60] flex bg-background pt-safe pb-safe [&>aside]:max-w-none">
            {taskPanel}
          </div>
        ) : null}

        {overlays}
      </div>
    );
  }

  // ── Desktop ──────────────────────────────────────────────────────────────
  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      {/* The house shell: a slim h-10 bar carrying the rail toggle, a divider,
          the app's name and the app-LEVEL actions. Same shape as Tasks and
          Email; what used to live here — six unrelated controls in one row —
          is now split between this bar (app scope) and the header below
          (project scope). */}
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border bg-card px-2">
        <Button
          variant={railOpen ? "secondary" : "ghost"}
          size="icon-sm"
          icon={railOpen ? "PanelLeftClose" : "PanelLeftOpen"}
          aria-label={railOpen ? "Hide the project tree" : "Show the project tree"}
          aria-pressed={railOpen}
          onClick={() => setRailOpen((v) => !v)}
        />
        <div className="h-4 w-px bg-border" />
        {/* The page's <h1>. The project name below is an <h2>, as it was. */}
        <h1 className="shrink-0 text-xs font-medium text-muted-foreground">Projects</h1>
        <span className="min-w-0 truncate text-xs text-muted-foreground">
          {center ? `${center} Center's slice` : "Every space you can see"}
        </span>
        <div className="ml-auto flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            icon="Search"
            onClick={() => setSearching(true)}
            title="Search every project (⌘K)"
          >
            Search
          </Button>
          <NotificationBell onOpenTask={openTaskById} />
        </div>
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        {railOpen ? (
          <nav className="w-60 shrink-0 overflow-y-auto border-r border-border bg-card p-2">
            <ProjectNav
              roots={visibleRoots}
              selectedId={selected?.id ?? null}
              app={app}
              onApp={setApp}
              onSelect={(project) => {
                setApp(null);
                setSelected(project);
              }}
              onAddChild={(parent, option) => {
                setCreating({ parent, kind: option.kind, label: option.label, level: option.level });
              }}
              onOpenSettings={setSettingsFor}
              onMove={setMovingNode}
              onNewSpace={() => {
                setCreating({
                  parent: null, kind: "project",
                  label: "New space", level: "space",
                });
              }}
              creating={treeDraft}
              onCommitCreate={(name) => void submitProject(name)}
              onCancelCreate={() => setCreating(undefined)}
              actions={projectMenuActions}
            />
          </nav>
        ) : null}

        <main className="flex min-w-0 flex-1 flex-col">
          <header className="shrink-0 border-b border-border">
            {/* Title row — what you are looking at, and nothing else. */}
            <div className="flex min-w-0 items-baseline gap-2 px-3 pt-2">
              <h2 className="min-w-0 truncate text-sm font-medium text-foreground">
                {title}
              </h2>
              {subtitle ? (
                <p className="min-w-0 truncate text-xs text-muted-foreground">
                  {subtitle}
                </p>
              ) : null}
            </div>
            {/* Action row — how you look at it (left) and what you can do to
                it (right). */}
            {/* A space and a folder have no views to switch between and no
                project actions to offer, so the whole action row goes —
                leaving an empty strip would look like a surface that failed
                to load. */}
            {noProjectChrome ? null : (
              <div className="flex items-center gap-1 px-3 pb-2 pt-1.5">
                <ModeSwitch
                  mode={mode}
                  layout="toolbar"
                  onPick={(next) => setChosenMode(next)}
                />
                <div className="ml-auto flex shrink-0 items-center gap-1">
                  {projectActions(false)}
                </div>
              </div>
            )}
          </header>

          {workArea}
        </main>

        {/* Peek and side DOCK — a third column beside the canvas, narrow or
            wide. Full does not: it is mounted over the board below, because a
            docked column cannot be wider than the space left over. */}
        {isOverlayMode(panelMode) ? null : taskPanel}
      </div>

      {/* WS-27ab — the `full` stop. A scrim plus the same panel, at the same
          `max-w-3xl` reading width `/tasks`' maximise-out-of-the-pane uses.
          Clicking the scrim closes it; the panel keeps its own ✕ and Escape. */}
      {taskPanel && isOverlayMode(panelMode) ? (
        <div
          className="fixed inset-0 z-50 flex items-stretch justify-center bg-background/70 p-4 sm:p-8"
          role="presentation"
          onClick={() => setOpenTask(null)}
        >
          <div
            className="flex min-h-0 w-full max-w-3xl overflow-hidden rounded-lg border border-border shadow-lg"
            onClick={(event) => event.stopPropagation()}
            role="presentation"
          >
            {taskPanel}
          </div>
        </div>
      ) : null}

      {/* Space Settings — name, icon, icon colour (migration 194). Mounted
          at the page root rather than inside the tree: the tree is drawn
          twice (rail and drawer), and a dialog inside it would be too. */}
      <SpaceSettings
        space={settingsFor}
        onClose={() => setSettingsFor(null)}
        onSave={(space, values) => void saveSpaceSettings(space, values)}
      />

      {/* WS-27bk §9.12.4 — "Move to…". Mounted here for the same reason Space
          Settings is: the tree is drawn twice (rail and drawer), and a dialog
          inside it would be too. */}
      {movingNode ? (
        <MoveDialog
          open
          moving={movingNode}
          roots={roots}
          busy={moving}
          onClose={() => setMovingNode(null)}
          onMove={(parentId) => void moveNodeTo(movingNode, parentId)}
        />
      ) : null}

      {overlays}
    </div>
  );
}

export default function ProjectsPage() {
  // `useSearchParams` needs a Suspense boundary in the App Router.
  return (
    <Suspense fallback={renderState("loading", LOADING_COPY, "page")}>
      <ProjectsWorkspace />
    </Suspense>
  );
}
