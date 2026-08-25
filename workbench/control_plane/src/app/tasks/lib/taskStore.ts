import { create } from "zustand";
import { dropIndexFor } from "@/lib/boardDrop";
import {
  allSelected,
  clickSelect,
  prune,
  type SelectionState,
} from "@/lib/selection";
import {
  Disposition,
  Energy,
  GtdContext,
  GtdItem,
  GtdProject,
  Person,
  OrgPerson,
  OrgPersonWrite,
  ProviderKind,
  Target,
  ViewKey,
} from "./types";
import {
  CONNECTED_PROVIDERS,
  MOCK_CONTEXTS,
  MOCK_ITEMS,
  MOCK_PEOPLE,
  MOCK_PROJECTS,
  type ConnectedProvider,
} from "./mockData";
import { isCalendarItem, isTickled } from "./utils";
import { type SyncState, canPush } from "./syncState";
import {
  DEFAULT_FILTERS,
  DEFAULT_SORT,
  rankForDrop,
  type GroupBy,
  type TaskFilters,
  type TaskSort,
} from "./ordering";
import {
  accountToProviderEntry,
  apiBulkDispose,
  apiBulkArchive,
  apiArchiveItem,
  apiAddSubtasks,
  apiListSubtasks,
  apiCapture,
  apiCaptureBatch,
  apiDeleteAccount,
  apiDeleteItem,
  apiRestoreItem,
  apiPurgeItem,
  apiOrganize,
  apiMergeInto,
  apiFileUnder,
  apiPatchItem,
  apiPushItem,
  apiRefreshSchema,
  apiRefreshMembers,
  apiCreateAccountProject,
  apiCreateAccountFolder,
  apiSyncTasks,
  apiAtomize,
  apiEnrichItem,
  apiBackfillContext,
  apiDelegateItem,
  fetchTaskSettings,
  updateTaskSettings,
  type TaskSettings,
  fetchAccounts,
  fetchItems,
  fetchPeople,
  fetchOrgPeople,
  createPerson,
  updatePerson,
  uploadResume,
  fetchProjects,
  fetchLocalHierarchy,
  apiCreateSpace,
  apiCreateFolder,
  apiCreateLocalProject,
  type LocalHierarchy,
  type OrganizeBody,
  type TaskAccount,
} from "./api";

/** Fire-and-forget a live-backend sync; the optimistic local update already
 *  happened, so a transient failure only means the next hydrate reconciles. */
function sync(promise: Promise<unknown>): void {
  void promise.catch(() => {});
}

/** Finalize any soft delete still pending in a snapshot — purge the rows (and
 *  propagate the ClickUp deletion). Called when a snapshot is superseded by a
 *  new one so a rapid second delete can't orphan the first's purge. Returns the
 *  same snapshot for convenient chaining; no-op if there's nothing pending. */
function flushPendingPurge(
  snap: UndoSnapshot | null,
  backend: "live" | "demo",
): void {
  if (snap?.softDeletedIds?.length && backend === "live") {
    sync(Promise.all(snap.softDeletedIds.map((id) => apiPurgeItem(id).catch(() => {}))));
  }
}

/** Sentinel @context for tasks that have none yet — the "@no context" bucket
 *  under My Next Actions. Synced ClickUp tasks arrive here (Clarify never ran),
 *  where they can be re-clarified or have their context auto-assigned. */
export const NO_CONTEXT = "@no context";

/** Fields shared by clarified items that can be stored on a PM tool. */
interface SyncFields {
  /** Local vs a connected PM tool (§5.1). */
  dest?: Target;
  projectId?: string;
  /** the tool's stage/status, e.g. "Backlog" | "To-do". */
  status?: string;
  /** due date / timeline (ISO). */
  dueAt?: string;
  /** the tool's assignee/owner. */
  assignee?: Person;
}

/** The outcome of clarifying an inbox item — the GTD decision tree (F2). */
export type ClarifyDecision =
  | { kind: "trash" }
  | { kind: "reference" }
  | { kind: "do-now" } // 2-minute rule → done
  | ({ kind: "someday" } & Pick<SyncFields, "dest" | "projectId" | "status">)
  | ({ kind: "delegate"; person: Person; nextAction: string } & Pick<
      SyncFields,
      "dest" | "projectId" | "status" | "dueAt"
    >)
  | ({
      kind: "next";
      nextAction: string;
      context: string;
      energy?: Energy;
      timeEstimateMins?: number;
      /** break this task into concrete child subtasks (local or ClickUp). */
      subtasks?: string[];
    } & SyncFields)
  | ({ kind: "calendar"; nextAction: string; dueAt: string; context?: string } & Omit<
      SyncFields,
      "dueAt"
    >)
  | ({
      // turn the item into a new project's first next action (GTD: outcome + next action)
      kind: "project";
      outcome: string;
      nextAction: string;
      context?: string;
      energy?: Energy;
      /** additional steps beyond the first next action, created as subtasks. */
      subtasks?: string[];
    } & SyncFields);

/** The editable metadata of a task (post-clarify edit). Every field optional —
 *  only what's provided changes. Maps to the gateway PATCH /items/{id}. Nulls
 *  aren't used; pass "" to clear a string field, undefined to leave it. */
export interface ItemMetaPatch {
  title?: string;
  notes?: string;
  nextAction?: string;
  context?: string;
  energy?: Energy;
  timeEstimateMins?: number;
  dueAt?: string;              // ISO; "" clears
  /** timeboxing (calendar_timeboxing.md §3) — ISO; "" clears (unschedule) */
  scheduledStart?: string;
  scheduledEnd?: string;
  /** false = FIXED block (meeting) the auto-mover leaves put; true = flexible */
  flexible?: boolean;
  /** actuals (focus timer + completion) — ISO; "" clears */
  actualStart?: string;
  actualEnd?: string;
  providerStatus?: string;    // the tool's stage
  workflowStage?: string;     // the local Kanban stage (board move)
  sortKey?: number;           // manual (drag) rank within a group/column
  assignee?: Person | null;   // null → unassign
  /** the full owner set — [] unassigns everyone; takes precedence over assignee
   *  and keeps the primary `assignee` (= assignees[0]) in step. */
  assignees?: Person[] | null;
  /** personal "My Next Actions" membership (My Next Actions = NEXT & isMine).
   *  false drops a handed-off/unassigned task from my list without deleting it
   *  on ClickUp; a LOCAL overlay only — never back-synced. */
  isMine?: boolean;
  /** prioritization matrix flags (local overlay; urgent is derived, not here) */
  important?: boolean;
  leveraged?: boolean;
  /** needs an unbroken flow state (deep/creative/builder work) */
  deepWork?: boolean;
  /** dismiss the delegate/schedule suggestion ("this one's mine") */
  keptMine?: boolean;
  /** the date the person we're waiting on actually PROMISED — ISO; "" clears
   *  it back to null ("no promise was made", and the Waiting-For overdue line
   *  then reads dueAt live). Lands on the item's open gtd_waiting record, not
   *  on gtd_items; local only, never back-synced. See lib/waiting.ts. */
  expectedBy?: string;
}

/** Resolve a storage target into item source/provider/syncState fields.
 *  SYNCED items start 'pending' — the actual write to ClickUp/Jira is
 *  Action-Broker-gated, so they queue until pushed (or finished later). */
function targetFields(
  t?: Target,
): {
  source: "LOCAL" | "SYNCED";
  provider: ProviderKind;
  /** Narrowed through `Extract` rather than re-typed as a local union: the two
   *  values a NEW item can be born with are a subset of the one vocabulary
   *  (`lib/syncState.ts`), never a second one. A spelling change there fails to
   *  compile here instead of quietly leaving this literal behind. */
  syncState: Extract<SyncState, "local" | "pending">;
  accountId?: string;
} | null {
  if (!t) return null;
  return {
    source: t.source,
    provider: t.source === "LOCAL" ? "local" : t.provider ?? "clickup",
    syncState: t.source === "SYNCED" ? "pending" : "local",
    accountId: t.source === "SYNCED" ? t.accountId : undefined,
  };
}

/** Map a UI ClarifyDecision to the gateway's organize body (live mode). */
function decisionToOrganizeBody(d: ClarifyDecision): OrganizeBody {
  const body: OrganizeBody = { kind: d.kind };
  const dest = "dest" in d ? d.dest : undefined;
  if (dest?.source === "SYNCED" && dest.accountId) body.account_id = dest.accountId;
  if ("projectId" in d && d.projectId) body.project_id = d.projectId;
  if ("status" in d && d.status) body.status = d.status;
  if ("dueAt" in d && d.dueAt) body.due_at = d.dueAt;
  if ("nextAction" in d && d.nextAction) body.next_action = d.nextAction;
  if ("context" in d && d.context) body.context = d.context;
  if ("energy" in d && d.energy) body.energy = d.energy;
  if ("timeEstimateMins" in d && d.timeEstimateMins)
    body.time_estimate_mins = d.timeEstimateMins;
  if ("subtasks" in d && d.subtasks && d.subtasks.length)
    body.subtasks = d.subtasks;
  if (d.kind === "project") body.outcome = d.outcome;
  if (d.kind === "delegate")
    body.assignee = {
      name: d.person.name,
      email: d.person.email,
      provider_user_id: d.person.providerUserId,
    };
  else if ("assignee" in d && d.assignee)
    body.assignee = {
      name: d.assignee.name,
      email: d.assignee.email,
      provider_user_id: d.assignee.providerUserId,
    };
  return body;
}

function applyDecision(
  item: GtdItem,
  d: Exclude<ClarifyDecision, { kind: "project" }>,
): GtdItem {
  const now = new Date().toISOString();
  const base: GtdItem = { ...item, updatedAt: now, clarifiedAt: now };
  switch (d.kind) {
    case "trash":
      return { ...base, disposition: "TRASH" };
    case "reference":
      return { ...base, disposition: "REFERENCE" };
    case "someday":
      return {
        ...base,
        disposition: "SOMEDAY",
        projectId: d.projectId ?? base.projectId,
        providerStatus: d.status ?? base.providerStatus,
        ...(targetFields(d.dest) ?? {}),
      };
    case "do-now":
      return { ...base, disposition: "DONE", isTwoMinute: true, completedAt: now };
    case "delegate":
      return {
        ...base,
        disposition: "WAITING",
        isMine: false,
        nextAction: d.nextAction,
        waitingOn: d.person,
        delegatedAt: now,
        projectId: d.projectId ?? base.projectId,
        providerStatus: d.status ?? base.providerStatus,
        dueAt: d.dueAt ?? base.dueAt,
        ...(targetFields(d.dest) ?? {}),
      };
    case "next": {
      // OWNER is independent of SIZE (Sort→Shape): a plain "next" decision
      // delegates too when it carries an assignee — same rule as the backend.
      const delegated = !!d.assignee;
      return {
        ...base,
        disposition: delegated ? "WAITING" : "NEXT",
        isMine: !delegated,
        waitingOn: delegated ? d.assignee : base.waitingOn,
        delegatedAt: delegated ? now : base.delegatedAt,
        nextAction: d.nextAction,
        context: d.context,
        energy: d.energy,
        timeEstimateMins: d.timeEstimateMins,
        projectId: d.projectId ?? base.projectId,
        providerStatus: d.status ?? base.providerStatus,
        dueAt: d.dueAt ?? base.dueAt,
        assignee: d.assignee ?? base.assignee,
        ...(targetFields(d.dest) ?? {}),
      };
    }
    case "calendar": {
      const delegated = !!d.assignee;
      return {
        ...base,
        disposition: delegated ? "WAITING" : "NEXT",
        isMine: !delegated,
        waitingOn: delegated ? d.assignee : base.waitingOn,
        delegatedAt: delegated ? now : base.delegatedAt,
        nextAction: d.nextAction,
        context: d.context,
        dueAt: d.dueAt,
        isHardDate: true,
        projectId: d.projectId ?? base.projectId,
        providerStatus: d.status ?? base.providerStatus,
        assignee: d.assignee ?? base.assignee,
        ...(targetFields(d.dest) ?? {}),
      };
    }
  }
}

// The clarify AI proposal lives in lib/clarify.ts (proposeClarification).

// UI-first build: the store is seeded from bundled mock data (see mockData.ts).
// Capture writes to local state only. When the gateway `/tasks` API lands, these
// mutators get an async API call behind them; the component API stays the same.

let idCounter = 1000;
const nextId = () => `local-${idCounter++}`;

function makeCaptureItem(
  title: string,
  dates?: import("./api").CaptureDates,
): GtdItem {
  const ts = new Date().toISOString();
  return {
    id: nextId(),
    source: "LOCAL",
    provider: "local",
    title,
    disposition: "INBOX",
    isMine: true,
    createdAt: ts,
    updatedAt: ts,
    deferUntil: dates?.deferUntil,
    dueAt: dates?.dueAt,
    isHardDate: dates?.isHardDate,
  };
}

/** A restorable snapshot taken *before* a dispose/clarify, for one-level undo. */
interface UndoSnapshot {
  items: GtdItem[];
  projects: GtdProject[];
  processed: number;
  selectedItemId: string | null;
  /** human label for the toast, e.g. "Trashed" / "Filed under Someday". */
  label: string;
  /** which item ids the change touched (live mode reverts these server-side). */
  changedIds?: string[];
  /** Items HARD-DELETED by this change. Undo re-creates them server-side
   *  (a new row/id) via capture, since delete is permanent — unlike a
   *  disposition change, which undo reverts in place via changedIds. */
  deletedItems?: GtdItem[];
  /** Ids SOFT-DELETED by this change (the current delete path). While the undo
   *  toast is up the rows are only tombstoned server-side, so Undo restores
   *  them LOSSLESSLY (apiRestoreItem) — provider linkage/history intact. When
   *  the toast dismisses without an undo, they're purged (apiPurgeItem), which
   *  also propagates the deletion to ClickUp for synced tasks. */
  softDeletedIds?: string[];
  /** Ids (bulk-)ARCHIVED or -restored by this change; undo flips them back
   *  upstream. archivedTo is the direction that was applied (true = archived). */
  archivedIds?: string[];
  archivedTo?: boolean;
  /** Ids whose SCHEDULING (time block / pin) this change touched — an
   *  unschedule, drag/resize, roll-over, plan-apply or focus-mode reflow.
   *  Undo re-patches their scheduled_start/end + flexible from the snapshot
   *  rows, so a mis-drag on the calendar is always one tap from safe. */
  scheduleRevertIds?: string[];
}

/** Friendly past-tense label for a one-tap disposition (undo toast). */
const DISPOSE_LABEL: Partial<Record<Disposition, string>> = {
  TRASH: "Trashed",
  SOMEDAY: "Moved to Someday",
  REFERENCE: "Filed as Reference",
  DONE: "Marked done",
  NEXT: "Filed as Next action",
  WAITING: "Moved to Waiting",
};

/** Friendly label for a clarify decision (undo toast). */
function clarifyLabel(d: ClarifyDecision): string {
  switch (d.kind) {
    case "trash": return "Trashed";
    case "reference": return "Filed as Reference";
    case "someday": return "Moved to Someday";
    case "do-now": return "Marked done";
    case "delegate": return `Delegated to ${d.person.name}`;
    // OWNER is independent of SIZE — any of these delegates when it carries
    // an assignee (Sort→Shape), same rule the store/backend both apply.
    case "next": return d.assignee ? `Delegated to ${d.assignee.name}` : "Filed as Next action";
    case "calendar": return d.assignee ? `Delegated to ${d.assignee.name}` : "Scheduled";
    case "project": return d.assignee ? `Made a Project — delegated to ${d.assignee.name}` : "Made a Project";
  }
}

/** Apply a one-tap disposition (shared by quick + bulk dispose). */
function disposeOne(item: GtdItem, disposition: Disposition): GtdItem {
  const now = new Date().toISOString();
  return {
    ...item,
    disposition,
    updatedAt: now,
    clarifiedAt: now,
    ...(disposition === "DONE" ? { completedAt: now, isTwoMinute: true } : {}),
  };
}

interface TaskState {
  items: GtdItem[];
  projects: GtdProject[];
  contexts: GtdContext[];
  people: Person[];
  /** Full HR roster behind the People view (lazy-loaded on open). */
  orgPeople: OrgPerson[];
  /** 'demo' = bundled mock data (no gateway); 'live' = the /tasks API. */
  backend: "demo" | "live";
  /** True until the first hydrate() resolves — the UI shows a spinner instead
   *  of the (empty) initial state, so production never flashes mock data. */
  loading: boolean;
  /** Destination entries for Clarify — Local + each connected workspace.
   *  In live mode entry ids are task_account UUIDs. */
  providers: ConnectedProvider[];
  /** Connected PM-tool workspaces (live mode). */
  accounts: TaskAccount[];
  /** Every connected tool's statuses, de-duplicated + ordered — the ClickUp
   *  half of the status-column axis (see statusColumns). Recomputed whenever
   *  accounts are (re)fetched; empty when nothing is connected. */
  providerStatuses: string[];

  selectedView: ViewKey;
  /** when drilled into a single @context under Next Actions */
  selectedContext: string | null;
  selectedItemId: string | null;
  /** A task opened FULL-PAGE (focused overlay) — the ClickUp/Linear-style
   *  maximized view over the same editable detail. null = closed. */
  focusedItemId: string | null;
  openFocus: (id: string) => void;
  closeFocus: () => void;
  /** A task the "Schedule" affordance is scheduling (pill / card button / context
   *  menu) — drives the global SchedulePopup. null = closed. */
  scheduleItemId: string | null;
  openSchedule: (id: string) => void;
  closeSchedule: () => void;
  /** A task the "Eliminate" affordance is disposing (delete or Someday) — drives
   *  the global EliminatePopup. null = closed. */
  eliminateItemId: string | null;
  openEliminate: (id: string) => void;
  closeEliminate: () => void;
  /** A task the "Delegate" affordance is handing off (the nudge pill / column) —
   *  drives the global DelegatePopup (eligible people only; a LOCAL task routes
   *  on to the promote-to-ClickUp dialog). null = closed. */
  delegateItemId: string | null;
  openDelegate: (id: string) => void;
  closeDelegate: () => void;
  /** The Focus-Mode SESSION (the "Do" room + its timer). Store-held so the room
   *  can MINIMIZE to a compact dock that stays visible across the whole control
   *  plane (the component stays mounted in AppShell, so the pomodoro segment
   *  survives minimize/navigation). null = no session. Distinct from
   *  `focusedItemId` (the task-detail modal). */
  focusSessionId: string | null;
  focusMinimized: boolean;
  enterFocusSession: (id: string) => void;
  minimizeFocusSession: () => void;
  expandFocusSession: () => void;
  /** Remove the session UI. The component stops the remote timer (actualEnd)
   *  before calling this when the timer is still running. */
  clearFocusSession: () => void;
  /** "Mine only / Synced / All" board filter — hides the connected-workspace
   *  mirror so your own captures aren't swamped. */
  sourceFilter: "all" | "local" | "synced";
  setSourceFilter: (f: "all" | "local" | "synced") => void;
  /** Toolbar filters (search / context / assignee) applied to the active view
   *  in both list and board modes. */
  filters: TaskFilters;
  setFilters: (patch: Partial<TaskFilters>) => void;
  clearFilters: () => void;
  /** Ordering of the active view. "manual" enables drag-to-reorder; a field
   *  sort overrides manual position and disables dragging. */
  sort: TaskSort;
  setSort: (patch: Partial<TaskSort>) => void;
  /** The list "lens" — how the current view is sliced into sections. "" defers
   *  to the view's built-in grouping (context on Next Actions, flat elsewhere);
   *  a chosen value overrides it (priority / mode / energy / context / none). */
  groupBy: GroupBy | "";
  setGroupBy: (g: GroupBy | "") => void;

  /** ids of the most recent capture batch (for undo). */
  lastCaptureIds: string[];
  /** global quick-capture palette (ubiquitous capture). */
  quickCaptureOpen: boolean;
  quickCaptureMode: "single" | "sweep";
  /** the focused clarify overlay (keyboard-driven inbox processing). */
  clarifyModalOpen: boolean;
  // ⚠️ `workspacesModalOpen` / `openWorkspaces` / `closeWorkspaces` were
  // removed 2026-08-25 with the modal they drove (D52, WS-39 S1 repair round 1)
  // — the connect flow could only end in 400 "Unknown provider".
  /** count of items processed out of the inbox this session (momentum). */
  processedThisSession: number;
  /** one-level undo for the most recent dispose/clarify — the safety net that
   *  makes rapid triage feel safe (GTD: the system must be trusted). */
  undoSnapshot: UndoSnapshot | null;

  // actions
  selectView: (view: ViewKey) => void;
  selectContext: (context: string | null) => void;
  selectItem: (id: string | null) => void;
  /** Capture a new inbox item (frictionless quick-add). */
  capture: (title: string, attachments?: import("./types").TaskAttachment[], dates?: import("./api").CaptureDates) => void;
  /** Capture many items at once (mind sweep) — one per non-empty line. */
  captureMany: (text: string) => void;
  /** Group-context quick-add (WS-27y pattern): create a clarified NEXT action
   *  directly in a board column / list group, carrying the group's prefill
   *  (stage / @context / energy / deep-work) so it LANDS there — unlike
   *  `capture`, which always files into the Inbox. Returns the optimistic id
   *  (for the landing flash), or null for a blank title. */
  quickAddNext: (
    title: string,
    prefill: import("./quickAdd").QuickAddPrefill,
  ) => string | null;
  /** Undo the most recent capture batch (only items still in the inbox). */
  undoLastCapture: () => void;
  /** Clarify an inbox item — apply the GTD decision and advance to the next. */
  clarify: (
    id: string,
    decision: ClarifyDecision,
    /** the confirmed prioritization flags (from the clarify card's Weight
     *  toggles). Applied as a local overlay alongside the GTD decision. */
    weight?: { important: boolean; leveraged: boolean; deepWork: boolean },
  ) => void;
  /** Skip the current item (leave it in the inbox to process later) and move on. */
  skipToNextInbox: () => void;
  /** One-tap disposition (hover / keyboard triage) — no full decision tree. */
  quickDispose: (id: string, disposition: Disposition) => void;
  /** Apply the same disposition to many items at once (multi-select). */
  bulkDispose: (ids: string[], disposition: Disposition) => void;
  /** Archive (hide from active views) or un-archive a task — independent of
   *  DONE. Optimistic; the row moves to / from the Archive view. */
  archiveItem: (id: string, archived: boolean) => void;
  /** Archive (or un-archive) many tasks at once (multi-select). Optimistic +
   *  undoable; local overlay only (never touches the connected tool). */
  bulkArchive: (ids: string[], archived: boolean) => void;
  /** Lazily pull archived tasks into the store (they're excluded from the
   *  normal hydrate) — called when the Archive view is opened. */
  loadArchive: () => Promise<void>;
  /** Lazily pull DONE tasks into the store (excluded from the normal hydrate)
   *  — called when the Done view is opened. */
  loadDone: () => Promise<void>;
  /** Ids awaiting a delete confirmation (null = no dialog open). The delete
   *  becomes real only on confirmPendingDelete. */
  pendingDeleteIds: string[] | null;
  /** Request deletion of one or more items. Fresh inbox captures delete right
   *  away (undo covers them); ClickUp-synced OR already-clarified tasks open an
   *  "Are you sure?" confirm first (real consequences — the ClickUp task is
   *  ARCHIVED upstream, not hard-deleted, once the undo window passes). */
  requestDelete: (ids: string[]) => void;
  /** Confirm the pending delete (the dialog's primary action). */
  confirmPendingDelete: () => void;
  /** Dismiss the confirm dialog without deleting. */
  cancelPendingDelete: () => void;
  /**
   * True exactly when something is selected — i.e. when the bulk bar is up.
   *
   * ⚠️ **This is a DERIVED mirror of `selectedIds.size > 0`, not a mode**
   * (owner ruling 2026-08-10: /projects is canonical, /tasks conforms). It used
   * to be a mode you entered from a "Select" button, and entering it changed
   * what a click MEANT — the same row opened a task before the button and
   * toggled a checkbox after it. /projects never had that, and neither does
   * this app now: every list surface draws its checkbox unconditionally, beside
   * the row rather than over it, so clicking the row still opens it.
   *
   * It survives only because `TaskBoard` / `TaskCard` / `WaitingForView` still
   * read it while their own checkbox move lands. **Nothing may gate an
   * affordance on it again**; the invariant and the three surfaces that must
   * not consult it are pinned by `selectionParity.test.ts`.
   */
  selectMode: boolean;
  selectedIds: Set<string>;
  /** The last row picked WITHOUT shift — what a shift-click measures from.
   *
   *  WS-27ad: /tasks had no anchor at all, so shift did nothing here while it
   *  swept a range on /projects. The grammar is now one shared transition
   *  (`@/lib/selection.clickSelect`), so a member who learns shift-click on one
   *  surface has learnt it on both. */
  selectAnchor: string | null;
  /**
   * Pick one id.
   *
   * `visible` is the surface's OWN render order — the board walks columns, the
   * grouped list walks sections, and "between these two" means between them on
   * screen. The store cannot know it, so the surface passes it; a shift-click
   * with no order to measure against falls back to a plain toggle.
   */
  toggleSelected: (
    id: string,
    shift?: boolean,
    visible?: readonly string[],
  ) => void;
  /** Replace the selection outright — Shift+Arrow's swept superset. */
  extendSelection: (ids: readonly string[]) => void;
  /**
   * Select every row the view is currently showing, or clear if they already
   * all are — the header checkbox, /projects' `onToggleAll` (page.tsx) written
   * once here instead of at the call site.
   *
   * `visible` is the FILTERED set, never the whole store: "select all" on a
   * screen showing three of forty tasks has to mean those three, or the next
   * click archives thirty-seven rows nobody could see.
   */
  selectAllVisible: (visible: readonly string[]) => void;
  /**
   * Drop selected ids that are no longer on screen.
   *
   * A selection that outlives its filter is how a bulk action hits rows nobody
   * can see: select forty, type a search that leaves three, press Archive
   * believing you are acting on the three in front of you. /projects prunes on
   * every change of its visible set (`page.tsx`, `@/lib/selection.prune`); this
   * is the same rule on this side.
   */
  pruneSelection: (visible: readonly string[]) => void;
  /** Clear the selection (which also takes the bulk bar down). */
  clearSelection: () => void;
  /** Delete an item. SOFT delete (tombstone) → lossless Undo within the window,
   *  then purge (+ ClickUp propagation for synced tasks) on dismiss. */
  deleteItem: (id: string) => void;
  /** Delete many items at once (multi-select). Same soft-delete + undo model. */
  deleteItems: (ids: string[]) => void;
  /** Defer (tickler): hide from the active inbox until a date, then resurface. */
  deferItem: (id: string, dateIso: string) => void;
  /** Bring a deferred item back into the active inbox now. */
  undeferItem: (id: string) => void;
  /** Edit a task's editable fields — works for inbox captures AND clarified
   *  items (context/energy/estimate/due/stage/assignee/next action/notes).
   *  For a SYNCED ClickUp task, the mapped fields also back-sync upstream. */
  updateItem: (id: string, patch: ItemMetaPatch) => void;
  /** Apply one or many SCHEDULING changes (timebox / unschedule / move /
   *  resize / pin / plan-apply / roll-over / reflow) as a single UNDOABLE
   *  step: one snapshot, one undo-toast entry, however many blocks moved.
   *  `label` is the toast text ("Removed from calendar", "Planned 5 blocks"). */
  applySchedule: (
    label: string,
    changes: {
      id: string;
      patch: Pick<
        ItemMetaPatch,
        "scheduledStart" | "scheduledEnd" | "flexible"
      >;
    }[],
  ) => void;
  /** Drag-reorder: move `id` to `toIndex` within `groupItems` (the destination
   *  group's items in their current manual order), optionally re-filing it to a
   *  new workflow stage / provider status. Computes a fractional sortKey between
   *  the neighbours and patches sortKey (+ the stage change) in one write. */
  reorderItem: (
    id: string,
    groupItems: GtdItem[],
    toIndex: number,
    refile?: { workflowStage?: string; providerStatus?: string },
  ) => void;
  /** Fetch a task's child subtasks (local rows) — the detail panel calls this
   *  on open. Not held in the main items list (subtasks are nested). */
  loadSubtasks: (id: string) => Promise<GtdItem[]>;
  /** Add child subtasks to a task; returns the full ordered child list and
   *  bumps the parent's subtaskCount optimistically. */
  addSubtasks: (id: string, titles: string[]) => Promise<GtdItem[]>;
  /** Inline-rename a captured item (fix a typo without clarifying). */
  renameItem: (id: string, title: string) => void;
  /** Fold an inbox capture INTO an existing synced task (dedup "add to the
   *  existing ClickUp task") instead of creating a duplicate: the capture is
   *  removed locally and its info is appended to the target (back-synced). */
  mergeIntoExisting: (id: string, targetId: string) => Promise<void>;
  /** Rename an existing task (the dedup match) to a more descriptive title
   *  taken from the inbox capture, then drop the capture. For a SYNCED target
   *  the new name back-syncs to the connected tool (ClickUp). */
  renameExistingFromCapture: (
    captureId: string,
    existingId: string,
    newTitle: string,
  ) => Promise<void>;
  /** File an inbox capture as a SUB-STEP of an existing task (clarify "this is
   *  a step of X"): it becomes a nested child of the parent and leaves the
   *  flat inbox/next lists. A SYNCED parent's child pushes to ClickUp. */
  fileUnderParent: (id: string, parentId: string) => Promise<void>;
  /** Undo the most recent dispose/clarify — restores the item(s) to the inbox. */
  undoLastChange: () => void;
  /** Dismiss the undo affordance without undoing (e.g. after a timeout). */
  dismissUndo: () => void;
  /** Load live data from the gateway; silently stays on mock data if absent. */
  hydrate: () => Promise<void>;
  /** The LOCAL Space→Folder→Project tree (Projects view). Loaded lazily when
   *  the Projects view opens; null until then. */
  localHierarchy: LocalHierarchy | null;
  loadLocalHierarchy: () => Promise<void>;
  /** People view: lazy roster load + create/edit + résumé ingestion. */
  loadPeople: (opts?: { q?: string; includeInactive?: boolean }) => Promise<void>;
  savePerson: (id: string | null, body: OrgPersonWrite) => Promise<OrgPerson>;
  uploadPersonResume: (
    id: string,
    file: File
  ) => Promise<{ addedSkills: string[] }>;
  /** Create a local space / folder / project; refreshes the tree + projects. */
  createLocalSpace: (name: string) => Promise<void>;
  createLocalFolder: (spaceId: string, name: string) => Promise<void>;
  createLocalProject: (req: {
    outcome: string;
    spaceId?: string;
    folderId?: string;
  }) => Promise<void>;
  /** Re-fetch connected workspaces (after connect/refresh in the modal). */
  refreshAccounts: () => Promise<void>;
  /** Disconnect a workspace account. */
  disconnectAccount: (id: string) => Promise<void>;
  /** Refresh one account's provider schema (projects/members/statuses). */
  refreshAccountSchema: (id: string) => Promise<void>;
  /** LIVE member pull for one workspace — people removed in the tool
   *  disappear from the delegate picker. */
  refreshAccountMembers: (accountId: string) => Promise<void>;
  /** Create a NEW provider project (ClickUp list) under a space/folder. */
  createWorkspaceProject: (
    accountId: string,
    req: { name: string; spaceId: string; folderId?: string },
  ) => Promise<{ projectId: string; providerRef: string; name: string }>;
  /** Create a NEW provider folder (ClickUp: space → folder) under a space. */
  createWorkspaceFolder: (
    accountId: string,
    spaceId: string,
    name: string,
  ) => Promise<void>;
  /** Duplicate-capture notice: the AI found the just-captured item is the
   *  same as (verdict "duplicate" — auto-skipped, undoable) or similar to
   *  (verdict "similar" — the user decides) an existing open item. */
  dupNotice: {
    verdict: "duplicate" | "similar";
    /** the freshly captured item (already removed when verdict=duplicate) */
    title: string;
    itemId: string | null;
    matchTitle: string;
    matchId: string;
    /** where the match already lives — its GTD disposition + source (local vs
     *  ClickUp) — so the notice can say "already a Next action on ClickUp". */
    matchDisposition?: string;
    matchSource?: string;
  } | null;
  /** Resolve the dup notice: keep both / treat as the same (remove new) /
   *  rename the existing match to a clearer title (from the new capture, back-
   *  syncs for a SYNCED match) then drop the new copy / dismiss. */
  resolveDupNotice: (
    action: "keep" | "same" | "dismiss" | "rename",
    newTitle?: string,
  ) => void;
  /** Per-user task-manager settings (AI tiers + toggles). Defaults render
   *  immediately; hydrate() refreshes from the gateway. */
  settings: TaskSettings;
  /** Patch settings (optimistic; persisted via PUT /tasks/settings). */
  updateSettings: (patch: Partial<TaskSettings>) => Promise<void>;
  settingsModalOpen: boolean;
  openSettings: () => void;
  closeSettings: () => void;
  /** True while a provider pull (POST /tasks/sync) is in flight. */
  syncing: boolean;
  /** Pull existing provider tasks into the mirror (one account, or all),
   *  then re-fetch items so Waiting/Next fill from the connected tool. */
  syncNow: (accountId?: string) => Promise<void>;
  /** Explicit user-approved push of a pending item to its workspace. */
  pushItem: (id: string) => Promise<void>;
  openQuickCapture: (mode: "single" | "sweep") => void;
  closeQuickCapture: () => void;
  /** Open/close the clarify overlay for an item. */
  openClarify: (id: string) => void;
  closeClarify: () => void;
  /** Re-clarify an ALREADY-processed task (synced ClickUp task that skipped
   *  Clarify, or one that needs breaking down). Opens the same wizard seeded
   *  from the item's current state; a SYNCED task keeps its ClickUp binding. */
  reclarifyItemId: string | null;
  openReclarify: (id: string) => void;
  closeReclarify: () => void;
  /** Ask the assistant to fill a task's MISSING fields; returns the proposed
   *  values (the caller confirms, then applies via updateItem). */
  enrichItem: (id: string) => Promise<import("./api").EnrichFields>;
  /** Auto-assign @context to actionable tasks that have none; re-hydrates. */
  backfillContext: () => Promise<{ scanned: number; updated: number }>;
  /** Delegate a LOCAL task to a teammate → promote it to a ClickUp task under
   *  the chosen project. No-op for already-synced tasks (PATCH the assignee). */
  delegateLocalToClickUp: (
    id: string,
    req: {
      assignee: Person;
      accountId: string;
      projectId: string;
      nextAction?: string;
      status?: string;
      dueAt?: string;
    },
  ) => Promise<void>;
}

/**
 * The one place a selection transition becomes store state.
 *
 * Every selection write goes through here so `selectMode` cannot drift from
 * `selectedIds.size > 0` — the drift is what made it a *mode* rather than a
 * fact about the bulk bar, and a mode is what changed the meaning of a click.
 * Keeping the derivation in one function is why the invariant is testable in
 * one place (`selectionParity.test.ts`) instead of at five call sites.
 */
function applySelection(next: SelectionState): Pick<
  TaskState,
  "selectedIds" | "selectAnchor" | "selectMode"
> {
  const selected = new Set(next.selected);
  return {
    selectedIds: selected,
    selectAnchor: next.anchor,
    selectMode: selected.size > 0,
  };
}

export const useTaskStore = create<TaskState>((set, get) => ({
  // Start EMPTY + loading. hydrate() fills from the gateway (live) or, only if
  // the gateway is truly absent, falls back to the bundled mocks (demo/local
  // dev). This is what stops production briefly flashing dummy tasks on load.
  items: [],
  projects: [],
  contexts: MOCK_CONTEXTS,
  people: [],
  orgPeople: [],
  backend: "demo",
  loading: true,
  providers: CONNECTED_PROVIDERS,
  accounts: [],
  providerStatuses: [],
  localHierarchy: null,

  selectedView: "inbox",
  selectedContext: null,
  focusedItemId: null,
  openFocus: (id) => set({ focusedItemId: id, selectedItemId: id }),
  closeFocus: () => set({ focusedItemId: null }),
  scheduleItemId: null,
  openSchedule: (id) => set({ scheduleItemId: id }),
  closeSchedule: () => set({ scheduleItemId: null }),
  eliminateItemId: null,
  openEliminate: (id) => set({ eliminateItemId: id }),
  closeEliminate: () => set({ eliminateItemId: null }),
  delegateItemId: null,
  openDelegate: (id) => set({ delegateItemId: id }),
  closeDelegate: () => set({ delegateItemId: null }),
  focusSessionId: null,
  focusMinimized: false,
  // Entering the room also closes the task-detail modal (z-[80], which would
  // otherwise sit ON TOP of the z-[70] room it just launched).
  enterFocusSession: (id) =>
    set({ focusSessionId: id, focusMinimized: false, focusedItemId: null }),
  minimizeFocusSession: () => set({ focusMinimized: true }),
  expandFocusSession: () => set({ focusMinimized: false }),
  clearFocusSession: () => set({ focusSessionId: null, focusMinimized: false }),
  sourceFilter: "all",
  setSourceFilter: (f) => set({ sourceFilter: f }),
  filters: DEFAULT_FILTERS,
  setFilters: (patch) => set((s) => ({ filters: { ...s.filters, ...patch } })),
  clearFilters: () => set({ filters: DEFAULT_FILTERS }),
  sort: DEFAULT_SORT,
  setSort: (patch) => set((s) => ({ sort: { ...s.sort, ...patch } })),
  groupBy: "",
  setGroupBy: (g) => set({ groupBy: g }),
  selectedItemId: null,
  lastCaptureIds: [],
  quickCaptureOpen: false,
  quickCaptureMode: "single",
  clarifyModalOpen: false,
  processedThisSession: 0,
  undoSnapshot: null,
  pendingDeleteIds: null,
  selectMode: false,
  selectedIds: new Set<string>(),
  selectAnchor: null,

  toggleSelected: (id, shift = false, visible = []) =>
    set((s) =>
      applySelection(
        clickSelect(
          { selected: s.selectedIds, anchor: s.selectAnchor },
          visible,
          id,
          shift,
        ),
      ),
    ),
  extendSelection: (ids) =>
    set((s) => applySelection({ selected: new Set(ids), anchor: s.selectAnchor })),
  selectAllVisible: (visible) =>
    set((s) =>
      applySelection({
        // Already all on? Then the box is ticked and clicking it unticks —
        // the same transition /projects' header checkbox makes.
        selected: allSelected(s.selectedIds, visible)
          ? new Set<string>()
          : new Set(visible),
        anchor: null,
      }),
    ),
  pruneSelection: (visible) =>
    set((s) => {
      const kept = prune(s.selectedIds, visible);
      // Referentially stable when nothing left the screen: this runs from an
      // effect keyed on the visible set, and returning a fresh Set every time
      // would re-render every subscriber on every filter keystroke.
      if (kept.size === s.selectedIds.size) return {};
      return applySelection({ selected: kept, anchor: s.selectAnchor });
    }),
  clearSelection: () =>
    set(applySelection({ selected: new Set(), anchor: null })),

  selectView: (view) =>
    set({
      selectedView: view,
      selectedContext: null,
      selectedItemId: null,
      // A search/filter is scoped to the view you set it in — reset on nav so a
      // stale query doesn't silently hide items in the next view.
      filters: DEFAULT_FILTERS,
      // A multi-selection is scoped to its view too — drop it on nav so you
      // don't archive/delete rows you can no longer see. Through the same
      // helper as every other selection write, so this one cannot be the site
      // that reintroduces a `selectMode` disagreeing with the selection.
      ...applySelection({ selected: new Set(), anchor: null }),
    }),

  selectContext: (context) =>
    set({ selectedView: "next", selectedContext: context,
          selectedItemId: null }),

  selectItem: (id) => set({ selectedItemId: id }),

  capture: (title, attachments, dates) => {
    const t = title.trim();
    if (!t) return;
    const item = { ...makeCaptureItem(t, dates), attachments };
    set((s) => ({ items: [item, ...s.items], lastCaptureIds: [item.id] }));
    if (get().backend === "live") {
      // Optimistic row already shown; swap in the server row (real id) when it lands.
      sync(
        apiCapture(t, undefined, attachments, dates).then((server) => {
          set((s) => ({
            items: s.items.map((i) => (i.id === item.id ? server : i)),
            lastCaptureIds: s.lastCaptureIds.map((x) =>
              x === item.id ? server.id : x,
            ),
            selectedItemId:
              s.selectedItemId === item.id ? server.id : s.selectedItemId,
          }));
          // Background duplicate check (capture stays frictionless): the AI
          // compares the new capture against open items. Confident duplicate
          // → auto-remove with an undoable notice; similar → ask the user.
          if (!get().settings.captureDedup) return;
          apiAtomize(t, { excludeIds: [server.id] })
            .then(({ items: cands }) => {
              const c = cands[0];
              // The atomizer sees the just-created row too — a self-match
              // (same id) is not a duplicate.
              if (!c || c.verdict === "new" || !c.matchId || c.matchId === server.id) return;
              if (c.verdict === "duplicate") {
                set((s) => ({
                  items: s.items.filter((i) => i.id !== server.id),
                  dupNotice: {
                    verdict: "duplicate", title: server.title,
                    itemId: null, matchTitle: c.matchTitle ?? "",
                    matchId: c.matchId!,
                    matchDisposition: c.matchDisposition,
                    matchSource: c.matchSource,
                  },
                }));
                apiDeleteItem(server.id).catch(() => {});
              } else {
                set({
                  dupNotice: {
                    verdict: "similar", title: server.title,
                    itemId: server.id, matchTitle: c.matchTitle ?? "",
                    matchId: c.matchId!,
                    matchDisposition: c.matchDisposition,
                    matchSource: c.matchSource,
                  },
                });
              }
            })
            .catch(() => { /* dedup is best-effort */ });
        }),
      );
    }
  },

  dupNotice: null,

  resolveDupNotice: (action, newTitle) => {
    const n = get().dupNotice;
    if (!n) return;
    if (action === "keep" && n.verdict === "duplicate") {
      // Re-add the auto-skipped capture ("add anyway").
      get().capture(n.title);
    } else if (action === "same" && n.verdict === "similar" && n.itemId) {
      // The user confirmed it's the same item — remove the new copy.
      const id = n.itemId;
      set((s) => ({ items: s.items.filter((i) => i.id !== id) }));
      if (get().backend === "live") sync(apiDeleteItem(id).catch(() => {}));
    } else if (action === "rename") {
      // The new capture's title is clearer — rename the existing match to it
      // (back-syncs for a SYNCED match), then drop the new copy if it's still
      // around (verdict "similar"; a "duplicate" was already auto-removed).
      const title = (newTitle ?? n.title).trim();
      if (title) {
        set((s) => ({
          items: s.items.map((i) =>
            i.id === n.matchId
              ? { ...i, title, updatedAt: new Date().toISOString() }
              : i,
          ),
        }));
        if (get().backend === "live") sync(apiPatchItem(n.matchId, { title }));
        if (n.itemId) {
          const id = n.itemId;
          set((s) => ({ items: s.items.filter((i) => i.id !== id) }));
          if (get().backend === "live") sync(apiDeleteItem(id).catch(() => {}));
        }
      }
    }
    set({ dupNotice: null });
  },

  captureMany: (text) => {
    const lines = text
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter(Boolean);
    if (!lines.length) return;
    const newItems = lines.map((l) => makeCaptureItem(l));
    set((s) => ({
      items: [...newItems, ...s.items],
      lastCaptureIds: newItems.map((i) => i.id),
    }));
    if (get().backend === "live") {
      sync(
        apiCaptureBatch(lines).then((serverItems) =>
          set((s) => {
            const byIndex = new Map(
              newItems.map((tmp, idx) => [tmp.id, serverItems[idx]]),
            );
            return {
              items: s.items.map((i) => byIndex.get(i.id) ?? i),
              lastCaptureIds: s.lastCaptureIds.map(
                (x) => byIndex.get(x)?.id ?? x,
              ),
            };
          }),
        ),
      );
    }
  },

  quickAddNext: (title, prefill) => {
    const t = title.trim();
    if (!t) return null;
    const now = new Date().toISOString();
    // The board's own rule (see updateItem): filing into the LAST configured
    // stage means the task is DONE — a quick-add into the Done column is a
    // log entry, not a to-do.
    const stages = get().settings.workflowStages;
    const lastStage =
      prefill.workflowStage !== undefined &&
      stages.length > 0 &&
      prefill.workflowStage === stages[stages.length - 1];
    // WS-27ad — a flat view's box says which bucket outright (`viewQuickAdd`:
    // Someday incubates, Done logs). The board's last-stage rule stands where
    // nothing was said.
    const disposition: Disposition =
      prefill.disposition ?? (lastStage ? "DONE" : "NEXT");
    const item: GtdItem = {
      ...makeCaptureItem(t),
      // Born clarified: the group the add sits in already answered "what is
      // this?" — it is a next action ON that stage/context/energy, and the
      // title IS the next physical step.
      nextAction: t,
      clarifiedAt: now,
      ...prefill,
      disposition,
      // Stamped from the resolved disposition, not from the stage rule alone —
      // a Done logged from the Done list is as complete as one dropped in the
      // last column, and a completed row with no `completedAt` sorts as if it
      // finished in 1970.
      ...(disposition === "DONE" ? { completedAt: now } : {}),
    };
    set((s) => ({ items: [item, ...s.items] }));
    if (get().backend === "live") {
      sync(
        // Create + clarify as capture-then-patch: /items has no "born NEXT"
        // shape, and this is the same two-step Clarify itself rides.
        apiCapture(t).then(async (server) => {
          const body: Parameters<typeof apiPatchItem>[1] = {
            disposition: item.disposition,
            next_action: t,
          };
          if (prefill.workflowStage !== undefined)
            body.workflow_stage = prefill.workflowStage;
          if (prefill.context !== undefined) body.context = prefill.context;
          if (prefill.energy !== undefined) body.energy = prefill.energy;
          if (prefill.deepWork !== undefined) body.deep_work = prefill.deepWork;
          // If the clarify PATCH fails the capture still exists — keep the
          // server row (it will show in the Inbox, which is honest) rather
          // than inviting a duplicate-creating retry.
          const final = await apiPatchItem(server.id, body).catch(() => server);
          set((s) => ({
            items: s.items.map((i) => (i.id === item.id ? final : i)),
          }));
        }),
      );
    }
    return item.id;
  },

  undoLastCapture: () => {
    const ids = get().lastCaptureIds;
    if (!ids.length) return;
    const remove = new Set(ids);
    set((s) => ({
      items: s.items.filter(
        (i) => !(remove.has(i.id) && i.disposition === "INBOX"),
      ),
      lastCaptureIds: [],
    }));
    if (get().backend === "live") {
      sync(Promise.all(ids.map((id) => apiDeleteItem(id).catch(() => {}))));
    }
  },

  openQuickCapture: (mode) => set({ quickCaptureOpen: true, quickCaptureMode: mode }),
  closeQuickCapture: () => set({ quickCaptureOpen: false }),
  openClarify: (id) => set({ selectedItemId: id, clarifyModalOpen: true }),
  closeClarify: () => set({ clarifyModalOpen: false }),
  reclarifyItemId: null,
  openReclarify: (id) => set({ selectedItemId: id, reclarifyItemId: id }),
  closeReclarify: () => set({ reclarifyItemId: null }),

  enrichItem: async (id) => {
    if (get().backend !== "live") return {};
    try {
      return await apiEnrichItem(id);
    } catch {
      return {};
    }
  },

  backfillContext: async () => {
    if (get().backend !== "live") return { scanned: 0, updated: 0 };
    const res = await apiBackfillContext();
    if (res.updated > 0) {
      // Contexts changed server-side — re-pull so the @context sidebar + the
      // Next Actions list reflect the new assignments.
      try {
        set({ items: await fetchItems("all") });
      } catch {
        /* next hydrate reconciles */
      }
    }
    return res;
  },

  delegateLocalToClickUp: async (id, req) => {
    if (get().backend !== "live") return;
    const server = await apiDelegateItem(id, {
      assignee: {
        name: req.assignee.name,
        email: req.assignee.email,
        provider_user_id: req.assignee.providerUserId,
      },
      account_id: req.accountId,
      project_id: req.projectId,
      next_action: req.nextAction,
      status: req.status,
      due_at: req.dueAt,
    });
    // The row is now SYNCED + WAITING (no longer in My Next Actions). Swap in
    // the authoritative server row.
    set((s) => ({ items: s.items.map((i) => (i.id === id ? server : i)) }));
  },

  clarify: (id, decision, weight) => {
    flushPendingPurge(get().undoSnapshot, get().backend);
    // The confirmed matrix flags overlay the decision. Applied locally to the
    // clarified row and (live) patched after organize, independent of the GTD
    // disposition so the golden-eval organize path stays untouched.
    const applyWeight = (i: GtdItem): GtdItem =>
      weight ? { ...i, important: weight.important, leveraged: weight.leveraged, deepWork: weight.deepWork } : i;
    set((s) => {
      const snapshot: UndoSnapshot = {
        items: s.items,
        projects: s.projects,
        processed: s.processedThisSession,
        selectedItemId: s.selectedItemId,
        label: clarifyLabel(decision),
        changedIds: [id],
      };
      let projects = s.projects;
      let items: GtdItem[];
      if (decision.kind === "project") {
        // Create a project and make this item its first next action. OWNER is
        // independent of SIZE (Sort→Shape): a project can ALSO be delegated —
        // mirror that the same way applyDecision's "delegate" case does.
        const now = new Date().toISOString();
        const pid = nextId();
        const tf =
          targetFields(decision.dest) ??
          { source: "LOCAL" as const, provider: "local" as ProviderKind, syncState: "local" as const };
        const project: GtdProject = {
          id: pid,
          source: tf.source,
          provider: tf.provider,
          accountId: tf.accountId,
          outcome: decision.outcome,
          status: "ACTIVE",
          hasNextAction: true,
        };
        projects = [project, ...s.projects];
        const delegated = !!decision.assignee;
        items = s.items.map((i) =>
          i.id === id
            ? {
                ...i,
                disposition: delegated ? "WAITING" : "NEXT",
                isMine: !delegated,
                waitingOn: delegated ? decision.assignee : i.waitingOn,
                delegatedAt: delegated ? now : i.delegatedAt,
                nextAction: decision.nextAction,
                context: decision.context,
                energy: decision.energy,
                projectId: pid,
                source: tf.source,
                provider: tf.provider,
                accountId: tf.accountId,
                syncState: tf.syncState,
                providerStatus: decision.status,
                dueAt: decision.dueAt ?? i.dueAt,
                assignee: decision.assignee ?? i.assignee,
                updatedAt: now,
                clarifiedAt: now,
              }
            : i,
        );
      } else {
        items = s.items.map((i) => (i.id === id ? applyDecision(i, decision) : i));
      }
      // Overlay the confirmed matrix flags on the clarified row.
      if (weight) items = items.map((i) => (i.id === id ? applyWeight(i) : i));
      // Re-clarify (the item wasn't in the inbox) is an in-place edit — don't
      // walk the inbox, bump the session counter, or close the reclarify modal
      // out from under the wizard's own close handler.
      const wasInbox =
        s.items.find((i) => i.id === id)?.disposition === "INBOX";
      if (!wasInbox) {
        return { items, projects, undoSnapshot: snapshot };
      }
      // advance to the OLDEST remaining inbox item — GTD processes FIFO
      const remaining = items.filter((i) => i.disposition === "INBOX");
      const nextInbox = remaining.length
        ? remaining.reduce((a, b) =>
            new Date(b.createdAt) < new Date(a.createdAt) ? b : a,
          )
        : undefined;
      return {
        items,
        projects,
        selectedItemId: nextInbox?.id ?? null,
        processedThisSession: s.processedThisSession + 1,
        undoSnapshot: snapshot,
      };
    });
    if (get().backend === "live") {
      const apply = apiOrganize(id, decisionToOrganizeBody(decision));
      // Persist the confirmed matrix flags as a local overlay (best-effort;
      // independent of organize so a flag hiccup never blocks the decision).
      if (weight) {
        sync(
          apply
            .then(() =>
              apiPatchItem(id, {
                important: weight.important,
                leveraged: weight.leveraged,
                deep_work: weight.deepWork,
              }),
            )
            .then((updated) =>
              set((s) => ({
                items: s.items.map((i) => (i.id === id ? updated : i)),
              })),
            ),
        );
      }
      if (decision.kind === "project") {
        // The server mints its own project id — reconcile both lists so the
        // optimistic local project/id drift doesn't linger.
        sync(
          apply.then(async () => {
            const [items, projects] = await Promise.all([
              fetchItems("all"),
              fetchProjects(),
            ]);
            set({ items, projects });
          }),
        );
      } else {
        sync(
          apply.then(async (server) => {
            set((s) => ({
              items: s.items.map((i) => (i.id === id ? server : i)),
            }));
            // Default-sync posture: an accepted decision that targeted a
            // workspace (the accept UI showed the destination) pushes
            // immediately when the tool has everything it needs (a real
            // provider project). Otherwise it stays staged with the manual
            // Push affordance — never lost, never silently failing.
            if (canPush(server.syncState) && server.projectId) {
              try {
                const pushed = await apiPushItem(server.id);
                set((s) => ({
                  items: s.items.map((i) => (i.id === pushed.id ? pushed : i)),
                }));
              } catch {
                /* stays pending — the Push button remains */
              }
            }
          }),
        );
      }
    }
  },

  /** LIVE member refresh for one workspace (delegate-picker freshness). */
  refreshAccountMembers: async (accountId: string) => {
    if (get().backend !== "live") return;
    try {
      const fresh = await apiRefreshMembers(accountId);
      set((s) => ({
        accounts: s.accounts.map((a) => (a.id === accountId ? fresh : a)),
      }));
    } catch {
      /* keep cached members */
    }
  },

  /** Create a NEW provider project (ClickUp list) under a space/folder and
   *  refresh the mirrored project list so pickers see it immediately. */
  createWorkspaceProject: async (accountId, req) => {
    const created = await apiCreateAccountProject(accountId, req);
    try {
      const [projects, accounts] = await Promise.all([
        fetchProjects(),
        fetchAccounts(),
      ]);
      set({ projects, accounts });
    } catch {
      /* next hydrate reconciles */
    }
    return created;
  },

  /** Create a NEW provider folder under a space and refresh the account so
   *  the picker's hierarchy shows it immediately (empty, ready for lists). */
  createWorkspaceFolder: async (accountId, spaceId, name) => {
    if (!name.trim()) return;
    await apiCreateAccountFolder(accountId, { name: name.trim(), spaceId });
    try {
      set({ accounts: await fetchAccounts() });
    } catch {
      /* next hydrate reconciles */
    }
  },

  skipToNextInbox: () =>
    set((s) => {
      const inbox = s.items
        .filter((i) => i.disposition === "INBOX")
        .sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());
      if (inbox.length <= 1) return s; // nothing else to move to
      const idx = inbox.findIndex((i) => i.id === s.selectedItemId);
      const next = inbox[(idx + 1) % inbox.length];
      return { selectedItemId: next.id };
    }),

  quickDispose: (id, disposition) => {
    flushPendingPurge(get().undoSnapshot, get().backend);
    set((s) => ({
      items: s.items.map((i) => (i.id === id ? disposeOne(i, disposition) : i)),
      processedThisSession: s.processedThisSession + 1,
      undoSnapshot: {
        items: s.items,
        projects: s.projects,
        processed: s.processedThisSession,
        selectedItemId: s.selectedItemId,
        label: DISPOSE_LABEL[disposition] ?? "Filed",
        changedIds: [id],
      },
    }));
    if (get().backend === "live") sync(apiBulkDispose([id], disposition));
  },

  bulkDispose: (ids, disposition) => {
    flushPendingPurge(get().undoSnapshot, get().backend);
    set((s) => {
      const set_ = new Set(ids);
      const affected = s.items.filter(
        (i) => set_.has(i.id) && i.disposition === "INBOX",
      ).length;
      return {
        items: s.items.map((i) =>
          set_.has(i.id) ? disposeOne(i, disposition) : i,
        ),
        processedThisSession: s.processedThisSession + affected,
        undoSnapshot: {
          items: s.items,
          projects: s.projects,
          processed: s.processedThisSession,
          selectedItemId: s.selectedItemId,
          label: `${DISPOSE_LABEL[disposition] ?? "Filed"} ${affected} item${affected === 1 ? "" : "s"}`,
          changedIds: ids,
        },
      };
    });
    if (get().backend === "live") sync(apiBulkDispose(ids, disposition));
  },

  archiveItem: (id, archived) => {
    const nowIso = new Date().toISOString();
    set((s) => ({
      items: s.items.map((i) =>
        i.id === id
          ? { ...i, archivedAt: archived ? nowIso : undefined, updatedAt: nowIso }
          : i,
      ),
      // Close the pop-up if we're archiving the focused task.
      focusedItemId:
        archived && s.focusedItemId === id ? null : s.focusedItemId,
    }));
    if (get().backend === "live") sync(apiArchiveItem(id, archived));
  },

  loadArchive: async () => {
    if (get().backend !== "live") return;
    try {
      const archived = await fetchItems("archive");
      set((s) => {
        // Merge: replace any existing rows by id, add the rest.
        const byId = new Map(s.items.map((i) => [i.id, i]));
        for (const a of archived) byId.set(a.id, a);
        return { items: Array.from(byId.values()) };
      });
    } catch {
      /* keep current state */
    }
  },

  loadDone: async () => {
    // DONE tasks are excluded from the "all" hydrate (so a big completed
    // backlog can't swamp the board), so the Done view lazy-loads them the
    // same way Archive does.
    if (get().backend !== "live") return;
    try {
      const done = await fetchItems("done");
      set((s) => {
        const byId = new Map(s.items.map((i) => [i.id, i]));
        for (const d of done) byId.set(d.id, d);
        return { items: Array.from(byId.values()) };
      });
    } catch {
      /* keep current state */
    }
  },

  bulkArchive: (ids, archived) => {
    const nowIso = new Date().toISOString();
    const set_ = new Set(ids);
    const affected = get().items.filter(
      (i) => set_.has(i.id) && Boolean(i.archivedAt) !== archived,
    );
    if (!affected.length) return;
    flushPendingPurge(get().undoSnapshot, get().backend);
    set((s) => ({
      items: s.items.map((i) =>
        set_.has(i.id)
          ? { ...i, archivedAt: archived ? nowIso : undefined, updatedAt: nowIso }
          : i,
      ),
      focusedItemId:
        archived && s.focusedItemId && set_.has(s.focusedItemId)
          ? null
          : s.focusedItemId,
      undoSnapshot: {
        items: s.items,
        projects: s.projects,
        processed: s.processedThisSession,
        selectedItemId: s.selectedItemId,
        label: `${archived ? "Archived" : "Restored"} ${affected.length} item${affected.length === 1 ? "" : "s"}`,
        // Archive is a local overlay (not a disposition change and not a
        // delete), so undo restores the snapshot locally + flips the flag back
        // upstream via bulk-archive.
        archivedIds: affected.map((i) => i.id),
        archivedTo: archived,
      },
    }));
    if (get().backend === "live") sync(apiBulkArchive(ids, archived));
  },

  loadLocalHierarchy: async () => {
    if (get().backend !== "live") return;
    try {
      set({ localHierarchy: await fetchLocalHierarchy() });
    } catch {
      /* keep current */
    }
  },

  loadPeople: async (opts) => {
    if (get().backend !== "live") return;
    try {
      set({ orgPeople: await fetchOrgPeople(opts) });
    } catch {
      /* keep current */
    }
  },

  savePerson: async (id, body) => {
    const saved = id ? await updatePerson(id, body) : await createPerson(body);
    set((s) => {
      const exists = s.orgPeople.some((p) => p.id === saved.id);
      return {
        orgPeople: exists
          ? s.orgPeople.map((p) => (p.id === saved.id ? saved : p))
          : [...s.orgPeople, saved],
      };
    });
    return saved;
  },

  uploadPersonResume: async (id, file) => {
    const res = await uploadResume(id, file);
    // Reflect the merged skills/profile in the roster immediately.
    set((s) => ({
      orgPeople: s.orgPeople.map((p) => (p.id === id ? res.person : p)),
    }));
    return { addedSkills: res.addedSkills };
  },

  createLocalSpace: async (name) => {
    if (!name.trim() || get().backend !== "live") return;
    await apiCreateSpace(name.trim());
    await get().loadLocalHierarchy();
  },

  createLocalFolder: async (spaceId, name) => {
    if (!name.trim() || get().backend !== "live") return;
    await apiCreateFolder(spaceId, name.trim());
    await get().loadLocalHierarchy();
  },

  createLocalProject: async (req) => {
    if (!req.outcome.trim() || get().backend !== "live") return;
    await apiCreateLocalProject({ ...req, outcome: req.outcome.trim() });
    // A new project shows in BOTH the tree and the flat projects list.
    await Promise.all([
      get().loadLocalHierarchy(),
      fetchProjects().then((projects) => set({ projects })).catch(() => {}),
    ]);
  },

  requestDelete: (ids) => {
    const items = get().items;
    const targets = ids
      .map((id) => items.find((i) => i.id === id))
      .filter((t): t is GtdItem => !!t);
    if (!targets.length) return;
    // Confirm before deleting anything with real consequences: a ClickUp-synced
    // task (its upstream counterpart gets deleted too) or an already-clarified
    // task (it's real work, not a stray capture). Fresh inbox captures skip the
    // dialog — undo is enough for fast triage.
    const needsConfirm = targets.some(
      (t) => t.source !== "LOCAL" || t.disposition !== "INBOX",
    );
    if (needsConfirm) {
      set({ pendingDeleteIds: targets.map((t) => t.id) });
      return;
    }
    if (targets.length === 1) get().deleteItem(targets[0].id);
    else get().deleteItems(targets.map((t) => t.id));
  },

  confirmPendingDelete: () => {
    const ids = get().pendingDeleteIds;
    set({ pendingDeleteIds: null });
    if (!ids?.length) return;
    if (ids.length === 1) get().deleteItem(ids[0]);
    else get().deleteItems(ids);
  },

  cancelPendingDelete: () => set({ pendingDeleteIds: null }),

  deleteItem: (id) => {
    const target = get().items.find((i) => i.id === id);
    if (!target) return;
    // A prior soft-delete's purge must not be orphaned by this new snapshot.
    flushPendingPurge(get().undoSnapshot, get().backend);
    set((s) => ({
      items: s.items.filter((i) => i.id !== id),
      selectedItemId: s.selectedItemId === id ? null : s.selectedItemId,
      // Close the focus modal if we just deleted the focused task.
      focusedItemId: s.focusedItemId === id ? null : s.focusedItemId,
      undoSnapshot: {
        items: s.items,
        projects: s.projects,
        processed: s.processedThisSession,
        selectedItemId: s.selectedItemId,
        label: "Deleted",
        // Soft delete → lossless undo (restore) or a purge on dismiss.
        softDeletedIds: [id],
      },
    }));
    // Soft-delete server-side now; the actual removal + ClickUp propagation
    // happen on dismiss (see dismissUndo), so Undo can restore losslessly.
    if (get().backend === "live") sync(apiDeleteItem(id).catch(() => {}));
  },

  mergeIntoExisting: async (id, targetId) => {
    // The capture is absorbed into an existing synced task — drop it locally
    // right away; swap the enriched target row in when the server confirms.
    const removed = get().items.find((i) => i.id === id);
    set((s) => ({
      items: s.items.filter((i) => i.id !== id),
      selectedItemId: s.selectedItemId === id ? null : s.selectedItemId,
      undoSnapshot: removed
        ? {
            items: s.items,
            projects: s.projects,
            processed: s.processedThisSession,
            selectedItemId: s.selectedItemId,
            label: "Merged into existing task",
            deletedItems: [removed],
          }
        : s.undoSnapshot,
    }));
    if (get().backend !== "live") return;
    const target = await apiMergeInto(id, targetId);
    set((s) => ({
      items: s.items.map((i) => (i.id === target.id ? target : i)),
    }));
  },

  renameExistingFromCapture: async (captureId, existingId, newTitle) => {
    // The inbox capture is a duplicate whose title is clearer than the task
    // that already exists — rename the existing task to it (back-syncs to the
    // tool for a SYNCED target) and drop the now-absorbed capture.
    const title = newTitle.trim();
    if (!title) return;
    const capture = get().items.find((i) => i.id === captureId);
    set((s) => ({
      items: s.items
        .filter((i) => i.id !== captureId)
        .map((i) =>
          i.id === existingId
            ? { ...i, title, updatedAt: new Date().toISOString() }
            : i,
        ),
      selectedItemId: s.selectedItemId === captureId ? null : s.selectedItemId,
      undoSnapshot: capture
        ? {
            items: s.items,
            projects: s.projects,
            processed: s.processedThisSession,
            selectedItemId: s.selectedItemId,
            label: "Renamed existing task",
            deletedItems: [capture],
          }
        : s.undoSnapshot,
    }));
    if (get().backend !== "live") return;
    // Rename the existing task first (this is the write the user asked for);
    // patch back-syncs the new name upstream when the target is SYNCED.
    const updated = await apiPatchItem(existingId, { title });
    set((s) => ({
      items: s.items.map((i) => (i.id === updated.id ? updated : i)),
    }));
    // Then drop the absorbed capture.
    await apiDeleteItem(captureId).catch(() => {});
  },

  fileUnderParent: async (id, parentId) => {
    // The capture becomes a nested child of the parent — subtasks aren't held
    // in the flat list, so drop it from the inbox/next views immediately.
    const removed = get().items.find((i) => i.id === id);
    set((s) => ({
      items: s.items.filter((i) => i.id !== id),
      selectedItemId: s.selectedItemId === id ? null : s.selectedItemId,
      undoSnapshot: removed
        ? {
            items: s.items,
            projects: s.projects,
            processed: s.processedThisSession,
            selectedItemId: s.selectedItemId,
            label: "Filed as a subtask",
            deletedItems: [removed],
          }
        : s.undoSnapshot,
    }));
    if (get().backend !== "live") return;
    // Refresh the parent's row (subtaskCount) so the detail panel reflects it.
    const parent = await apiFileUnder(id, parentId);
    set((s) => ({
      items: s.items.map((i) => (i.id === parent.id ? parent : i)),
    }));
  },

  deleteItems: (ids) => {
    const remove = new Set(ids);
    const targets = get().items.filter((i) => remove.has(i.id));
    if (!targets.length) return;
    const targetIds = targets.map((t) => t.id);
    flushPendingPurge(get().undoSnapshot, get().backend);
    set((s) => ({
      items: s.items.filter((i) => !remove.has(i.id)),
      selectedItemId:
        s.selectedItemId && remove.has(s.selectedItemId)
          ? null
          : s.selectedItemId,
      focusedItemId:
        s.focusedItemId && remove.has(s.focusedItemId)
          ? null
          : s.focusedItemId,
      undoSnapshot: {
        items: s.items,
        projects: s.projects,
        processed: s.processedThisSession,
        selectedItemId: s.selectedItemId,
        label: `Deleted ${targets.length} item${targets.length === 1 ? "" : "s"}`,
        softDeletedIds: targetIds,
      },
    }));
    if (get().backend === "live") {
      sync(Promise.all(targetIds.map((id) => apiDeleteItem(id).catch(() => {}))));
    }
  },

  deferItem: (id, dateIso) => {
    set((s) => ({
      items: s.items.map((i) =>
        i.id === id
          ? { ...i, deferUntil: dateIso, updatedAt: new Date().toISOString() }
          : i,
      ),
    }));
    if (get().backend === "live") sync(apiPatchItem(id, { defer_until: dateIso }));
  },

  undeferItem: (id) => {
    set((s) => ({
      items: s.items.map((i) =>
        i.id === id
          ? { ...i, deferUntil: undefined, updatedAt: new Date().toISOString() }
          : i,
      ),
    }));
    if (get().backend === "live") sync(apiPatchItem(id, { defer_until: "" }));
  },

  updateItem: (id, patch) => {
    set((s) => ({
      items: s.items.map((i) => {
        if (i.id !== id) return i;
        const title = patch.title !== undefined ? patch.title.trim() : i.title;
        if (!title) return i; // never allow an empty title
        return {
          ...i,
          title,
          notes: patch.notes !== undefined ? patch.notes : i.notes,
          nextAction:
            patch.nextAction !== undefined ? patch.nextAction : i.nextAction,
          context: patch.context !== undefined ? patch.context : i.context,
          energy: patch.energy !== undefined ? patch.energy : i.energy,
          timeEstimateMins:
            patch.timeEstimateMins !== undefined
              ? patch.timeEstimateMins || undefined
              : i.timeEstimateMins,
          dueAt: patch.dueAt !== undefined ? patch.dueAt || undefined : i.dueAt,
          expectedBy:
            patch.expectedBy !== undefined
              ? patch.expectedBy || undefined
              : i.expectedBy,
          scheduledStart:
            patch.scheduledStart !== undefined
              ? patch.scheduledStart || undefined
              : i.scheduledStart,
          scheduledEnd:
            patch.scheduledEnd !== undefined
              ? patch.scheduledEnd || undefined
              : i.scheduledEnd,
          flexible: patch.flexible !== undefined ? patch.flexible : i.flexible,
          actualStart:
            patch.actualStart !== undefined
              ? patch.actualStart || undefined
              : i.actualStart,
          actualEnd:
            patch.actualEnd !== undefined
              ? patch.actualEnd || undefined
              : i.actualEnd,
          providerStatus:
            patch.providerStatus !== undefined
              ? patch.providerStatus
              : i.providerStatus,
          workflowStage:
            patch.workflowStage !== undefined
              ? patch.workflowStage
              : i.workflowStage,
          sortKey:
            patch.sortKey !== undefined ? patch.sortKey : i.sortKey,
          // Dropping on the last configured stage marks the task DONE — mirror
          // the backend optimistically so the card leaves the active board.
          disposition:
            patch.workflowStage !== undefined &&
            patch.workflowStage ===
              get().settings.workflowStages[
                get().settings.workflowStages.length - 1
              ]
              ? "DONE"
              : i.disposition,
          // The full owner set takes precedence and keeps the primary in step;
          // else the single-assignee patch; else unchanged.
          assignees:
            patch.assignees !== undefined
              ? patch.assignees ?? []
              : patch.assignee !== undefined
                ? patch.assignee
                  ? [patch.assignee]
                  : []
                : i.assignees,
          assignee:
            patch.assignees !== undefined
              ? patch.assignees?.[0] ?? undefined
              : patch.assignee !== undefined
                ? patch.assignee ?? undefined
                : i.assignee,
          isMine: patch.isMine !== undefined ? patch.isMine : i.isMine,
          important:
            patch.important !== undefined ? patch.important : i.important,
          leveraged:
            patch.leveraged !== undefined ? patch.leveraged : i.leveraged,
          deepWork:
            patch.deepWork !== undefined ? patch.deepWork : i.deepWork,
          keptMine:
            patch.keptMine !== undefined ? patch.keptMine : i.keptMine,
          updatedAt: new Date().toISOString(),
        };
      }),
    }));
    if (get().backend === "live") {
      const body: Parameters<typeof apiPatchItem>[1] = {};
      if (patch.title !== undefined && patch.title.trim())
        body.title = patch.title.trim();
      if (patch.notes !== undefined) body.notes = patch.notes;
      if (patch.nextAction !== undefined) body.next_action = patch.nextAction;
      if (patch.context !== undefined) body.context = patch.context;
      if (patch.energy !== undefined) body.energy = patch.energy;
      if (patch.timeEstimateMins !== undefined)
        body.time_estimate_mins = patch.timeEstimateMins;
      if (patch.dueAt !== undefined) body.due_at = patch.dueAt;
      if (patch.expectedBy !== undefined) body.expected_by = patch.expectedBy;
      if (patch.scheduledStart !== undefined)
        body.scheduled_start = patch.scheduledStart;
      if (patch.scheduledEnd !== undefined)
        body.scheduled_end = patch.scheduledEnd;
      if (patch.flexible !== undefined) body.flexible = patch.flexible;
      if (patch.actualStart !== undefined)
        body.actual_start = patch.actualStart;
      if (patch.actualEnd !== undefined) body.actual_end = patch.actualEnd;
      if (patch.providerStatus !== undefined)
        body.provider_status = patch.providerStatus;
      if (patch.workflowStage !== undefined)
        body.workflow_stage = patch.workflowStage;
      if (patch.sortKey !== undefined) body.sort_key = patch.sortKey;
      if (patch.assignees !== undefined) {
        // The full set (may be []) — the backend keeps `assignee` in step.
        body.assignees = (patch.assignees ?? []).map((p) => ({
          name: p.name,
          email: p.email,
          provider_user_id: p.providerUserId,
        }));
      } else if (patch.assignee !== undefined) {
        if (patch.assignee === null) body.clear_assignee = true;
        else
          body.assignee = {
            name: patch.assignee.name,
            email: patch.assignee.email,
            provider_user_id: patch.assignee.providerUserId,
          };
      }
      if (patch.isMine !== undefined) body.is_mine = patch.isMine;
      if (patch.important !== undefined) body.important = patch.important;
      if (patch.leveraged !== undefined) body.leveraged = patch.leveraged;
      if (patch.deepWork !== undefined) body.deep_work = patch.deepWork;
      if (patch.keptMine !== undefined) body.kept_mine = patch.keptMine;
      if (Object.keys(body).length) {
        // Swap in the server row (authoritative — e.g. a ClickUp back-sync may
        // normalize the stage) so the optimistic edit reconciles.
        sync(
          apiPatchItem(id, body).then((server) =>
            set((s) => ({
              items: s.items.map((i) => (i.id === id ? server : i)),
            })),
          ),
        );
      }
    }
  },

  applySchedule: (label, changes) => {
    if (!changes.length) return;
    flushPendingPurge(get().undoSnapshot, get().backend);
    // Snapshot BEFORE applying, so undo restores the pre-change grid exactly;
    // the per-item writes then ride the normal updateItem path (optimistic +
    // server sync + authoritative-row swap).
    set((s) => ({
      undoSnapshot: {
        items: s.items,
        projects: s.projects,
        processed: s.processedThisSession,
        selectedItemId: s.selectedItemId,
        label,
        scheduleRevertIds: changes.map((c) => c.id),
      },
    }));
    for (const c of changes) get().updateItem(c.id, c.patch);
  },

  reorderItem: (id, groupItems, toIndex, refile) => {
    const moving = get().items.find((i) => i.id === id);
    if (!moving) return;
    // `toIndex` is the gap index within `groupItems` (which may still include
    // the moved card, e.g. an intra-group drag). Translating that into an index
    // in the neighbour set is `@/lib/boardDrop.dropIndexFor` — shared with
    // /projects' board since WS-27ad, because the off-by-one it handles is
    // invisible in review and only bites on a downward intra-group drag.
    const others = groupItems.filter((i) => i.id !== id);
    const newKey = rankForDrop(others, dropIndexFor(groupItems, id, toIndex));
    // One patch carries the rank and any stage re-file, so a cross-column drag
    // that also repositions is a single write (and one optimistic update).
    const patch: ItemMetaPatch = { sortKey: newKey };
    if (refile?.workflowStage !== undefined)
      patch.workflowStage = refile.workflowStage;
    if (refile?.providerStatus !== undefined)
      patch.providerStatus = refile.providerStatus;
    get().updateItem(id, patch);
  },

  loadSubtasks: async (id) => {
    if (get().backend !== "live") return [];
    try {
      return await apiListSubtasks(id);
    } catch {
      return [];
    }
  },

  addSubtasks: async (id, titles) => {
    const clean = titles.map((t) => t.trim()).filter(Boolean);
    if (!clean.length) return [];
    if (get().backend !== "live") return [];
    const children = await apiAddSubtasks(id, clean);
    // Reflect the new count on the parent card without a full re-hydrate.
    set((s) => ({
      items: s.items.map((i) =>
        i.id === id ? { ...i, subtaskCount: children.length } : i,
      ),
    }));
    return children;
  },

  renameItem: (id, title) => {
    const t = title.trim();
    if (!t) return;
    set((s) => ({
      items: s.items.map((i) =>
        i.id === id ? { ...i, title: t, updatedAt: new Date().toISOString() } : i,
      ),
    }));
    if (get().backend === "live") sync(apiPatchItem(id, { title: t }));
  },

  undoLastChange: () => {
    const snap = get().undoSnapshot;
    if (!snap) return;
    const { items, projects, processed, selectedItemId, changedIds,
      deletedItems, softDeletedIds, archivedIds, archivedTo,
      scheduleRevertIds } = snap;
    set({
      items,
      projects,
      processedThisSession: processed,
      selectedItemId,
      undoSnapshot: null,
    });
    if (get().backend !== "live") return;
    if (softDeletedIds?.length) {
      // Lossless undo of a soft delete: the rows are only tombstoned, so just
      // clear the tombstone. Local state is already restored from the snapshot;
      // nothing was touched upstream (the ClickUp delete only happens on purge).
      sync(
        Promise.all(softDeletedIds.map((id) => apiRestoreItem(id).catch(() => {}))),
      );
    } else if (deletedItems?.length) {
      // Undo a HARD delete: the row is gone server-side, so re-create it (a
      // new id) and swap the restored local placeholder to the server row so
      // future edits target a real row. Local state is already restored.
      sync(
        Promise.all(
          deletedItems.map(async (d) => {
            try {
              const created = await apiCapture(d.title, d.notes ?? undefined);
              set((s) => ({
                items: s.items.map((i) => (i.id === d.id ? created : i)),
                selectedItemId:
                  s.selectedItemId === d.id ? created.id : s.selectedItemId,
              }));
            } catch {
              /* leave the local restore; a reload reconciles */
            }
          }),
        ),
      );
    } else if (archivedIds?.length) {
      // Flip the archive back the other way upstream (local state is already
      // restored from the snapshot).
      sync(apiBulkArchive(archivedIds, !archivedTo).catch(() => {}));
    } else if (scheduleRevertIds?.length) {
      // Revert the server rows' SCHEDULING to the snapshot values ("" clears a
      // block that the undone change had created). Local state is already
      // restored from the snapshot.
      const prev = new Map(items.map((i) => [i.id, i]));
      sync(
        Promise.all(
          scheduleRevertIds.map((id) => {
            const p = prev.get(id);
            return p
              ? apiPatchItem(id, {
                  scheduled_start: p.scheduledStart ?? "",
                  scheduled_end: p.scheduledEnd ?? "",
                  flexible: p.flexible ?? true,
                }).catch(() => {})
              : Promise.resolve();
          }),
        ),
      );
    } else if (changedIds?.length) {
      // Revert the server rows to their pre-change disposition (the local
      // state is already fully restored from the snapshot).
      const prev = new Map(items.map((i) => [i.id, i]));
      sync(
        Promise.all(
          changedIds.map((id) => {
            const p = prev.get(id);
            return p
              ? apiPatchItem(id, { disposition: p.disposition }).catch(() => {})
              : Promise.resolve();
          }),
        ),
      );
    }
  },

  dismissUndo: () => {
    // The undo window closed without an undo. Finalize any soft delete: purge
    // the rows and propagate the deletion to ClickUp for synced tasks. (Restore
    // is no longer offered once dismissed.)
    const snap = get().undoSnapshot;
    set({ undoSnapshot: null });
    if (snap?.softDeletedIds?.length && get().backend === "live") {
      sync(
        Promise.all(snap.softDeletedIds.map((id) => apiPurgeItem(id).catch(() => {}))),
      );
    }
  },

  hydrate: async () => {
    try {
      const [items, projects, accounts, orgPeople] = await Promise.all([
        fetchItems("all"),
        fetchProjects(),
        fetchAccounts(),
        fetchPeople().catch(() => [] as Person[]),
      ]);
      const providers: ConnectedProvider[] = [
        { id: "local", label: "Local", provider: "local", source: "LOCAL", statuses: [] },
        ...accounts.map(accountToProviderEntry),
      ];
      // People priority: the org-knowledge layer (roles/skills, §6.1) →
      // provider workspace members → bundled mocks.
      const members = accounts.flatMap((a) => a.members);
      const people = orgPeople.length
        ? orgPeople
        : members.length
          ? members
          : MOCK_PEOPLE;
      set({
        backend: "live",
        loading: false,
        items,
        projects,
        accounts,
        providerStatuses: providerStatusesFrom(accounts),
        providers,
        people,
      });
      // Settings load in parallel — defaults already render, so the panes are
      // usable before they arrive.
      const settings = await fetchTaskSettings().catch(() => get().settings);
      set({ settings });
      // ⚠️ The auto-sync-on-open fire was REMOVED 2026-08-25 (D52, WS-39 S1
      // repair round 1). It ran `syncNow()` whenever any `task_accounts` row
      // survived — which, after the retirement, is the ONLY state it could be
      // in — and `POST /tasks/sync` builds a provider first, so it could do
      // nothing but 400 and stamp `sync_status='error'`/`sync_error` on the
      // row. That is the same "control that cannot succeed" as the deleted
      // Connect/Sync buttons, except nobody pressed this one: it fired on every
      // app open and re-earned the error the scheduler guard exists to prevent.
      // `syncNow` itself is left in place for S3a to delete with the store.
    } catch {
      // Gateway absent/unreachable → demo mode on the bundled mocks (local
      // dev only). We seed the mocks HERE, not at init, so production (gateway
      // present) never shows them even for a frame.
      set({
        backend: "demo",
        loading: false,
        items: MOCK_ITEMS,
        projects: MOCK_PROJECTS,
        people: MOCK_PEOPLE,
      });
    }
  },

  refreshAccounts: async () => {
    if (get().backend !== "live") return;
    try {
      const accounts = await fetchAccounts();
      const providers: ConnectedProvider[] = [
        { id: "local", label: "Local", provider: "local", source: "LOCAL", statuses: [] },
        ...accounts.map(accountToProviderEntry),
      ];
      const members = accounts.flatMap((a) => a.members);
      const orgPeople = await fetchPeople().catch(() => [] as Person[]);
      const projects = await fetchProjects();
      set({
        accounts,
        providerStatuses: providerStatusesFrom(accounts),
        providers,
        projects,
        people: orgPeople.length
          ? orgPeople
          : members.length
            ? members
            : MOCK_PEOPLE,
      });
    } catch {
      /* keep current state */
    }
  },

  disconnectAccount: async (id) => {
    await apiDeleteAccount(id);
    await get().refreshAccounts();
    // Mirrored items cascade server-side; re-pull the item list too.
    try {
      set({ items: await fetchItems("all") });
    } catch {
      /* next hydrate reconciles */
    }
  },

  refreshAccountSchema: async (id) => {
    await apiRefreshSchema(id);
    await get().refreshAccounts();
  },

  settings: {
    chatModel: "tier-powerful",
    clarifyModel: "tier-balanced",
    atomizeModel: "tier-fast",
    emailCaptureModel: "tier-fast",
    captureDedup: true,
    autoSyncOnOpen: true,
    clarifyUseLlm: true,
    backgroundSync: true,
    mirrorDoneTasks: false,
    workflowStages: ["TODO", "IN PROCESS", "WAITING FOR", "DONE"],
    urgentWindowHours: 48,
    statusStageMap: {},
    dayStartHour: 7,
    dayEndHour: 22,
    dailyCapacityMins: 360,
    bufferMins: 0,
    energyWindows: [],
    timezone: "UTC",
    autoRollover: true,
    planningPrompt: "",
    maxFocusRunMins: 90,
    breakMins: 10,
    lunchStartHour: null,
    lunchEndHour: null,
    dayTemplates: [],
  },

  updateSettings: async (patch) => {
    // Optimistic: the modal reflects the change instantly; the server is the
    // source of truth on response (and on the next hydrate if the PUT fails).
    set((s) => ({ settings: { ...s.settings, ...patch } }));
    if (get().backend !== "live") return;
    try {
      set({ settings: await updateTaskSettings(patch) });
    } catch {
      /* next hydrate reconciles */
    }
  },

  settingsModalOpen: false,
  openSettings: () => set({ settingsModalOpen: true }),
  closeSettings: () => set({ settingsModalOpen: false }),

  syncing: false,

  syncNow: async (accountId) => {
    if (get().backend !== "live" || get().syncing) return;
    set({ syncing: true });
    try {
      await apiSyncTasks(accountId ? { accountId } : undefined);
      // Re-pull items + account sync status so the views fill immediately.
      const [items] = await Promise.all([
        fetchItems("all"),
        get().refreshAccounts(),
      ]);
      set({ items });
    } catch {
      /* account rows carry sync_error; next hydrate reconciles */
    } finally {
      set({ syncing: false });
    }
  },

  pushItem: async (id) => {
    const pushed = await apiPushItem(id);
    set((s) => ({ items: s.items.map((i) => (i.id === id ? pushed : i)) }));
  },
}));

// ── Derived selectors (pure; keep view logic in one place) ──────────────────

/** Items shown for a given view (+ optional context drill-down + source
 *  filter). ``source`` hides the connected-workspace mirror ("local") or shows
 *  only it ("synced"); "all" (default) shows both. */
export function itemsForView(
  items: GtdItem[],
  view: ViewKey,
  context: string | null,
  source: "all" | "local" | "synced" = "all",
): GtdItem[] {
  if (source === "local") items = items.filter((i) => i.source === "LOCAL");
  else if (source === "synced") items = items.filter((i) => i.source !== "LOCAL");
  // Archived tasks are hidden everywhere except the Archive view.
  if (view === "archive") return items.filter((i) => i.archivedAt);
  items = items.filter((i) => !i.archivedAt);
  switch (view) {
    case "inbox":
      return items.filter((i) => i.disposition === "INBOX");
    case "next":
      // "My Next Actions" = only tasks assigned to ME (isMine). This excludes
      // unassigned "team pool" tasks (synced as NEXT but is_mine=false) and, of
      // course, anything delegated to someone else (which is WAITING anyway).
      // Co-assigned tasks (me + others) keep is_mine=true, so they stay.
      // The "@no context" bucket (NO_CONTEXT) holds tasks with no @context yet —
      // e.g. synced ClickUp tasks that never went through Clarify.
      //
      // DONE tasks stay here too (until archived) so a card dropped on the board's
      // "Done" column doesn't vanish — it rests in that terminal column, from
      // which the user archives it. They were mine (isMine) when completed, so
      // they keep satisfying isMine; the board/grouped list files them into the
      // last stage (Done). The flat, ungrouped list still excludes DONE (that
      // filter lives in the caller via `includeDone`) so a plain list isn't
      // swamped by a completed pile.
      return items.filter(
        (i) =>
          ((i.disposition === "NEXT" && i.isMine) ||
            (i.disposition === "DONE" && i.isMine)) &&
          (!context
            ? true
            : context === NO_CONTEXT
              ? !i.context
              : i.context === context),
      );
    case "waiting":
      return items.filter((i) => i.disposition === "WAITING");
    case "someday":
      return items.filter((i) => i.disposition === "SOMEDAY");
    case "reference":
      return items.filter((i) => i.disposition === "REFERENCE");
    case "done":
      // Completed tasks live here until archived (they're filtered out of the
      // active board/lists). Most-recently-completed first.
      return items
        .filter((i) => i.disposition === "DONE")
        .sort((a, b) => (b.completedAt ?? "").localeCompare(a.completedAt ?? ""));
    case "priority":
      // The matrix map: every open, actionable task ASSIGNED TO ME on ClickUp
      // (isMine — my provider user id is among the task's assignees), whether
      // it's a NEXT action or a WAITING item I'm still on the hook for. Tasks
      // delegated to someone else (WAITING, is_mine=false) are NOT mine and drop
      // out. Excludes inbox (unclarified), done, reference, someday.
      return items.filter(
        (i) =>
          i.isMine &&
          (i.disposition === "NEXT" || i.disposition === "WAITING"),
      );
    case "engage":
      // "Right now": actionable work I can pick up (NEXT & mine). The Engage
      // surface filters this further by energy/time/context and ranks by the
      // matrix (see the Engage view component).
      return items.filter((i) => i.disposition === "NEXT" && i.isMine);
    case "calendar":
      return items
        .filter(isCalendarItem)
        .sort((a, b) => (a.dueAt ?? "").localeCompare(b.dueAt ?? ""));
    default:
      return [];
  }
}

/** Per-view counts for the sidebar badges. ``source`` mirrors itemsForView's
 *  source filter so the badges track the All / Mine / ClickUp toggle instead of
 *  always reporting the "All" totals. */
export function viewCounts(
  items: GtdItem[],
  source: "all" | "local" | "synced" = "all",
): Record<ViewKey, number> {
  if (source === "local") items = items.filter((i) => i.source === "LOCAL");
  else if (source === "synced") items = items.filter((i) => i.source !== "LOCAL");
  const c = {
    inbox: 0, next: 0, priority: 0, waiting: 0, calendar: 0, projects: 0,
    someday: 0, reference: 0, done: 0, engage: 0, archive: 0, horizons: 0,
  } as Record<ViewKey, number>;
  for (const i of items) {
    if (i.archivedAt) continue; // archived rows never count toward active views
    if (i.disposition === "INBOX" && !isTickled(i)) c.inbox++;
    else if (i.disposition === "NEXT" && i.isMine) c.next++; // My Next Actions only
    else if (i.disposition === "WAITING") c.waiting++;
    else if (i.disposition === "SOMEDAY") c.someday++;
    else if (i.disposition === "REFERENCE") c.reference++;
    else if (i.disposition === "DONE") c.done++;
    // Priority = every open actionable task ASSIGNED TO ME (isMine) — NEXT or
    // WAITING; engage = the do-able-now subset (NEXT mine). Independent of the
    // above. Must mirror itemsForView's priority/engage filters exactly.
    if (i.isMine && (i.disposition === "NEXT" || i.disposition === "WAITING"))
      c.priority++;
    if (i.disposition === "NEXT" && i.isMine) c.engage++;
    if (isCalendarItem(i)) c.calendar++;
  }
  return c;
}

/** Flatten every connected account's statuses into one ordered, de-duplicated
 *  list (case-insensitive), preserving each tool's own workflow order. This is
 *  the ClickUp half of the board/list status axis (unioned with the local
 *  workflow stages by statusColumns). */
export function providerStatusesFrom(accounts: TaskAccount[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const a of accounts) {
    for (const s of a.statuses ?? []) {
      const key = s.trim().toLowerCase();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push(s);
    }
  }
  return out;
}

/** Count of MY NEXT items per context (for the expandable @context sub-list).
 *  Mirrors the "My Next Actions" filter — only tasks assigned to me count, so
 *  the subfolder badges match what the view actually shows. */
export function contextCounts(items: GtdItem[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const i of items) {
    if (i.disposition !== "NEXT" || !i.isMine) continue;
    // Context-less "My Next Actions" (unprocessed synced tasks) fall into the
    // "@no context" bucket so they're visible and can be clarified/backfilled.
    const key = i.context || NO_CONTEXT;
    out[key] = (out[key] ?? 0) + 1;
  }
  return out;
}
