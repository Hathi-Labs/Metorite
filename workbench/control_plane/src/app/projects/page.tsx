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
import AssistantToggle from "@/components/AssistantToggle";
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
  type TaskTypeRow,
  type NodeSummary,
  type StuckReport,
  type LoadReport,
  type OutlookReport,
  type CapacityReport,
  type ConflictsReport,
  type ThroughputReport,
  type FinishedReport,
  type ViewRow,
  projectsApi,
  projectsKey,
  projectWatchersApi,
  type ProjectWatchState,
  PROJECTS_CACHE,
} from "./lib/api";
import { FieldManager } from "./components/FieldManager";
import { DeleteProjectDialog } from "./components/DeleteProjectDialog";
import { MoveDialog } from "./components/MoveDialog";
import { MergeTasksDialog } from "./components/MergeTasksDialog";
import { MoveTasksDialog } from "./components/MoveTasksDialog";
import { type TreeDropTarget, planTreeDrop } from "./lib/treeDrop";
import { LifecyclePolicy } from "./components/LifecyclePolicy";
import { StatusManager } from "./components/StatusManager";
import { TagManager } from "./components/TagManager";
import { BulkBar } from "./components/BulkBar";
import { FilterBar } from "./components/FilterBar";
import { NotificationBell } from "./components/NotificationBell";
import { type CreatingDraft, ProjectTree } from "./components/ProjectTree";
import type { ProjectMenuHandlers } from "./lib/projectMenu";
import { CalendarView } from "./components/CalendarView";
import { MoreTasksBar } from "./components/MoreTasksBar";
import { SearchPalette } from "./components/SearchPalette";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { deleteTaskCopy, deleteTasksCopy } from "./lib/deleteCopy";
import { TimelineView } from "./components/TimelineView";
import { TableView } from "./components/TableView";
import { TaskBoard } from "./components/TaskBoard";
import { TaskList } from "./components/TaskList";
import { TaskPanel } from "./components/TaskPanel";
import { ShortcutsSheet } from "./components/ShortcutsSheet";
import { TriageRail } from "./components/TriageRail";
import { AssistantRail } from "./components/AssistantRail";
import SidePanelEditor from "@/components/SidePanelEditor";
import {
  getState as getSidePanelState,
  subscribe as subscribeSidePanel,
} from "@/lib/sidePanelStore";
import {
  BOARD_MIN_REM,
  SidePanelFitContext,
  sidePanelFits,
} from "@/lib/sidePanelFit";
import { PROJECTS_CHANGED_EVENT } from "@/components/projects/ProjectToolCards";
import { invalidate } from "@/lib/dataCache";
import { useFrontendTool } from "@/hooks/useFrontendTool";
import { SAVED_VIEW_POSITION, orderBearingView, type planDrop } from "./lib/board";
import { TASK_PAGE_SIZE, appendTasks, nextTaskPage } from "./lib/paging";
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
  labelPeople,
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
  spaceOf,
} from "./lib/tree";
import AnalyticsView from "./components/AnalyticsView";
import ReportsView from "./components/ReportsView";
import NodeDashboard from "./components/NodeDashboard";
import SpaceSettings from "./components/SpaceSettings";
import {
  chatEnabled,
  projectAppSections,
  type ProjectAppId,
  SPACES_SECTION_LABEL,
} from "./lib/projectApps";
import {
  DOCK_QUERY,
  chatDockState,
  readChatDocked,
  assistantButton,
  writeChatDocked,
} from "./lib/chatDock";

/**
 * The sidebar's own destinations, with the flagged entries resolved
 * (WS-27bm — `NEXT_PUBLIC_PROJECTS_CHAT` flips "AI chat" to live). Read once:
 * `NEXT_PUBLIC_*` is inlined at build time, so this cannot change at runtime.
 */
const PROJECT_APP_SECTIONS = projectAppSections();
/** The same flag, as the dock reads it (`lib/chatDock.ts`). */
const CHAT_LIVE = chatEnabled();

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
  /** P-22 — the undated tasks themselves. The timeline gives each one a row. */
  unscheduled: [] as TaskRow[],
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
  onDropNode,
  onNewSpace,
  creating,
  onCommitCreate,
  onCancelCreate,
  onPicked,
  actions,
  onManageStatuses,
  onManageFields,
  onManageTags,
  onManageLifecycle,
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
  /** WS-27bk §9.12.4 slice 2 — a completed drag in the rail. */
  onDropNode: (movingId: string, target: TreeDropTarget) => void;
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
  /**
   * The four ROOT-scoped screens, offered on a space row's menu. Each takes
   * the row the menu opened from, NOT the selected project — the whole point
   * of a row menu is that it acts on the row you pointed at.
   */
  onManageStatuses: (space: ProjectRow) => void;
  onManageFields: (space: ProjectRow) => void;
  onManageTags: (space: ProjectRow) => void;
  onManageLifecycle: (space: ProjectRow) => void;
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
        onDropNode={onDropNode}
        creating={creating}
        onCommitCreate={onCommitCreate}
        onCancelCreate={onCancelCreate}
        // ⚠️ `onDelete` is wrapped for the reason the block below states in
        // full: it RAISES A DIALOG, and on a phone this tree is the drawer
        // sheet, so the dialog would open behind it. Every other entry in
        // `actions` writes and closes its own menu — those are safe as they
        // are, and wrapping them would close the drawer for a state change
        // somebody wants to make twice in a row.
        actions={
          actions
            ? {
                ...actions,
                ...(actions.onDelete
                  ? {
                      onDelete: (project: ProjectRow, level: NodeLevel) => {
                        actions.onDelete?.(project, level);
                        onPicked?.();
                      },
                    }
                  : {}),
              }
            : undefined
        }
        // Each closes the phone's drawer, like every other row action here —
        // a dialog opening behind an open drawer is a dialog nobody can see.
        onManageStatuses={(space) => {
          onManageStatuses(space);
          onPicked?.();
        }}
        onManageFields={(space) => {
          onManageFields(space);
          onPicked?.();
        }}
        onManageTags={(space) => {
          onManageTags(space);
          onPicked?.();
        }}
        onManageLifecycle={(space) => {
          onManageLifecycle(space);
          onPicked?.();
        }}
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
        <Button
          key={entry.id}
          variant="ghost"
          size={sheet ? "lg" : "sm"}
          selected={mode === entry.id}
          icon={entry.icon}
          onClick={() => onPick(entry.id)}
          className="capitalize"
        >
          {entry.id}
        </Button>
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
  /**
   * How many tasks exist under the current filters, or null before the read.
   *
   * 🔴 **The board holds a PAGE, and until 2026-09-22 it never said so.**
   * `GET /projects/tasks` caps a page at `MAX_PAGE_SIZE` and answers no
   * cursor, so a 150-task project drew 100 rows, headed the lane "To do 100",
   * and ended with the ordinary "+ Add". Fifty tasks were unreachable from the
   * board, the list and the table, while the sidebar, the overview and the
   * timeline all read the true 150 — the timeline because it loads from
   * `/calendar`, a different endpoint with a different cap. One store, three
   * lenses (D52/D53/D54), and the lenses disagreed about how much work exists.
   *
   * Keeping `total` beside the rows is what lets `MoreTasksBar` say "Showing
   * 100 of 150" and go and get the rest. See `lib/paging.ts`.
   */
  const [taskTotal, setTaskTotal] = useState<number | null>(null);
  /** A `Load more` read is in flight. Separate from the board's own loading. */
  const [loadingMore, setLoadingMore] = useState(false);
  /**
   * How many tasks are on the shelf for the board in view, or null.
   *
   * ⚠️ **Without this the archive is invisible until you go looking.** An
   * archived task leaves the board and the current-state reports, so a
   * member reading "6 open" has no way to know whether that is the whole
   * story or whether forty more are filed just out of sight. The chip
   * carries the number so the shelf announces itself.
   *
   * Counted with the SAME filters as the board, so it answers exactly "how
   * many would I see if I clicked this" — a count that ignored the tag
   * filter beside it would send people to an empty view.
   */
  const [archivedCount, setArchivedCount] = useState<number | null>(null);
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
  // WS-27bm — the AI chat docked beside the board (`lib/chatDock.ts`). Read in
  // an effect for the reason `panelMode` is: no storage on the server.
  const [chatDocked, setChatDocked] = useState(false);
  // Wide enough to dock — the same media query the CSS `xl` is, subscribed,
  // so the column follows the window and a narrow one mounts no rail at all.
  const [dockWide, setDockWide] = useState(false);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setChatDocked(readChatDocked());
    const mql = window.matchMedia(DOCK_QUERY);
    const onChange = () => setDockWide(mql.matches);
    onChange();
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
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
  //: H-8 — the row whose delete confirmation is open, and whether the call is
  //: in flight. Shaped exactly like the move pair above, because the two are
  //: the same interaction: a row menu raises a dialog, the page owns the write.
  const [deletingNode, setDeletingNode] = useState<
    { project: ProjectRow; level: NodeLevel } | null
  >(null);
  const [deleting, setDeleting] = useState(false);
  //: The server's refusal, held HERE rather than in the page's `error` strip.
  //: That strip renders inside the work area, under this modal's backdrop, so
  //: a 403 re-armed the button and said nothing.
  const [deleteError, setDeleteError] = useState<string | null>(null);
  //: Bumped on every open. The `key` above needs it: closing and reopening the
  //: SAME row leaves the id unchanged, so the id alone would not remount and
  //: the stale-armed frame would survive exactly where it is easiest to hit.
  const [deleteOpenedAt, setDeleteOpenedAt] = useState(0);
  //: WS-27bl — the tasks whose move card is open. One state for both entry
  //: points: a single row from its menu, and the whole bulk selection.
  const [movingTasks, setMovingTasks] = useState<readonly string[] | null>(null);
  const [movingTasksBusy, setMovingTasksBusy] = useState(false);
  const [moveTasksError, setMoveTasksError] = useState<string | null>(null);
  //: The tasks whose MERGE card is open. One state for both entry points,
  //: exactly as `movingTasks` above: a single row from its right-click menu,
  //: and the whole bulk selection.
  const [mergingTasks, setMergingTasks] = useState<readonly string[] | null>(null);
  const [mergeBusy, setMergeBusy] = useState(false);
  const [mergeError, setMergeError] = useState<string | null>(null);
  // Analytics reads the portfolio roll-up — the same shape as a node's, so
  // one dashboard component draws both.
  const [portfolio, setPortfolio] = useState<NodeSummary | null>(null);
  // §9.12.7's three reads. Held SEPARATELY rather than in one object: each
  // one can fail or arrive on its own, and a single slot would make the whole
  // dashboard wait for the slowest of them.
  const [stuck, setStuck] = useState<StuckReport | null>(null);
  const [load, setLoad] = useState<LoadReport | null>(null);
  const [outlook, setOutlook] = useState<OutlookReport | null>(null);
  // WS-27bm S7a. The Analytics app's own read — the node dashboards do not
  // draw it, so it is not fetched for them.
  const [capacity, setCapacity] = useState<CapacityReport | null>(null);
  // WS-27bm S7c. The Analytics app's own read too, drawn beside Capacity.
  const [conflicts, setConflicts] = useState<ConflictsReport | null>(null);
  const [throughput, setThroughput] = useState<ThroughputReport | null>(null);
  const [finished, setFinished] = useState<FinishedReport | null>(null);
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
  // ⚠️ These four hold the NODE the dialog is for, not a boolean (owner
  // directive 2026-09-06). They used to be flags, and the dialog read
  // `selected` — which was right while the header's overflow menu was the only
  // door, because that menu IS the selected project's. A tree row's menu is
  // not: right-clicking a space you have not selected must manage THAT space,
  // and a flag plus `selected` would quietly have managed the other one.
  const [managingFields, setManagingFields] = useState<ProjectRow | null>(null);

  // WS-27m — the selected node's tag registry. Root-scoped like the fields, and
  // held here for the same reason: the filter bar, the panel's picker and the
  // manager all read it, and three fetches of one list would disagree.
  const [tags, setTags] = useState<TagRow[]>([]);
  //: WS-27bh — the selected root's EFFECTIVE task types, so a card can draw
  //: what a task IS rather than the uuid it carries.
  const [taskTypes, setTaskTypes] = useState<TaskTypeRow[]>([]);
  const [managingTags, setManagingTags] = useState<ProjectRow | null>(null);

  // The status editor (owner directive 2026-09-03). `statuses` above is already
  // held here for the board's lanes, so the dialog writes through the same
  // state and a rename relabels every lane without a refetch.
  //
  // Reachable from any node, unlike the lifecycle policy below: the gateway
  // resolves the root itself (`admin._root_for`), so a subproject opens its
  // space's set rather than being refused. That is the point — one set per
  // space is what keeps two spaces comparable through the category.
  const [managingStatuses, setManagingStatuses] = useState<ProjectRow | null>(null);

  // WS-27z — the lifecycle-policy dialog. Root projects only: the policy is a
  // root setting the whole subtree inherits, and the gateway 422s a child.
  const [managingLifecycle, setManagingLifecycle] = useState<ProjectRow | null>(
    null
  );
  // The header's one overflow menu (owner ask 2026-08-31, Plane's header
  // discipline): management dialogs open from HERE, not from a row of
  // always-visible buttons beside the view switcher.
  const [manageOpen, setManageOpen] = useState(false);
  /**
   * WS-27bk §9.12.2(b) — whether the caller watches the SELECTED project.
   *
   * `null` means "not asked yet", and the menu item renders a neutral label
   * for it rather than guessing "Watch". Guessing is how a toggle ends up
   * telling somebody they are not subscribed when they are.
   */
  const [projectWatch, setProjectWatch] = useState<ProjectWatchState | null>(
    null,
  );

  /**
   * Ask once per selected project, and drop the answer the moment the
   * selection changes.
   *
   * ⚠️ The `cancelled` flag is not ceremony. Clicking through a tree faster
   * than the network answers would otherwise let an earlier project's reply
   * land last and paint the wrong state onto the current one.
   */
  useEffect(() => {
    const id = selected?.id;
    if (!id) {
      setProjectWatch(null);
      return;
    }
    let cancelled = false;
    setProjectWatch(null);
    projectWatchersApi
      .get(id)
      .then((state) => {
        if (!cancelled) setProjectWatch(state);
      })
      .catch(() => {
        // A failed read leaves the item in its neutral "not asked" state. It
        // must not read as "not watching", which is a claim we cannot make.
        if (!cancelled) setProjectWatch(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selected?.id]);

  /** Toggle the caller's subscription to the selected project. */
  const toggleProjectWatch = useCallback(async () => {
    const id = selected?.id;
    if (!id || !projectWatch) return;
    const next = !projectWatch.watching;
    // Optimistic: both writes are idempotent, so a lost race costs nothing.
    setProjectWatch({ ...projectWatch, watching: next });
    try {
      if (next) await projectWatchersApi.watch(id);
      else await projectWatchersApi.unwatch(id);
      setProjectWatch(await projectWatchersApi.get(id));
    } catch {
      setProjectWatch(await projectWatchersApi.get(id).catch(() => null));
    }
  }, [selected?.id, projectWatch]);
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
  // The delete waiting for the shared ConfirmDialog: one task, or the bulk
  // bar's selection. Null while no dialog is up.
  const [confirmingDelete, setConfirmingDelete] = useState<
    { kind: "one"; taskId: string } | { kind: "bulk"; ids: string[] } | null
  >(null);
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
   * Addresses seen somewhere OTHER than an assignee list — today, the authors
   * of comments and timeline entries in the open task panel.
   *
   * ⚠️ **Deliberately NOT merged into `people`, and that is the whole reason
   * it is a second list.** `people` is the assignee filter's options. A
   * colleague who commented once and holds no task would become a filterable
   * assignee whose every result is empty — a filter that promises rows it
   * cannot produce. These people need a NAME, which is a different job from
   * being a filter option.
   */
  const [seenPeople, setSeenPeople] = useState<string[]>([]);
  /**
   * ⚠️ `useCallback` with no dependencies, and the panel's load effect is
   * why. That effect lists this in its dependency array, so an arrow rebuilt
   * on every render of this page would re-read the timeline on every render.
   * `setSeenPeople` is stable and `mergeAssignees` is a module function, so
   * an empty dependency list is correct rather than a silencing.
   */
  const notePeopleSeen = useCallback((who: string[]) => {
    setSeenPeople((current) => mergeAssignees(current, who));
  }, []);
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
  /**
   * Open one of the four space-scoped screens from a tree row.
   *
   * ⚠️ **It SELECTS the row as well as opening the dialog, and that is
   * deliberate.** Statuses, tags and custom fields are root-scoped, and the
   * page holds ONE copy of each for the node on screen — `setStatuses` feeds
   * the board's lanes, `setTags` the filter bar and the task panel, `setFields`
   * the panel's custom values. Editing space A's vocabulary while the page
   * shows space B would write A's list into B's state, and the lanes behind
   * the open dialog would then be someone else's.
   *
   * The dialog still receives the NODE rather than reading `selected`, because
   * `setSelected` lands on the next render and the dialog has to name the
   * right space on this one.
   */
  // ⚠️ The name says "space" and three of its four callers still mean one —
  // but `onManageStatuses` no longer does. Since migration 196 a subproject may
  // own its lanes, so `projectMenu` offers Statuses on every level but a
  // folder, and this helper receives whichever node was clicked. It already
  // did the right thing (it selects and opens the node it is handed); only the
  // parameter name assumed otherwise.
  const manageSpace =
    (open: (node: ProjectRow) => void) => (node: ProjectRow) => {
      setApp(null);
      setSelected(node);
      open(node);
    };

  /**
   * The same, for a ROOT-scoped screen opened from any row.
   *
   * Tags and custom fields belong to the space (D-PM-16), so right-clicking
   * a subproject and choosing "Tags" must open the SPACE's registry — the
   * one whose chips that board is already showing. Resolving it here rather
   * than in `ProjectTree` keeps the tree saying "manage this row's
   * vocabularies" and leaves which registry that means to the layer holding
   * the whole tree.
   *
   * ⚠️ `setSelected(node)` keeps the ROW the member clicked, not the space
   * it resolved to. Selecting the space instead would navigate the board out
   * from under them as a side effect of opening a dialog.
   */
  const manageRoot =
    (open: (node: ProjectRow) => void) => (node: ProjectRow) => {
      setApp(null);
      setSelected(node);
      open(spaceOf(roots, node));
    };

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
      // Raises the dialog and nothing else. `DeleteProjectDialog` reads the
      // counts and takes the name; `deleteNode` performs the write only once
      // it has both.
      onDelete: (project, level) => {
        setDeleteError(null);
        setDeleteOpenedAt((n) => n + 1);
        setDeletingNode({ project, level });
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
  // An app pane (Analytics, Reports, the AI chat) is not a view of the
  // selected project, so the board's view switcher and project actions go.
  // `ai-chat` was missing here, and the board's tabs sat above the chat.
  const noProjectChrome =
    dashboardOnly || app === "analytics" || app === "reports" || app === "ai-chat";

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

  /**
   * §9.12.7 (a) to (d), for whatever scope is on screen.
   *
   * ⚠️ **The SAME four reads now feed the Analytics pane AND every node
   * dashboard** (owner ask 2026-09-17: *"in the analytics, as well as the
   * overview of each project/subproject, I want to see the workload of the
   * individual people who are working on the project"*). The endpoints have
   * taken a node scope since the portfolio-scope slice; nothing was asking
   * them for one, so the answer existed and no surface showed it.
   *
   * A second fetch path per surface would be a second place for the scope
   * rule to be wrong, and the two would disagree in exactly the case nobody
   * checks — a node whose numbers differ from the portfolio's.
   *
   * Each read settles on its own, so one slow panel never blanks the others.
   */
  const analyticsNode = app === "analytics" ? undefined : selected?.id;
  const wantsAnalytics = app === "analytics" || dashboardOnly || overview;
  useEffect(() => {
    if (!wantsAnalytics) return;
    let cancelled = false;
    setStuck(null);
    setLoad(null);
    setThroughput(null);
    setFinished(null);
    setOutlook(null);
    // ⚠️ A rejected panel stays null and renders NOTHING, rather than
    // rendering zeroes. Zeroes would read as "no stuck work", which is the
    // opposite of "we could not ask".
    projectsApi.stuck(analyticsNode).then(
      (r) => !cancelled && setStuck(r),
      () => !cancelled && setStuck(null)
    );
    projectsApi.load(analyticsNode).then(
      (r) => !cancelled && setLoad(r),
      () => !cancelled && setLoad(null)
    );
    projectsApi.throughput(analyticsNode).then(
      (r) => !cancelled && setThroughput(r),
      () => !cancelled && setThroughput(null)
    );
    projectsApi.finished(analyticsNode).then(
      (r) => !cancelled && setFinished(r),
      () => !cancelled && setFinished(null)
    );
    projectsApi.outlook(analyticsNode).then(
      (r) => !cancelled && setOutlook(r),
      () => !cancelled && setOutlook(null)
    );
    return () => {
      cancelled = true;
    };
  }, [wantsAnalytics, analyticsNode, treeKey]);

  /**
   * WS-27bm S7a — the Capacity panel, for the Analytics app only.
   *
   * A separate effect rather than a sixth read in the one above: that one
   * also feeds every node dashboard, and those do not draw this panel. A
   * rejected read stays null and renders nothing, as the others do.
   */
  useEffect(() => {
    if (app !== "analytics") return;
    let cancelled = false;
    setCapacity(null);
    projectsApi.capacity().then(
      (r) => !cancelled && setCapacity(r),
      () => !cancelled && setCapacity(null)
    );
    // S7c — the same effect, because the same app draws it. A rejected read
    // stays null and renders nothing, as the others do.
    setConflicts(null);
    projectsApi.conflicts().then(
      (r) => !cancelled && setConflicts(r),
      () => !cancelled && setConflicts(null)
    );
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
            onDropNode={dropNode}
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
            onManageStatuses={manageSpace(setManagingStatuses)}
            onManageFields={manageRoot(setManagingFields)}
            onManageTags={manageRoot(setManagingTags)}
            onManageLifecycle={manageSpace(setManagingLifecycle)}
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

  /**
   * The saved view whose hand-arranged order the board reads and writes.
   *
   * ⚠️ Named ONCE and derived from `views`, because three places need the
   * same answer and they must not disagree: the task read asks for this
   * view's positions, the drag handler writes them, and the delete button
   * refuses to remove it. `orderBearingView` holds the rule.
   *
   * `null` until the views read lands. `loadProject` omits the parameter
   * then and re-reads when it arrives — one extra round trip on a cold
   * first paint, and the cache answers every switch after it.
   */
  const boardViewId = useMemo(
    () => orderBearingView(views)?.id ?? null,
    [views]
  );

  /**
   * The question the board asks the server, as a parameter bag.
   *
   * ⚠️ **ONE definition, because two readers now ask it.** `loadProject` reads
   * page 1 and `loadMoreTasks` reads page N. If each built its own bag they
   * would drift the first time a filter was added to one, and a `Load more`
   * that quietly dropped the tag filter would append rows from outside the
   * board the member is looking at — the very "hide work that is genuinely
   * there" this code already warns about.
   */
  const taskParamsFor = useCallback(
    (project: ProjectRow) => ({
      project_id: project.id,
      include_subtree: true,
      page_size: TASK_PAGE_SIZE,
      ...toQuery(filters),
      // WS-27x — the table's header sort; {} when none, so every other
      // surface keeps the endpoint's default ordering.
      ...sortQuery(tableSort),
      // H-64. The view whose hand-arranged order to read back.
      //
      // ⚠️ **The drag handler has written this view's positions since
      // WS-27 and nothing ever asked for them.** Without it every row
      // arrives with `view_position` undefined, `sortForView` sends them
      // all down its `created_at` branch, and a drag inside a column is a
      // silent no-op.
      //
      // ⚠️ Omitted rather than sent as `undefined` when views have not
      // landed yet. `cacheKey` sorts the params into the read key, so a
      // present-but-undefined entry would be a DIFFERENT question from the
      // same read a moment later, and the cached rows could never be
      // reused. It resolves on the next pass, when `views` arrives.
      ...(boardViewId ? { view_id: boardViewId } : {}),
    }),
    [filters, tableSort, boardViewId]
  );

  const loadProject = useCallback(
    async (project: ProjectRow) => {
      setError(null);
      // Filters travel to the server, never applied to the page after it
      // arrives: paging happens in SQL, so a filter applied here would return
      // short pages and hide work that is genuinely there.
      const taskParams = taskParamsFor(project);
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

      // The shelf's size, under the same filters. `page_size: 1` because only
      // `total` is wanted — the rows are the board's job, not this read's.
      const shelfParams = { ...taskParams, archived_only: true, page_size: 1 };
      const shelfKey = projectsKey("tasks", shelfParams);

      try {
        const [statusRes, taskRes, shelfRes] = await Promise.all([
          read(statusesKey, () => projectsApi.statuses(project.id)),
          read(tasksKey, () => projectsApi.tasks(taskParams)),
          // ⚠️ Never fails the board. A count is decoration next to the rows,
          // and a shelf read that 500s must not blank a working board.
          read(shelfKey, () => projectsApi.tasks(shelfParams)).catch(() => null),
        ]);
        setStatuses(statusRes.rows);
        setTasks(taskRes.rows);
        // ⚠️ Reset, never merge. This runs on a fresh project, a changed
        // filter and a changed sort — each of which is a DIFFERENT question,
        // so pages read for the old one must not survive into the new answer.
        setTaskTotal(taskRes.total ?? taskRes.rows.length);
        setArchivedCount(
          filters.archived
            ? taskRes.total ?? taskRes.rows.length
            : shelfRes?.total ?? null,
        );
      } catch (err) {
        setError(String((err as Error).message));
        // ⚠️ Only blank what we had nothing for. Clearing rows we are already
        // showing turns a failed refresh into an empty board — the screen goes
        // blank at the moment the reader most needs to see something.
        if (!heldStatuses) setStatuses([]);
        if (!heldTasks) setTasks([]);
      }
    },
    [filters, tableSort, boardViewId, taskParamsFor]
  );

  useEffect(() => {
    if (selected) void loadProject(selected);
  }, [selected, loadProject]);

  /**
   * Fetch the next page of tasks and add it to what is on screen.
   *
   * ⚠️ **Appends, never replaces.** `appendTasks` also drops a row the
   * shifting offset served twice and keeps the held copy when ids collide, so
   * an optimistic edit the member can see survives the merge. See
   * `lib/paging.ts` for why both matter.
   *
   * ⚠️ The page asked for is derived from how many rows are HELD, not from a
   * counter, so a read that fails half way is retried rather than stepped over.
   */
  const loadMoreTasks = useCallback(async () => {
    if (!selected || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const params = {
        ...taskParamsFor(selected),
        page: nextTaskPage(tasks.length),
      };
      const res = await read(projectsKey("tasks", params), () =>
        projectsApi.tasks(params)
      );
      setTasks((current) => appendTasks(current, res.rows));
      // The count can have moved while the member read page 1. Take the
      // server's newest answer rather than keeping the one from the first read.
      setTaskTotal(res.total ?? null);
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setLoadingMore(false);
    }
  }, [selected, loadingMore, tasks.length, taskParamsFor]);

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
        // ⚠️ The TIMELINE only. A calendar cell is a day, so a task with no
        // day has nowhere to be drawn there — the count is all that surface
        // can honestly say. The timeline gives it a row and no bar, and that
        // row is what you drag across to schedule it.
        include_undated: mode === "timeline",
        ...toQuery(filters),
      });
      // ⚠️ Normalised HERE, at the boundary, so nothing downstream has to
      // guard. `monthGroups` spreads `month.unscheduled`, and a spread of
      // `undefined` throws during RENDER — which blanks the whole Projects
      // page rather than degrading.
      //
      // The gateway does always send these today (`calendar.py` returns
      // `unscheduled: []` when `include_undated` is false). The shape that
      // does not is an OLDER gateway, which is what the app talks to for the
      // minutes a deploy is rolling — and `unscheduled` only arrived on
      // 2026-09-16, so that window has already existed once.
      setMonth({
        rows: res.rows ?? [],
        links: res.links ?? [],
        undated: res.undated ?? 0,
        unscheduled: res.unscheduled ?? [],
        truncated: res.truncated ?? false,
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

  /**
   * Reload WHAT IS ON SCREEN, after a write.
   *
   * 🔴 **This used to be `loadMonth`, so a write on the board refreshed the
   * calendar.** `loadMonth` reads the calendar window and sets `month`. It
   * touches neither `tasks` nor `statuses`. So every caller below — every
   * undo step, every redo, every task patch routed through `rewriteTask`,
   * and both link writes — refreshed a surface the member was not looking
   * at, and the board they WERE looking at kept its old rows until they
   * reloaded the page. The owner reported it as "it doesn't immediately show
   * up in the UI", on 2026-09-20.
   *
   * The mode decides, because the modes read different endpoints. Loading
   * both would double every write's cost to refresh something nobody can
   * see.
   */
  const refreshSurface = useCallback(async () => {
    if (mode === "calendar" || mode === "timeline") {
      await loadMonth();
      return;
    }
    if (selected) await loadProject(selected);
  }, [mode, loadMonth, selected, loadProject]);

  // Always the CURRENT reload, for undo steps that outlive the render that
  // recorded them. See `rewriteTask`.
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  refreshRef.current = refreshSurface;

  // WS-27af — the assignee filter's options, accumulated from whatever has
  // been loaded. `mergeAssignees` returns the same array when nothing is new,
  // so this settles after the first load instead of re-rendering the bar.
  useEffect(() => {
    const found = assigneesIn([...tasks, ...month.rows]);
    setPeople((current) => mergeAssignees(current, found));
  }, [tasks, month.rows]);

  /**
   * The directory's names for everybody holding work here.
   *
   * ⚠️ **Keyed off `people`, which is the UNION across loads**, not the
   * current page of tasks. A name that arrived once stays known, so
   * switching filters does not make labels flicker back to addresses.
   *
   * ⚠️ **Never blocks and never fails loudly.** A board must render before
   * this answers, and keep rendering if it never does — `personLabel` falls
   * back to the address's local part on its own.
   */
  const [personNames, setPersonNames] = useState<Map<string, string>>(
    () => new Map(),
  );

  /**
   * Everybody a label is owed for: the board's assignees, plus anybody the
   * open panel has shown.
   *
   * ⚠️ Disambiguation runs over the UNION, and it has to. Two colleagues
   * called Priya Sharma are told apart by what else is on screen; resolving
   * the two lists separately would render one of them plainly while the
   * other carried a qualifier, for the same name.
   */
  const namedPeople = useMemo(
    () => mergeAssignees(people, seenPeople),
    [people, seenPeople],
  );

  useEffect(() => {
    const unknown = namedPeople.filter(
      (who) => !who.startsWith("agent:") && !personNames.has(who.toLowerCase()),
    );
    if (unknown.length === 0) return;
    let live = true;
    void projectsApi
      .personNames(unknown)
      .then((res) => {
        if (!live) return;
        // ⚠️ Read the body HERE, not inside the updater below. React runs a
        // state updater during RENDER, so a throw in there escapes this
        // promise chain's `.catch` and blanks the whole Projects page. A 200
        // whose body carries no `names` is not hypothetical — it is what an
        // older gateway answers during the minutes a deploy is rolling.
        const found = Object.entries(res?.names ?? {});
        setPersonNames((current) => {
          const next = new Map(current);
          for (const [email, name] of found) {
            next.set(email.toLowerCase(), name);
          }
          // ⚠️ Remember the MISSES too, as an empty string. Without this the
          // effect asks again on every render for anybody the directory does
          // not know, which is a request loop keyed on absence.
          for (const who of unknown) {
            const key = who.toLowerCase();
            if (!next.has(key)) next.set(key, "");
          }
          return next;
        });
      })
      .catch(() => {
        // Labels are a nicety. A failed lookup leaves the local part.
      });
    return () => {
      live = false;
    };
  }, [namedPeople, personNames]);

  /** Every person on screen, labelled and disambiguated together. */
  const personLabels = useMemo(
    () => labelPeople(namedPeople, personNames),
    [namedPeople, personNames],
  );

  // A different project is a different set of people. Emptied rather than
  // carried, so one project's members never appear in another's filter.
  useEffect(() => {
    setPeople([]);
    // The panel's people go too. A name resolved for one project's commenter
    // is not wrong in another, but keeping the list would grow it for the
    // whole session and send a lookup for people nobody is looking at.
    setSeenPeople([]);
  }, [selected?.id]);

  useEffect(() => {
    if (!selected) {
      setFields([]);
      setTags([]);
      setTaskTypes([]);
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
    // WS-27bh. Same rule as the two above: a board that draws no type chip
    // beats a board that refuses to load because the registry did not arrive.
    projectsApi
      .types(selected.id)
      .then((res) => {
        if (live) setTaskTypes(res.rows);
      })
      .catch(() => {
        if (live) setTaskTypes([]);
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
  // ⚠️ The unscheduled tasks are grouped WITH the dated ones (P-22).
  //
  // The timeline draws a row per grouped task, so a task missing from `groups`
  // gets no row — and a row is exactly what an undated task needs, since the
  // empty row is the surface you drag across to schedule it. Grouping only
  // `month.rows` would have given the feature to flat timelines and withheld
  // it from grouped ones, which is the kind of split nobody discovers until
  // they group a board and their unscheduled work vanishes.
  const monthGroups = useMemo(
    () => groupTasks(
      [...month.rows, ...month.unscheduled], groupBy, { statuses, projectName },
    ),
    [month.rows, month.unscheduled, groupBy, statuses, projectName]
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

  /**
   * A lifecycle verb on the whole selection. Owner request, 2026-09-20.
   *
   * ⚠️ **Deliberately NOT routed through `applyBulk`.** That function builds
   * a patch, and the gateway refuses an action sent beside one. Two paths
   * because they are two request shapes, which is the same reason `onMove`
   * is not an edit either.
   *
   * ⚠️ **Delete is confirmed and the rest are not.** Archiving fifty tasks is
   * undone by restoring fifty tasks, and the Restore button is on the same
   * bar. Deleting fifty is undone by nothing.
   *
   * The confirmation is the shared `ConfirmDialog` (it was `window.confirm`
   * until 2026-09-24). A delete without `confirmedIds` only opens it, and
   * the dialog's confirm calls back here with the ids it showed.
   */
  async function applyBulkAction(
    action: "archive" | "unarchive" | "delete",
    confirmedIds?: string[],
  ) {
    const ids = confirmedIds ?? [...picked];
    if (ids.length === 0) return;
    if (action === "delete" && !confirmedIds) {
      setConfirmingDelete({ kind: "bulk", ids });
      return;
    }
    setBulkBusy(true);
    setBulkNotice(null);
    try {
      const outcome = await projectsApi.bulkEdit({ action, task_ids: ids });
      setBulkNotice(describeOutcome(outcome));
      // ⚠️ The selection is DROPPED after a delete and kept otherwise. Keeping
      // it would leave the bar counting rows that no longer exist, and the
      // next button pressed would report fifty "not found".
      if (action === "delete") setPicked(new Set());
      await refreshRef.current();
      setTreeKey((k) => k + 1);
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
      // Opened from one of the app's own destinations — the AI chat slot, most
      // often, where a card's "Open task" landed the panel beside a full-width
      // chat and never showed the project (owner report, 2026-09-23). Leave
      // the destination, select the task's project so its board shows, and
      // carry the conversation into the dock, where it comes back beside the
      // board when the task closes.
      if (app !== null) {
        const home = flatten(visibleRoots).find((e) => e.node.id === task.project_id);
        if (home) setSelected(home.node as ProjectRow);
        // Only where a dock can draw. Below 80rem, or on a phone, the dock is
        // `absent`, so docking would hide the chat and store a choice the
        // member never made (review of PR #431). There the conversation stays
        // one tap away under "AI chat".
        if (app === "ai-chat" && CHAT_LIVE && dockWide && !isMobile) {
          setChatDocked(true);
          writeChatDocked(true);
        }
        setApp(null);
      }
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
    [selected, statuses, app, visibleRoots, dockWide, isMobile]
  );

  /**
   * Open one task by id — what a notification, and the People Center's "Open
   * work" list, both link to.
   *
   * Those links have been generating `/projects?task=<id>` since WS-28b and
   * landing on an unchanged board, because nothing here read the parameter.
   */
  /**
   * Fold the open card's tasks into `targetId`.
   *
   * ⚠️ Reloads the BOARD and not just the target. A merge changes rows the
   * page is holding in three ways at once — the sources leave the board, the
   * target's fields move, and a source's subtasks re-parent under it — and
   * patching that by hand is three chances to show something the server does
   * not agree with. The write path this file already trusts is a refetch.
   */
  async function mergeInto(targetId: string) {
    const sources = mergingTasks ?? [];
    if (!sources.length) return;
    setMergeBusy(true);
    setMergeError(null);
    try {
      await projectsApi.mergeTasks(targetId, sources);
      setMergingTasks(null);
      // The merged tasks are gone from every live surface, so a selection
      // still holding them would arm a bulk action against archived stubs.
      setPicked(new Set());
      await refreshRef.current();
      toast.show({
        variant: "success",
        title:
          sources.length === 1
            ? "Merged into the task you kept"
            : `Merged ${sources.length} tasks into the one you kept`,
      });
    } catch (err) {
      // Stays ON the card. The gateway's refusals are all things the member
      // can act on — a different project, an already-merged task — and a
      // toast over a closed card gives them nothing to act on.
      setMergeError(String((err as Error).message));
    } finally {
      setMergeBusy(false);
    }
  }

  const openTaskById = useCallback(
    async (taskId: string) => {
      try {
        let task = await projectsApi.task(taskId);
        // ⚠️ Follow the merge, ONCE. A task folded into another has none of
        // its content any more, so opening the stub shows an empty panel and
        // leaves the reader to work out where everything went. The promise
        // made when merging was that the old number and any pasted link keep
        // working — this is where that promise is kept.
        //
        // One hop and not a loop: the gateway refuses a merge INTO a stub, so
        // a chain cannot be created through the product. A single hop means a
        // chain that somehow exists degrades to showing the next task rather
        // than to spinning.
        if (task.merged_into_task_id) {
          task = await projectsApi.task(task.merged_into_task_id);
        }
        await openWithStatuses(task);
      } catch (err) {
        setError(String((err as Error).message));
      }
    },
    [openWithStatuses]
  );

  // `?app=analytics|reports` opens one of the app's own destinations — the
  // door a chat card uses (WS-27bm, `ProjectToolCards`). Only a LIVE entry
  // opens; a preview slug is ignored, because the sidebar would refuse it.
  // Consumed after the open, like `?task=`, so the same link works twice.
  const appLink = searchParams.get("app");
  useEffect(() => {
    if (!appLink) return;
    const live = PROJECT_APP_SECTIONS.flatMap((s) => s.items).find(
      (i) => i.id === appLink && i.launch === "live",
    );
    if (live) setApp(live.id);
    const rest = new URLSearchParams(searchParams.toString());
    rest.delete("app");
    const qs = rest.toString();
    router.replace(qs ? `/projects?${qs}` : "/projects");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appLink]);

  // The chat's navigation (WS-27bm S6, H-164). `open_in_app` reads the row,
  // then dispatches one of these through a CUSTOM `frontend_tool` event.
  // `dispatched`: the model reaches them through that skill tool, so they
  // stay out of the prompt addendum.
  useFrontendTool({
    name: "projects.open_task",
    description: "Open a task in the Projects page.",
    dispatched: true,
    handler: async (args) => {
      const id = String(args.task_id ?? "");
      if (!/^[0-9a-f-]{36}$/i.test(id)) return "not a task id";
      await openTaskById(id);
      return "opened";
    },
  });
  useFrontendTool({
    name: "projects.open_project",
    description: "Select a space, folder or project in the Projects page.",
    dispatched: true,
    handler: (args) => {
      const id = String(args.project_id ?? "");
      const row = flatten(visibleRoots).find((e) => e.node.id === id);
      if (!row) return "not visible";
      setApp(null);
      setSelected(row.node as ProjectRow);
      return "opened";
    },
  });
  useFrontendTool({
    name: "projects.open_app",
    description: "Open a live Projects app (analytics, reports).",
    dispatched: true,
    handler: (args) => {
      const id = String(args.app ?? "");
      const live = PROJECT_APP_SECTIONS.flatMap((s) => s.items).find(
        (i) => i.id === id && i.launch === "live",
      );
      if (!live) return "not a live app";
      setApp(live.id);
      return "opened";
    },
  });

  // A chat write (WS-27bm S4) announces itself once per receipt card, and
  // the board reloads the selected project so the member sees the change
  // without a click. The chat never reaches the page's state; this event
  // is the one seam.
  useEffect(() => {
    const onChanged = () => {
      // The app's own writes invalidate the family in `api.ts`; a chat write
      // ran server-side, so this is where the same drop happens. It notifies
      // the tree and the summaries too, not only the selected board.
      invalidate(PROJECTS_CACHE);
      if (selected) void loadProject(selected);
    };
    window.addEventListener(PROJECTS_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(PROJECTS_CHANGED_EVENT, onChanged);
  }, [selected, loadProject]);

  // `?project=<id>` selects a node (WS-27bm S6): the link `open_in_app`
  // returns for a member who is not on this page. Consumed after the select,
  // like `?app=` and `?task=`, so the same link works twice.
  const projectLink = searchParams.get("project");
  useEffect(() => {
    if (!projectLink) return;
    const row = flatten(visibleRoots).find((e) => e.node.id === projectLink);
    if (!row) return; // the tree is still loading; the effect runs again when it lands
    // A deep link is consumed by setting state once, as `?app=` above does.
    /* eslint-disable react-hooks/set-state-in-effect */
    setApp(null);
    setSelected(row.node as ProjectRow);
    /* eslint-enable react-hooks/set-state-in-effect */
    const rest = new URLSearchParams(searchParams.toString());
    rest.delete("project");
    const qs = rest.toString();
    router.replace(qs ? `/projects?${qs}` : "/projects");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectLink, visibleRoots]);

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
        if (live) {
          await openWithStatuses(task);
          // Consume the link. The effect is keyed on the id, so a second
          // click on the same chat card row (WS-27bm) would push an
          // unchanged URL and open nothing. Clearing `task` — and only
          // `task` — after the open makes the next push a change again.
          const rest = new URLSearchParams(searchParams.toString());
          rest.delete("task");
          const qs = rest.toString();
          router.replace(qs ? `/projects?${qs}` : "/projects");
        }
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
        // The palette acts on the SELECTED project, which is what the palette
        // has always meant. `commandCtx.hasProject` already gates every one of
        // these, so a null here is a command that was not offered.
        if (!selected) return;
        if (what === "fields") setManagingFields(selected);
        else if (what === "tags") setManagingTags(selected);
        else if (what === "statuses") setManagingStatuses(selected);
        else if (what === "lifecycle") setManagingLifecycle(selected);
      },
      showShortcuts: () => setShowingShortcuts(true),
    }),
    [router, setPanelMode, selected],
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
  const doomedTask =
    confirmingDelete?.kind === "one"
      ? tasks.find((t) => t.id === confirmingDelete.taskId)
      : undefined;
  const deleteCopy =
    confirmingDelete?.kind === "bulk"
      ? deleteTasksCopy(confirmingDelete.ids.length)
      : deleteTaskCopy(
          doomedTask
            ? { title: doomedTask.title, subtasks: doomedTask.subtasks?.total ?? 0 }
            : null,
        );

  const overlayOpen =
    searching ||
    Boolean(confirmingDelete) ||
    showingShortcuts ||
    Boolean(managingFields) ||
    Boolean(managingTags) ||
    Boolean(managingStatuses) ||
    Boolean(managingLifecycle);

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

  /**
   * H-8 — delete a project and everything under it. Confirmed already.
   *
   * ⚠️ **Not undoable, and deliberately not offered as such.** Every other
   * destructive act in this page records an `undoApi` entry; this one cannot,
   * because the inverse of a cascade is a restore and there is no endpoint that
   * performs one. Recording an undo that would fail is worse than recording
   * none — it tells somebody the act was reversible after it was not.
   *
   * ⚠️ **The selection may be INSIDE what was just deleted.** Clearing only
   * when the deleted row IS the selected one leaves the page holding a
   * subproject whose whole branch is gone, and every panel keyed on it then
   * reads an id the server no longer knows. So the test is ancestry, taken
   * from the tree BEFORE the refetch, while the path still exists.
   *
   * ⚠️ The open TASK panel is a second holder of a dead id and needs no test:
   * the cascade took every task under the node, so an open panel is closed
   * unconditionally.
   */
  async function deleteNode(node: ProjectRow) {
    const selectionIsInside =
      !!selected &&
      (selected.id === node.id ||
        pathTo(roots, selected.id).some((row) => row.id === node.id));

    setDeleting(true);
    setDeleteError(null);
    try {
      const res = await projectsApi.deleteProject(node.id);
      setDeletingNode(null);
      if (selectionIsInside) setSelected(null);
      // The panel holds a TASK, and the cascade took every task in the
      // subtree. Clearing the selection does not close it, so the panel kept
      // a dead id and every read it made 404'd. The task-delete path already
      // does this; the project-delete path has to as well.
      setOpenTask(null);
      setTreeKey((k) => k + 1);
      // The counts come off the RESPONSE, which the server read before the
      // write. The dialog's numbers were a different read at a different
      // moment, and reporting those would be reporting the question rather
      // than the answer. `cascaded.projects` counts this project too.
      toast.show({
        variant: "success",
        title:
          res.cascaded.tasks > 0
            ? `Deleted ${node.name} — ${res.cascaded.projects} project(s) and ${res.cascaded.tasks} task(s) removed`
            : `Deleted ${node.name}`,
      });
    } catch (err) {
      // The dialog stays OPEN on a refusal, for `moveNodeTo`'s reason: the
      // choice was made and the server's answer is the one worth showing.
      // ⚠️ Into `deleteError`, NOT the page's `error` — see that state's note.
      setDeleteError(String((err as Error).message));
    } finally {
      setDeleting(false);
    }
  }

  /**
   * WS-27bl §9.13 — move the selection, with the mapping the member agreed.
   *
   * ⚠️ The server is asked a SECOND time here, and that is deliberate. The
   * card previewed a plan; this posts the destination and the overrides and
   * lets `move.py` re-resolve. A plan held in the browser can be stale by the
   * time somebody clicks — a field deleted, a status renamed — and applying a
   * remembered answer is how a value lands under a key nothing defines.
   */
  /**
   * File one task, or bring one back. Owner request, 2026-09-20.
   *
   * **Any status files.** Archive is a shelf, not an outcome — the category
   * guard went on 2026-09-21. An error can still arrive (the task moved, the
   * grant changed), so the strip still shows one.
   */
  async function setTaskArchived(taskId: string, archived: boolean) {
    try {
      if (archived) await projectsApi.archiveTask(taskId);
      else await projectsApi.unarchiveTask(taskId);
      await refreshRef.current();
      setTreeKey((k) => k + 1);
      toast.show({
        variant: "success",
        title: archived ? "Task archived" : "Task restored from the archive",
      });
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  /**
   * Delete one task for good.
   *
   * ⚠️ **The subtask sentence is not decoration.** Deleting a parent PROMOTES
   * its children — `parent_task_id` SET NULLs — so they survive at the top
   * level. Somebody who expects a cascade would otherwise delete a parent to
   * be rid of a subtree and find the subtree still there, or, worse, hesitate
   * to delete anything because they cannot tell which it does. The words
   * are `deleteTaskCopy`'s, drawn by the shared `ConfirmDialog`.
   *
   * Without `confirmed` this only opens the dialog. Its confirm calls back.
   */
  async function deleteTaskById(taskId: string, confirmed = false) {
    if (!confirmed) {
      setConfirmingDelete({ kind: "one", taskId });
      return;
    }
    try {
      const done = await projectsApi.deleteTask(taskId);
      await refreshRef.current();
      setTreeKey((k) => k + 1);
      const promoted = done.cascaded.subtasks_promoted;
      toast.show({
        variant: "success",
        title: promoted
          ? `Deleted. ${promoted} subtask${promoted === 1 ? "" : "s"} moved up a level.`
          : "Task deleted",
      });
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function moveTasksTo(
    destinationId: string,
    statusMap: Record<string, string>,
    acceptedDrops: string[] | null,
  ) {
    const ids = movingTasks ?? [];
    setMovingTasksBusy(true);
    setMoveTasksError(null);
    try {
      const res = await projectsApi.moveTasks({
        task_ids: [...ids],
        destination_project_id: destinationId,
        status_map: statusMap,
        accept_drops: acceptedDrops !== null,
        // What the card actually SHOWED as dropping. The server answers 409
        // if the destination changed and the real loss is now larger.
        ...(acceptedDrops ? { accepted_drops: acceptedDrops } : {}),
      });
      setMovingTasks(null);
      setPicked(new Set());
      setAnchor(null);
      // The tasks are in another project now, so this board's list is wrong
      // and so is the tree's count. Refetch rather than patch in place.
      //
      // ⚠️ BOTH. `setTreeKey` re-reads the TREE, and nothing else. The board's
      // rows come from `loadProject`, so bumping the key alone left the moved
      // cards sitting on the board they had just left.
      setTreeKey((k) => k + 1);
      await refreshRef.current();
      toast.show({
        variant: "success",
        title:
          res.dropped_fields.length > 0
            ? `Moved ${res.moved} task(s) — dropped ${res.dropped_fields.join(", ")}`
            : `Moved ${res.moved} task(s)`,
      });
    } catch (err) {
      // Stays OPEN on a refusal, and the message renders IN the card: the
      // page's error strip sits under the modal backdrop.
      setMoveTasksError(String((err as Error).message));
    } finally {
      setMovingTasksBusy(false);
    }
  }

  /**
   * WS-27bk §9.12.4 slice 2 — a completed drag in the rail.
   *
   * ⚠️ **The planner decides, and it already refused the illegal ones.** A
   * target the grammar rejects never became a drop target, so a refusal here
   * is a race — the tree changed under the drag — and it is shown rather than
   * swallowed.
   *
   * `null` means the drop changed nothing. Writing it would cost an activity
   * row and a refetch to put a node back where it already was.
   */
  async function dropNode(movingId: string, target: TreeDropTarget) {
    const planned = planTreeDrop(roots, movingId, target);
    if (planned === null) return;
    if ("refusal" in planned) {
      setError(planned.refusal);
      return;
    }
    const path = pathTo(roots, movingId);
    const node = path[path.length - 1];
    const from = path.length > 1 ? path[path.length - 2].id : null;
    const previous =
      typeof node?.position === "number" ? node.position : undefined;

    setError(null);
    try {
      /**
       * ⚠️ THE SPREAD FIRST, and every row of it.
       *
       * A sibling set that has never been ordered carries `null` on every
       * row, and a midpoint needs numbers. `planTreeDrop` hands back the
       * whole re-spread when that happens — once per set, never again — and
       * every row of it must land before the move, or the order the user
       * just chose is measured against positions that do not exist yet.
       */
      if (planned.spread) {
        for (const row of planned.spread) {
          if (row.id === movingId) continue;
          await projectsApi.moveNode(row.id, planned.plan.parentId, row.position);
        }
      }
      await projectsApi.moveNode(
        movingId,
        planned.plan.parentId,
        planned.plan.position,
      );
      setTreeKey((k) => k + 1);
      undoApi.record({
        label: `moved ${node?.name ?? "a project"}`,
        undo: async () => {
          await projectsApi.moveNode(movingId, from, previous);
          setTreeKey((k) => k + 1);
        },
        redo: async () => {
          await projectsApi.moveNode(
            movingId,
            planned.plan.parentId,
            planned.plan.position,
          );
          setTreeKey((k) => k + 1);
        },
      });
    } catch (err) {
      setError(String((err as Error).message));
      // The tree on screen still shows the drag's optimistic nothing — this
      // page never moves a row locally — so a refetch is what puts it back in
      // step with a write that did not land.
      setTreeKey((k) => k + 1);
    }
  }

  /**
   * ── The two halves of a dependency, WITHOUT the undo bookkeeping ────────
   *
   * Bare on purpose. An undo step that called the recording version would push
   * a NEW entry onto the stack while running off it, so one Ctrl+Z would leave
   * the stack longer than it started. Both throw, because `undoApi` needs the
   * rejection to put a failed step back rather than skip silently past it.
   */
  async function createBlockLink(blockerId: string, blockedId: string) {
    const created = await projectsApi.createLink(blockerId, blockedId, "blocks");
    await refreshRef.current();
    return created.id;
  }

  /**
   * `DELETE /tasks/{taskId}/links/{linkId}` takes EITHER end — the handler
   * matches `source_task_id = :tid OR target_task_id = :tid`, and says in its
   * own comment that the caller may be either. So the id here is a visibility
   * check rather than a direction: the caller must be able to reach the task
   * they name, and the link must touch it.
   *
   * The blocker's id travels because the timeline has it in hand. Nothing
   * breaks if a future caller passes the blocked end.
   */
  async function dropLink(blockerId: string, linkId: string) {
    await projectsApi.deleteLink(blockerId, linkId);
    await refreshRef.current();
  }

  async function linkTasks(blockerId: string, blockedId: string) {
    try {
      const id = await createBlockLink(blockerId, blockedId);
      recordLinkHistory("linked two tasks", blockerId, blockedId, id, "created");
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  /** Remove a dependency — WS-27bk §9.12.5. */
  async function unlinkTasks(blockerId: string, linkId: string) {
    const edge = month.links.find((link) => link.id === linkId);
    if (!edge) return;
    try {
      await dropLink(blockerId, linkId);
      recordLinkHistory(
        "removed a dependency",
        edge.blocker_id,
        edge.blocked_id,
        linkId,
        "deleted",
      );
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  /**
   * One undo entry for both directions, because they share the trap.
   *
   * ⚠️ **THE LINK'S ID MOVES.** Re-creating a dependency writes a NEW row with
   * a new id, so an entry that captured the original id points at nothing the
   * second time round — undo, redo, undo, and the third step 404s. The live id
   * is therefore held in a mutable cell that each re-create rewrites.
   *
   * A pair of task ids would not do instead. The route needs the LINK id, and
   * two tasks can legitimately carry more than one link between them.
   */
  function recordLinkHistory(
    label: string,
    blockerId: string,
    blockedId: string,
    id: string,
    did: "created" | "deleted",
  ) {
    let live = id;
    const remake = async () => {
      live = await createBlockLink(blockerId, blockedId);
    };
    const remove = () => dropLink(blockerId, live);
    undoApi.record({
      label,
      undo: did === "created" ? remove : remake,
      redo: did === "created" ? remake : remove,
    });
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

  /** Every visible node, for the chat's focus picker (`lib/chatScope.ts`). */
  const chatScopes = useMemo(
    () =>
      flatten(visibleRoots).map((e) => ({
        id: e.node.id,
        name: e.node.name,
        level: levelOf(visibleRoots, e.node.id),
        depth: e.depth,
        archived: Boolean((e.node as ProjectRow).archived_at),
      })),
    [visibleRoots],
  );

  // WS-27bm S8 visual review — may a document open in the side panel beside
  // the board? The row holds the tree, the panel, the board and the dock, and
  // the board keeps BOARD_MIN_REM (`lib/sidePanelFit.ts`). Measured, not
  // assumed: the tree, the dock and the root font size all move with the
  // member's density. Hooks run before the loading return below.
  const rowRef = useRef<HTMLDivElement>(null);
  const navRef = useRef<HTMLElement>(null);
  const dockRef = useRef<HTMLElement>(null);
  const [panelFits, setPanelFits] = useState(true);
  useEffect(() => {
    const row = rowRef.current;
    if (!row || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const dock = dockRef.current;
      const remPx =
        parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      setPanelFits(
        sidePanelFits({
          rowWidth: row.clientWidth,
          navWidth: navRef.current?.offsetWidth ?? 0,
          dockWidth: dock && !dock.hidden ? dock.offsetWidth : 0,
          panelWidth: getSidePanelState().width,
          boardMinPx: BOARD_MIN_REM * remPx,
        }),
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(row);
    if (navRef.current) observer.observe(navRef.current);
    if (dockRef.current) observer.observe(dockRef.current);
    const unsubscribe = subscribeSidePanel(measure);
    return () => {
      observer.disconnect();
      unsubscribe();
    };
  }, [loading, railOpen, chatDocked, app]);

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
            {/* WS-27bk §9.12.2(b). First, because it is the one item about
                YOU rather than about the project's configuration — and the
                only one a member reaches repeatedly.

                ⚠️ Watching a project is NOT watching its tasks. The server
                resolves the ancestor chain per notification, so a task added
                tomorrow is covered by a subscription taken today. */}
            <button
              type="button"
              role="menuitem"
              disabled={projectWatch === null}
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-foreground hover:bg-muted disabled:opacity-50"
              onClick={() => {
                setManageOpen(false);
                void toggleProjectWatch();
              }}
            >
              <Icon
                name={projectWatch?.watching ? "BellOff" : "Bell"}
                className="h-3.5 w-3.5 text-muted-foreground"
              />
              {projectWatch === null
                ? "Watch project"
                : projectWatch.watching
                  ? "Stop watching"
                  : projectWatch.inherited
                    ? "Watch directly"
                    : "Watch project"}
            </button>
            {/* An inherited subscription is stated, never implied by a
                disabled control: the member is already hearing about this
                project, and only a parent explains why. */}
            {projectWatch?.inherited && !projectWatch.watching ? (
              <p className="px-2 pb-1 text-[11px] leading-snug text-muted-foreground">
                You already follow this through a parent project.
              </p>
            ) : null}
            <div className="my-1 h-px bg-border" role="separator" />
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-foreground hover:bg-muted"
              onClick={() => {
                setManageOpen(false);
                setManagingFields(selected);
              }}
            >
              <Icon name="SlidersHorizontal" className="h-3.5 w-3.5 text-muted-foreground" />
              Custom fields
            </button>
            {/* Statuses leads the vocabularies: it is the one whose category
                half drives the roll-up, completion, and what /tasks shows. */}
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-foreground hover:bg-muted"
              onClick={() => {
                setManageOpen(false);
                setManagingStatuses(selected);
              }}
            >
              <Icon name="Columns3" className="h-3.5 w-3.5 text-muted-foreground" />
              Statuses
            </button>
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-foreground hover:bg-muted"
              onClick={() => {
                setManageOpen(false);
                setManagingTags(selected);
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
                  setManagingLifecycle(selected);
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
    selectedLevel === "project" && (summary?.children?.length ?? 0) > 0
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
  /**
   * The member's PLACE, which both chat mounts hand the rail — the node they
   * last selected, the filters, the open task and the selection — so "this
   * project" resolves without an id. One object, so the slot and the dock
   * cannot describe one place two ways.
   */
  const railPlace = {
    node: selected
      ? {
          id: selected.id,
          name: selected.name,
          level: selectedLevel,
          archived: Boolean(selected.archived_at),
        }
      : null,
    filters,
    openTask: openTask
      ? { id: openTask.id, title: openTask.title, number: openTask.task_number ?? null }
      : null,
    selectedTaskIds: Array.from(picked),
    scopes: chatScopes,
  };

  const workArea = app === "ai-chat" ? (
    // WS-27bm — the AI chat, full width in its own slot. Reachable only when
    // `NEXT_PUBLIC_PROJECTS_CHAT` flips the entry to live; off, the sidebar
    // disables it and says so. The rail header names the node, because the
    // tree does not highlight it while an app is open. No `view`: the member
    // is looking at the chat, not at a canvas.
    <div className="min-w-0 flex-1 overflow-hidden">
      <AssistantRail {...railPlace} view={null} />
    </div>
  ) : app === "reports" ? (
    // §9.12.8 — a saved question, rendered on screen before anything sends.
    // Its own reads; it shares only `finished`, to say how much there is to
    // report on while the list is empty.
    <ReportsView finished={finished} />
  ) : app === "analytics" ? (
    // Analytics — the portfolio roll-up in Plane's shape: a KPI strip over
    // a per-space state matrix (see AnalyticsView's header for sources).
    // Same endpoint as the dashboards, so the two cannot disagree.
    <>
      {shownError ? renderState("error", shownError) : null}
      {portfolio ? (
        <AnalyticsView
          summary={portfolio}
          stuck={stuck}
          load={load}
          throughput={throughput}
          finished={finished}
          outlook={outlook}
          capacity={capacity}
          conflicts={conflicts}
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
          stuck={stuck}
          load={load}
          throughput={throughput}
          finished={finished}
          outlook={outlook}
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
          archivedCount={archivedCount}
          personLabels={personLabels}
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
          personLabels={personLabels}
          count={picked.size}
          statuses={statuses}
          // The registry the two tag pickers suggest from — the same one the
          // task panel and the filter row read, so one tag is one colour and
          // one spelling everywhere.
          tags={tags}
          busy={bulkBusy}
          notice={bulkNotice}
          onClear={() => {
            setPicked(new Set());
            setAnchor(null);
            setBulkNotice(null);
          }}
          onApply={(request) => void applyBulk(request)}
          onAction={(action) => void applyBulkAction(action)}
          onMove={() => {
            setMoveTasksError(null);
            setMovingTasks([...picked]);
          }}
          onMerge={() => {
            setMergeError(null);
            setMergingTasks([...picked]);
          }}
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

      {/* 🔴 The board holds a PAGE. Say so, above the scroll area rather than
          inside it, so the admission cannot be scrolled away from.

          ⚠️ Only the three views fed by `GET /projects/tasks`. The timeline
          and the calendar load from `/calendar`, which reports its own
          `truncated` flag and already prints its own sentence — a second bar
          over them would be a second way to say one thing (§5), and it would
          disagree, because the two endpoints cap differently. The overview
          counts in SQL and is never short. */}
      {selected && (mode === "board" || mode === "list" || mode === "table") ? (
        <MoreTasksBar
          loaded={tasks.length}
          total={taskTotal}
          busy={loadingMore}
          onLoadMore={() => void loadMoreTasks()}
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
                stuck={stuck}
                load={load}
                throughput={throughput}
                finished={finished}
                outlook={outlook}
              />
            ) : (
              renderState("loading", "Counting the work below…")
            )
          ) : mode === "timeline" ? (
            <TimelineView
              tasks={month.rows}
              links={month.links}
              unscheduled={month.unscheduled}
              undated={month.undated}
              truncated={month.truncated}
              today={dayKey(new Date())}
              shownFields={shownFields}
              tags={tags}
              taskTypes={taskTypes}
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
              onUnlink={(blockerId, linkId) => void unlinkTasks(blockerId, linkId)}
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
              taskTypes={taskTypes}
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
          personLabels={personLabels}
              groups={groups}
              groupBy={groupBy}
              statuses={statuses}
              fields={fields}
              taskTypes={taskTypes}
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
          personLabels={personLabels}
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
              taskTypes={taskTypes}
              projectName={projectName}
              projectId={selected.id}
              shownFields={shownFields}
              onCreated={() => void loadProject(selected)}
              // A rename writes one title and the board must redraw it. Same
              // answer as `onCreated` today; a separate prop because they are
              // separate events (see TaskBoard's Props).
              onRenamed={() => void loadProject(selected)}
              onArchive={(taskId, archived) =>
                void setTaskArchived(taskId, archived)
              }
              onDeleteTask={(taskId) => void deleteTaskById(taskId)}
              selected={picked}
              onToggle={toggleSelection}
              onMoveTask={(taskId) => {
                setMoveTasksError(null);
                setMovingTasks([taskId]);
              }}
              onMergeTask={(taskId) => {
                setMergeError(null);
                setMergingTasks([taskId]);
              }}
              onExtendSelection={extendSelection}
              onSelect={(task) => void openWithStatuses(task)}
              onDrop={handleDrop}
            />
          ) : (
            <TaskList
          personLabels={personLabels}
              groups={groups}
              groupBy={groupBy}
              filters={filters}
              onClearFilters={() => changeFilters(EMPTY_FILTERS)}
              statuses={statuses}
              tags={tags}
              taskTypes={taskTypes}
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
  //
  // ⚠️ WRAPPED, because the boundary above covers the CANVASES ONLY.
  //
  // `LayoutBoundary`'s own header explains the choice: scope it to the code
  // that walks server-shaped data, so a broken canvas leaves the tree and the
  // toolbar alive. The task panel walks server-shaped data too — relations,
  // subtasks, custom fields, attachments — and it sat outside every boundary.
  // Measured 2026-09-03: a relations body one field short threw out of
  // `RelationsBlock`, reached the React root, and blanked the whole document.
  //
  // `completeRelations` fixes that one body. This makes the NEXT one a named
  // fallback beside a working board, instead of a white page.
  const taskPanel = openTask ? (
    <LayoutBoundary key={`panel-${openTask.id}`} layout="task panel">
    <TaskPanel
      personLabels={personLabels}
      /**
       * ⚠️ People the BOARD never saw.
       *
       * `people` is built from assignees, so a colleague who commented on a
       * task but holds none of them has no directory name and renders as the
       * local part of their address — which is the defect the name lookup was
       * added to end, surviving in the one place people are named most.
       *
       * Reported upward rather than looked up in the panel: the existing
       * effect already resolves names and hands the labels back down, and
       * a second lookup inside the panel would be a second way to do one
       * thing (§5) with its own cache to go stale. It lands in
       * `seenPeople`, NOT in `people` — see that state's own note.
       */
      onPeopleSeen={notePeopleSeen}
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
    </LayoutBoundary>
  ) : null;

  /** Everything that floats above the layout, in ONE place — so the two
   *  branches cannot end up offering different dialogs. */
  const overlays = (
    <>
      {/* H-8 — the delete confirmation.
          ⚠️ **Here, and NOT beside `MoveDialog`, on purpose.** This page has
          two returns: the phone branch above and the desktop one below. Only
          `overlays` is rendered by both. `MoveDialog` is mounted in the
          desktop return alone, so "Move to…" opens nothing at all on a phone —
          the menu entry is there, the click lands, and no dialog exists to
          render. That is a defect on an existing feature and is filed rather
          than fixed here; this one simply does not repeat it. */}
      {/* ⚠️ `key` IS THE SAFETY MECHANISM, not a list-rendering habit.
          This dialog is mounted unconditionally and returns null when closed,
          so it never unmounts and its state survives a close. Its reset lives
          in a passive effect, which React runs AFTER the commit paints — so
          reopening on the same row rendered one frame with the previous
          `typed` and `summary` still set, and the destructive button ARMED.
          One paint, on the one act in this app that cannot be undone.
          Keying on the id makes the reset structural: a different row, or the
          same row opened again, is a new instance with fresh state. */}
      {/* WS-27bl §9.13.4. In `overlays`, which BOTH page returns render —
          `MoveDialog` is in the desktop return alone and opens nothing on a
          phone (H-120). Keyed on the selection so reopening never shows the
          previous set's plan. */}
      {/* ⚠️ `tasks`, not the whole project. The card offers what the board
          has loaded, which is what the member can see and therefore what
          they can mean. Fetching every task in the project to populate a
          picker would be a second, unfiltered read of a list the page
          already holds. */}
      <MergeTasksDialog
        key={`merge:${mergingTasks?.join(",") ?? "none"}`}
        sources={mergingTasks}
        tasks={tasks}
        busy={mergeBusy}
        error={mergeError}
        onClose={() => {
          setMergingTasks(null);
          setMergeError(null);
        }}
        onMerge={(targetId) => void mergeInto(targetId)}
      />

      <MoveTasksDialog
        key={`move:${movingTasks?.join(",") ?? "none"}`}
        taskIds={movingTasks}
        roots={roots}
        busy={movingTasksBusy}
        error={moveTasksError}
        onClose={() => {
          setMovingTasks(null);
          setMoveTasksError(null);
        }}
        onConfirm={(destinationId, statusMap, acceptedDrops) =>
          void moveTasksTo(destinationId, statusMap, acceptedDrops)
        }
      />

      <DeleteProjectDialog
        key={`delete:${deletingNode?.project.id ?? "none"}:${deleteOpenedAt}`}
        project={deletingNode?.project ?? null}
        level={deletingNode?.level}
        error={deleteError}
        busy={deleting}
        onClose={() => {
          setDeletingNode(null);
          setDeleteError(null);
        }}
        onConfirm={(project) => void deleteNode(project)}
      />

      {/* One delete confirmation, shared with My Tasks. The words are this
          app's own and true for it: a Projects delete is permanent. */}
      <ConfirmDialog
        open={Boolean(confirmingDelete)}
        {...deleteCopy}
        onCancel={() => setConfirmingDelete(null)}
        onConfirm={() => {
          const pending = confirmingDelete;
          setConfirmingDelete(null);
          if (pending?.kind === "one") void deleteTaskById(pending.taskId, true);
          if (pending?.kind === "bulk") void applyBulkAction("delete", pending.ids);
        }}
      />

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

      {managingTags ? (
        <TagManager
          projectId={managingTags.id}
          projectName={managingTags.name}
          onClose={() => setManagingTags(null)}
          onChanged={setTags}
          // A rename or merge rewrites task rows, so the board is stale until
          // it reloads — the chips would otherwise show a name no card carries.
          onTasksTouched={() => {
            if (selected) void loadProject(selected);
          }}
        />
      ) : null}

      {managingStatuses ? (
        <StatusManager
          projectId={managingStatuses.id}
          projectName={managingStatuses.name}
          onClose={() => setManagingStatuses(null)}
          // `setStatuses` is the board's own lane source, so a rename or a
          // recolour repaints the lanes behind the open dialog.
          onChanged={setStatuses}
          // A re-categorise can stamp or clear `completed_at` across a whole
          // lane, and a rename changes what every card reads. Either way the
          // task rows on screen are stale until they reload.
          onTasksTouched={() => {
            if (selected) void loadProject(selected);
          }}
        />
      ) : null}

      {managingLifecycle ? (
        <LifecyclePolicy
          project={managingLifecycle}
          onClose={() => setManagingLifecycle(null)}
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

      {managingFields ? (
        <FieldManager
          projectId={managingFields.id}
          projectName={managingFields.name}
          onClose={() => setManagingFields(null)}
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
  // WS-27bm — the docked chat's one state for this render (`lib/chatDock.ts`).
  // The toggle and the column both read it, so the button is pressed exactly
  // when the column is on screen.
  const dockState = chatDockState({
    live: CHAT_LIVE,
    docked: chatDocked,
    wide: dockWide && !isMobile,
    slotOpen: app === "ai-chat",
    taskDocked: Boolean(taskPanel) && !isOverlayMode(panelMode),
  });
  // The top-bar button, whole — pressed, tooltip, and what a press changes.
  const assistant = assistantButton({
    state: dockState,
    wide: dockWide,
    slotOpen: app === "ai-chat",
  });

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
          {/* The assistant sits at the right end of the top bar, where My
              Tasks puts it (owner ask, 2026-09-24). One component for both
              apps. It lived in the project header's action row until then,
              so a space, a folder, Analytics, Reports and the chat slot had
              no button at all. Fence: `components/AssistantToggle.test.ts`. */}
          {CHAT_LIVE ? (
            <AssistantToggle
              open={assistant.pressed}
              title={assistant.title}
              onToggle={() => {
                const { press } = assistant;
                if (press.app !== undefined) setApp(press.app);
                if (press.closeTask) setOpenTask(null);
                if (press.docked !== undefined) {
                  setChatDocked(press.docked);
                  writeChatDocked(press.docked);
                }
              }}
            />
          ) : null}
        </div>
      </div>

      <SidePanelFitContext.Provider value={panelFits}>
      <div ref={rowRef} className="flex min-h-0 flex-1 overflow-hidden">
        {railOpen ? (
          <nav ref={navRef} className="w-60 shrink-0 overflow-y-auto border-r border-border bg-card p-2">
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
              onDropNode={dropNode}
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
              onManageStatuses={manageSpace(setManagingStatuses)}
              onManageFields={manageSpace(setManagingFields)}
              onManageTags={manageSpace(setManagingTags)}
              onManageLifecycle={manageSpace(setManagingLifecycle)}
            />
          </nav>
        ) : null}

        {/* The shared side panel — where a chat's "Open in side panel" on a
            Markdown or HTML file, and a panel-surface generated view, draw.
            The main chat page mounts it; this page did not, so those clicks
            wrote to a store nothing rendered (owner report, 2026-09-23). It
            draws nothing until something is opened, and it is a LEFT column,
            as on the chat page: its resize handle and border assume that. */}
        {CHAT_LIVE ? <SidePanelEditor hideWhenEmpty /> : null}

        {/* `overflow-hidden`: the board never draws over its neighbours. A
            narrow board clips; it does not spill its tabs over the chat
            (WS-27bm S8 visual review). */}
        <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
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
              // `flex-wrap`: with the chat docked the canvas loses 26rem, and
              // an action row that cannot wrap paints its right half over the
              // chat column (measured at 1440, 2026-09-23).
              <div className="flex flex-wrap items-center gap-1 px-3 pb-2 pt-1.5">
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

        {/* WS-27bm — the AI chat DOCKED beside the canvas (`lib/chatDock.ts`).
            It shares the right-hand column with the docked task panel: while
            a task holds the column the chat hides but stays mounted, so a
            streaming reply keeps streaming. */}
        {dockState === "absent" ? null : (
          <aside
            ref={dockRef}
            aria-label="Assistant"
            hidden={dockState === "hidden"}
            className="flex w-[26rem] shrink-0 flex-col overflow-hidden border-l border-border"
          >
            <AssistantRail
              {...railPlace}
              view={noProjectChrome ? null : mode}
              onClose={() => {
                setChatDocked(false);
                writeChatDocked(false);
              }}
            />
          </aside>
        )}

      </div>
      </SidePanelFitContext.Provider>

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
