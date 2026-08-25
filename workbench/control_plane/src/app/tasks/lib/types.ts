// GTD Task Manager — canonical client types.
//
// These mirror the canonical Postgres model in
// `project-docs/specs/task_manager_app.md` §4 (gtd_items / gtd_projects /
// gtd_contexts), trimmed to what the UI needs. The app is built UI-first
// against mock data (see mockData.ts); when the gateway `/tasks` API lands,
// these types stay and only the data source swaps.

import type { SyncState } from "./syncState";

/** Where a task/project lives and who is the source of truth. */
export type Source = "LOCAL" | "SYNCED";

/** Which connected backend a SYNCED item mirrors (LOCAL items have none). */
export type ProviderKind = "clickup" | "asana" | "jira" | "linear" | "local";

/** The GTD disposition — the bucket an item lands in after Clarify.
 *  (No CALENDAR bucket: the Calendar is a VIEW over date-specific actions.) */
export type Disposition =
  | "INBOX"
  | "NEXT"
  | "WAITING"
  | "SOMEDAY"
  | "PROJECT"
  | "REFERENCE"
  | "DONE"
  | "TRASH";

/** Energy required — one of the four GTD engage criteria. */
export type Energy = "low" | "medium" | "high";

/** A GTD context (the `@` list): grouped by what you need to act. */
export interface GtdContext {
  /** e.g. "@computer" */
  name: string;
  /** lucide-react icon name */
  icon: string;
}

export interface Person {
  name: string;
  email?: string;
  /** a stable tailwind-ish accent for the avatar chip, e.g. "primary" */
  accent?: string;
  /** the person's id in the connected PM tool (for real assignment) */
  providerUserId?: string;
}

/** The full HR record behind the People view — roles, manager, skills (org chart
 *  + résumé-extracted), capacity, and the ClickUp assignment id. Editable in-app
 *  (the app is the source of truth); mirrors the gateway's OrgPersonModel. */
export interface OrgPerson {
  id: string;
  name: string;
  email?: string;
  role?: string;
  title?: string;
  department?: string;
  team?: string;
  reportsTo?: string;
  managerId?: string;
  status: string;
  skills: string[];
  /** per-skill provenance: {skill: "orgchart"|"resume"|"manual"} */
  skillsSource: Record<string, string>;
  domain?: string;
  resumeSummary?: string;
  yearsExperience?: number;
  capacityHoursPerWeek?: number;
  currentLoadHoursPerWeek?: number;
  availableHoursPerWeek?: number;
  /** ClickUp user id — the real assignment target. */
  providerUserId?: string;
}

/** Create/update payload for an OrgPerson (camelCase; api.ts maps to snake). */
export type OrgPersonWrite = Partial<Omit<OrgPerson, "id" | "skillsSource" | "availableHoursPerWeek">>;

/** Result of ingesting a résumé: what skills it added + the parsed profile. */
export interface ResumeIngestResult {
  resumeId: string;
  addedSkills: string[];
  extracted: {
    skills?: string[];
    experience_summary?: string | null;
    years_experience?: number | null;
    domain?: string | null;
  };
  person: OrgPerson;
}

/** A GTD project — a first-class outcome needing >1 action (§5.1). */
export interface GtdProject {
  id: string;
  source: Source;
  provider?: ProviderKind;
  /** which connected workspace account a SYNCED project mirrors */
  accountId?: string;
  /** native project/list id in the tool (ClickUp list id) — the accordion
   *  picker selects by this */
  providerRef?: string;
  /** LOCAL tree placement (Space→Folder→Project). NULL on SYNCED (their tree
   *  is the provider's) or on an ungrouped local project. */
  spaceId?: string;
  folderId?: string;
  /** the desired outcome / "wild success" statement (the title) */
  outcome: string;
  /** natural-planning: why this matters */
  purpose?: string;
  status: "ACTIVE" | "SOMEDAY" | "DONE" | "DROPPED";
  /** the cardinal GTD health check — does it have a defined next action? */
  hasNextAction: boolean;
  /** link up to an Area of Focus (Horizon H2) */
  areaId?: string;
}

/** A GTD item — an inbox capture or a clarified action. */
export interface GtdItem {
  id: string;
  source: Source;
  provider?: ProviderKind;
  /** which connected workspace account a SYNCED item targets/mirrors */
  accountId?: string;
  /** deep link to the task in the connected PM tool (once pushed) */
  providerUrl?: string;
  title: string;
  notes?: string;

  // GTD overlay
  disposition: Disposition;
  /** the clarified physical next action (set once it leaves the inbox) */
  nextAction?: string;
  /** "@computer" | "@calls" | … (matches a GtdContext.name) */
  context?: string;
  energy?: Energy;
  timeEstimateMins?: number;
  isTwoMinute?: boolean;
  /** Prioritization matrix inputs. `urgent` is NOT stored — derive it from
   *  dueAt via isUrgent(); the 8-cell label comes from priorityCell(). */
  important?: boolean;
  leveraged?: boolean;
  /** needs an unbroken FLOW state (deep/creative/builder work) — the planner
   *  protects a long peak-energy block; Focus Mode defaults to a longer timer */
  deepWork?: boolean;
  /** the user dismissed the delegate/schedule suggestion ("this one's mine") */
  keptMine?: boolean;
  projectId?: string;

  // people / delegation
  isMine: boolean;
  /** who I'm waiting on (WAITING disposition) */
  waitingOn?: Person;
  /** ISO — when it left my hands (the "since-when" of §1's who/what/since-when) */
  delegatedAt?: string;
  /** ISO — the date it was promised BY. Past it ⇒ overdue (spec §6). Distinct
   *  from dueAt: my deadline and the date I asked THEM for are two facts. */
  expectedBy?: string;
  /** ISO — when a follow-up nudge last went out. Unset until the nudge path
   *  ships (owner-gated); reading it is what stops a double-chase. */
  lastNudgedAt?: string;
  /** primary/display assignee (= assignees[0]); kept for single-owner readers */
  assignee?: Person;
  /** the full owner set — a task can have several assignees (e.g. in ClickUp) */
  assignees?: Person[];
  /** the item's stage/status in the connected PM tool (e.g. "Backlog", "To-do") */
  providerStatus?: string;
  /** the task's stage on the local Kanban board (configured in settings) */
  workflowStage?: string;
  /** manual (drag) rank within a group/column; unset → created-at ordering */
  sortKey?: number;
  /** set → this item is a subtask of another gtd_item (its parent). */
  parentItemId?: string;
  /** number of child subtasks (roll-up badge on the card/detail). */
  subtaskCount?: number;
  /** when set, the task is archived (hidden from active views) */
  archivedAt?: string;
  /** sync lifecycle: 'local' (ours) · 'pending' (staged for the member's own
   *  push) · 'awaiting_approval' (pushed, but the Action Broker QUEUED the
   *  outward write for a human approver — nothing exists in the tool yet) ·
   *  'synced' (written back). Lets you clarify now and finish/push to
   *  ClickUp/Jira later. The three questions the UI asks of this value live in
   *  `lib/syncState.ts`, which owns the type — never compare to a literal. */
  syncState?: SyncState;

  // hard landscape
  /** ISO date string */
  dueAt?: string;
  /** true → must happen on dueAt; surfaces in the Calendar view */
  isHardDate?: boolean;

  // Timeboxing (calendar_timeboxing.md §3): the block when the task is actually
  // scheduled to be done — distinct from the dueAt deadline. Unset = unscheduled.
  /** ISO datetime — start of the scheduled time block */
  scheduledStart?: string;
  /** ISO datetime — end of the block (defaults to start + estimate) */
  scheduledEnd?: string;
  /** false = a FIXED block (meeting) the auto-mover (roll-over / replan) leaves
   *  put; true/undefined = flexible, may be moved. See calendar_ux_review §5.5 */
  flexible?: boolean;
  /** when the block was ACTUALLY worked (focus timer + completion) — vs the
   *  scheduled_* plan. Powers planned-vs-actual + learned estimates (§4). */
  actualStart?: string;
  actualEnd?: string;

  createdAt: string;
  /** Context attachments captured with the item (photo/file/link). */
  attachments?: TaskAttachment[];
  /** Source linkage when the capture came from another app (email, etc.). */
  origin?: {
    kind: string;
    accountId?: string;
    emailId?: string;
    subject?: string;
    fromName?: string;
    fromEmail?: string;
  };
  updatedAt: string;
  /** set when disposition becomes DONE (e.g. the 2-minute rule) */
  completedAt?: string;
  /** set when the item leaves the inbox (clarified) */
  clarifiedAt?: string;
  /** GTD tickler — hidden from the active inbox until this date, then resurfaces */
  deferUntil?: string;
}

/** Where a clarified item should be stored (dual-source model, §5.1). */
export interface Target {
  source: Source;
  /** which connected PM tool for a SYNCED target; 'local' for LOCAL */
  provider?: ProviderKind;
  /** the specific workspace account (live mode: several workspaces of the
   *  same provider can be connected side by side) */
  accountId?: string;
}

/** The left-rail views.
 *
 * **Personal only.** `projects` and `people` were removed 2026-08-06 (owner
 * decision): this app manages the tasks that are *yours*. The company's
 * departments, projects and team tasks live in `/projects`, and the directory
 * lives in `/people` — one surface each, rather than a second half-copy of both
 * behind a task manager.
 *
 * `GtdProject` itself survives: a task still belongs to a project and the cards
 * still name it. What went is the *browsing* surface, which was the ClickUp
 * Space → Folder → List hierarchy — precisely what `/projects` now owns.
 */
export type ViewKey =
  | "inbox"
  | "next"
  | "priority"
  | "waiting"
  /**
   * ⚠️ **No longer selectable from the Tasks sidebar** — D54 (2026-08-24, board
   * WS-39 S2) moved the calendar to its own app at `/calendar`.
   *
   * The member survives on purpose: `ViewKey` is the store's **filter**
   * vocabulary, not only the sidebar's destination list, and two rules keyed on
   * it are still real — `itemsForView("calendar")` is the canonical
   * "what is on the calendar" selector, and `viewQuickAdd("calendar")` returns
   * null because the calendar offers a per-DAY capture box instead of a
   * per-view one.
   *
   * 📌 **Finding for S3a, not a change to make here** (CLAUDE.md §5):
   * `CalendarView` hand-filters `s.items` rather than calling
   * `itemsForView("calendar")`, so the canonical selector currently has no
   * caller. Reconcile the two when S3a re-points this store at `pm_tasks`;
   * doing it now would be a behaviour change inside a move.
   *
   * Not to be confused with the GTD **disposition** `kind: "calendar"`
   * (a date-specific action — "Scheduled"), which is a different vocabulary in
   * the same file and is untouched.
   */
  | "calendar"
  | "someday"
  | "reference"
  | "done"
  | "engage"
  | "archive"
  | "horizons";

/** A context reference kept with a capture. Files/images are served from the
 *  gateway attachment store; links are references only. */
export interface TaskAttachment {
  kind: "file" | "image" | "link";
  name: string;
  url: string;
  attachmentId?: string;
  mime?: string;
  size?: number;
}

/** ClickUp-shaped navigation node for the project picker accordion. */
export interface WorkspaceHierarchySpace {
  id: string;
  name: string;
  lists: { id: string; name: string }[];
  folders: { id: string; name: string; lists: { id: string; name: string }[] }[];
}
