// Gateway client for My Tasks. Every task read and write goes through the
// Projects lens (`lens.ts`, `/api/projects/my/*`), the one task store (D53).
// `gatewayFetch` reaches the `/tasks` routes that survive S8: the AI doors,
// intake, settings, people, the agent planner and the day state.
// Mirrors the email app's lib/api.ts: snake_case backend ↔ camelCase UI types.
// The store hydrates from here when the gateway is reachable and silently
// falls back to the bundled mock data when it isn't (UI-first demo mode).

import { MyTask, MyTasksProject, Person, OrgPerson, OrgPersonWrite, ResumeIngestResult, Disposition, TaskAttachment } from "./types";
import type { ClarifyProposal, ClarifyDisposition, Confidence } from "./clarify";
import {
  lensAddSubtasks,
  lensArchiveItem,
  lensBulkArchive,
  lensBulkDispose,
  lensCapture,
  lensCaptureBatch,
  lensCreateArea,
  lensDelegateItem,
  lensDeleteArea,
  lensEstimateStats,
  lensFetchAreas,
  lensFetchItems,
  lensFetchMyRoot,
  lensFetchLed,
  lensFetchUntriaged,
  lensMyTaskLanes,
  lensFetchProjects,
  lensFileUnder,
  lensItemDetail,
  lensListSubtasks,
  lensMergeInto,
  lensMoveTask,
  lensOrganize,
  lensPlan,
  lensPatchItem,
  lensPurgeItem,
  lensRenameArea,
  lensRestoreItem,
  lensStageAttachment,
  lensTrashItem,
} from "./lens";
import type { LensArea, LensAreaRemoval, LensLane, LensLedProject, LensMoveRequest } from "./lens";
export type { LensArea, LensAreaRemoval } from "./lens";

async function gatewayFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/tasks${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(
      (body as { detail?: string; error?: string }).detail ||
        (body as { error?: string }).error ||
        `Gateway error ${res.status}`
    ) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

// ── Mappers ──────────────────────────────────────────────────────────────────

type Raw = Record<string, unknown>;

function asPerson(v: unknown): Person | undefined {
  if (!v || typeof v !== "object") return undefined;
  const p = v as Raw;
  if (!p.name && !p.email) return undefined;
  return {
    name: String(p.name ?? p.email ?? ""),
    email: p.email ? String(p.email) : undefined,
    providerUserId: p.provider_user_id ? String(p.provider_user_id) : undefined,
  };
}

/** Pull the proposed owner's workload flags (server-annotated on the clarify
 *  proposal, §5 Phase 2) off the raw suggested_assignee. Undefined when the
 *  server didn't annotate load (semantic/workload path off or no owner). */
function asAssigneeLoad(
  v: unknown
): { overloaded: boolean; openTaskCount: number; note?: string } | undefined {
  if (!v || typeof v !== "object") return undefined;
  const a = v as Raw;
  if (a.overloaded === undefined && a.open_task_count === undefined)
    return undefined;
  return {
    overloaded: Boolean(a.overloaded),
    openTaskCount: Number(a.open_task_count ?? 0),
    note: a.load_note ? String(a.load_note) : undefined,
  };
}

/**
 * A `pm_projects` NODE as the promote picker reads it. The title is `name`,
 * and the status is lowercase (`146_projects.sql`). Exported for its test.
 */
export function mapProject(raw: Raw): MyTasksProject {
  const status = String(raw.status ?? "active").toUpperCase();
  return {
    id: String(raw.id ?? ""),
    source: "LOCAL",
    outcome: String(raw.name ?? raw.outcome ?? ""),
    purpose: raw.description ? String(raw.description) : undefined,
    // `active` is the only node status a picker offers; anything else
    // (archived, closed) reads as DONE so the ACTIVE filter drops it.
    status: status === "ACTIVE" ? "ACTIVE" : "DONE",
    hasNextAction: false,
  };
}

// ── Calls ────────────────────────────────────────────────────────────────────

export async function fetchItems(view = "all"): Promise<MyTask[]> {
  return lensFetchItems(view);
}

// ── Rich provider detail (comments / attachments / subtasks) ────────────────

export interface TaskComment {
  id: string;
  author: string;
  text: string;
  createdAtMs?: number;
}
export interface TaskSubtask {
  providerTaskId: string;
  title: string;
  status?: string;
  statusType?: string;
  providerUrl?: string;
  assignees: Person[];
}
export interface ProviderTaskDetail {
  comments: TaskComment[];
  attachments: TaskAttachment[];
  subtasks: TaskSubtask[];
  error?: string;
}

/** One task's comments, attachments and subtasks. */
export async function apiItemDetail(id: string): Promise<ProviderTaskDetail> {
  // Composed from three Projects reads: timeline, attachments and children.
  return lensItemDetail(id);
}

/** Promote a task into a project: the My Tasks door to `move`. */
export async function apiMoveTask(
  taskId: string,
  req: LensMoveRequest,
): Promise<Raw> {
  return lensMoveTask(taskId, req);
}

export async function fetchProjects(): Promise<MyTasksProject[]> {
  // ⚠️ These are the COMPANY's projects, not a per-user tree. A member's own
  // structure is their Areas (migration 191), reached through `my/*`. This
  // list exists to choose a promote destination, and only a real project can
  // be one.
  const rows = await lensFetchProjects();
  return rows.map((r) => mapProject(r as Raw));
}

export async function fetchPeople(): Promise<Person[]> {
  const rows = await gatewayFetch<Raw[]>(`/people`);
  return rows
    .map((r) => ({
      name: String(r.name ?? ""),
      email: r.email ? String(r.email) : undefined,
      providerUserId: r.provider_user_id ? String(r.provider_user_id) : undefined,
    }))
    .filter((p) => p.name);
}

// ── Org / HR people (the People management view) ─────────────────────────────

function mapOrgPerson(r: Raw): OrgPerson {
  const num = (v: unknown) => (v == null ? undefined : Number(v));
  return {
    id: String(r.id ?? ""),
    name: String(r.name ?? ""),
    email: r.email ? String(r.email) : undefined,
    role: r.role ? String(r.role) : undefined,
    title: r.title ? String(r.title) : undefined,
    department: r.department ? String(r.department) : undefined,
    team: r.team ? String(r.team) : undefined,
    reportsTo: r.reports_to ? String(r.reports_to) : undefined,
    managerId: r.manager_id ? String(r.manager_id) : undefined,
    status: String(r.status ?? "active"),
    skills: Array.isArray(r.skills) ? (r.skills as unknown[]).map(String) : [],
    skillsSource:
      r.skills_source && typeof r.skills_source === "object"
        ? (r.skills_source as Record<string, string>)
        : {},
    domain: r.domain ? String(r.domain) : undefined,
    resumeSummary: r.resume_summary ? String(r.resume_summary) : undefined,
    yearsExperience: num(r.years_experience),
    capacityHoursPerWeek: num(r.capacity_hours_per_week),
    currentLoadHoursPerWeek: num(r.current_load_hours_per_week),
    availableHoursPerWeek: num(r.available_hours_per_week),
    providerUserId: r.provider_user_id ? String(r.provider_user_id) : undefined,
  };
}

function orgPersonToWire(b: OrgPersonWrite): Record<string, unknown> {
  const w: Record<string, unknown> = {};
  const set = (k: string, v: unknown) => {
    if (v !== undefined) w[k] = v;
  };
  set("name", b.name);
  set("email", b.email);
  set("role", b.role);
  set("title", b.title);
  set("department", b.department);
  set("team", b.team);
  set("reports_to", b.reportsTo);
  set("manager_id", b.managerId);
  set("status", b.status);
  set("skills", b.skills);
  set("domain", b.domain);
  set("resume_summary", b.resumeSummary);
  set("years_experience", b.yearsExperience);
  set("capacity_hours_per_week", b.capacityHoursPerWeek);
  set("current_load_hours_per_week", b.currentLoadHoursPerWeek);
  set("clickup_user_id", b.providerUserId);
  return w;
}

export async function fetchOrgPeople(
  opts: { q?: string; includeInactive?: boolean } = {}
): Promise<OrgPerson[]> {
  const sp = new URLSearchParams();
  if (opts.q?.trim()) sp.set("q", opts.q.trim());
  if (opts.includeInactive) sp.set("include_inactive", "true");
  const qs = sp.toString();
  const rows = await gatewayFetch<Raw[]>(`/people${qs ? `?${qs}` : ""}`);
  return rows.map(mapOrgPerson).filter((p) => p.name);
}

export async function createPerson(body: OrgPersonWrite): Promise<OrgPerson> {
  return mapOrgPerson(
    await gatewayFetch<Raw>(`/people`, {
      method: "POST",
      body: JSON.stringify(orgPersonToWire(body)),
    })
  );
}

export async function updatePerson(
  id: string,
  body: OrgPersonWrite
): Promise<OrgPerson> {
  return mapOrgPerson(
    await gatewayFetch<Raw>(`/people/${id}`, {
      method: "PATCH",
      body: JSON.stringify(orgPersonToWire(body)),
    })
  );
}

/** Upload a résumé (multipart) → parse → auto-merge skills; returns what changed.
 *  Not via gatewayFetch (that forces JSON) — a raw multipart POST. */
export async function uploadResume(
  id: string,
  file: File
): Promise<ResumeIngestResult> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`/api/tasks/people/${id}/resume`, {
    method: "POST",
    body: fd,
  });
  if (!res.ok) {
    const b = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(b.detail || `Résumé upload failed (${res.status})`);
  }
  const raw = (await res.json()) as Raw;
  return {
    resumeId: String(raw.resume_id ?? ""),
    addedSkills: Array.isArray(raw.added_skills)
      ? (raw.added_skills as unknown[]).map(String)
      : [],
    extracted: (raw.extracted ?? {}) as ResumeIngestResult["extracted"],
    person: mapOrgPerson((raw.person ?? {}) as Raw),
  };
}

/** Optional tickler / deadline captured alongside a quick capture. */
export interface CaptureDates {
  /** tickler: hide the item until this date, then resurface it in the inbox. */
  deferUntil?: string;
  /** a deadline (ISO); pairs with isHardDate so it shows on the Calendar. */
  dueAt?: string;
  isHardDate?: boolean;
}

export async function apiCapture(
  title: string,
  notes?: string,
  attachments?: TaskAttachment[],
  dates?: CaptureDates
): Promise<MyTask> {
  return lensCapture(title, notes, attachments, dates);
}

export async function apiCaptureBatch(titles: string[]): Promise<MyTask[]> {
  return lensCaptureBatch(titles);
}

export async function apiPatchItem(
  id: string,
  patch: {
    title?: string;
    notes?: string;
    /** `null` CLEARS my stated disposition, so the derived one shows again. */
    disposition?: Disposition | null;
    defer_until?: string;
    next_action?: string;
    context?: string;
    energy?: string;
    /** D77 — the task's ONE estimate (`pm_tasks.estimate_mins`); null clears. */
    time_estimate_mins?: number | null;
    /** D77 — the work's shared start date, "YYYY-MM-DD"; null clears. */
    start_date?: string | null;
    due_at?: string;
    scheduled_start?: string;
    scheduled_end?: string;
    flexible?: boolean;
    actual_start?: string;
    actual_end?: string;
    provider_status?: string;
    workflow_stage?: string;
    sort_key?: number;
    assignee?: { name: string; email?: string; provider_user_id?: string };
    clear_assignee?: boolean;
    /** the full owner set — [] clears everyone; takes precedence over assignee */
    assignees?: { name: string; email?: string; provider_user_id?: string }[];
    is_mine?: boolean;
    important?: boolean;
    leveraged?: boolean;
    deep_work?: boolean;
    kept_mine?: boolean;
    /** the promised-by date on the item's OPEN waiting-for record; "" clears
     *  it (no promise ⇒ the overdue line reads due_at live). */
    expected_by?: string;
  }
): Promise<MyTask> {
  return lensPatchItem(id, patch as Record<string, unknown>);
}

/** Items to render on the calendar grid for the window [fromIso, toIso):
 *  scheduled time-blocks + deadline items. See calendar_timeboxing.md. */
export interface PlanDayBlock {
  itemId: string;
  title: string;
  start: string;
  end: string;
  energy?: string;
  rationale?: string;
  /** true = this block was already on the calendar and is being moved;
   *  false = a new task pulled in from the unscheduled list. */
  previouslyScheduled?: boolean;
  /** true = an unfinished task carried forward from a PRIOR day. */
  carriedOver?: boolean;
}
export interface PlanDayUnplaced {
  itemId: string;
  title: string;
  reason: string;
}
export interface DayPlanResult {
  blocks: PlanDayBlock[];
  unplaced: PlanDayUnplaced[];
  /** blocks that WERE scheduled but no longer fit — cleared on apply, back to
   *  the unscheduled list. */
  evicted: PlanDayUnplaced[];
  notes?: string;
  usedMins: number;
  capacityMins: number;
  /** "ai" = LLM judged the selection/order (your prompt + note applied);
   *  "priority" = LLM unavailable, deterministic fallback (prompts ignored). */
  rankedBy: "ai" | "priority";
  /** when rankedBy === "priority", a short reason the AI ranking was skipped. */
  rankNote?: string;
}
export interface PlanDayRequest {
  day_start: string;
  day_end: string;
  energy_windows: { start: string; end: string; energy: string }[];
  capacity_mins: number;
  buffer_mins: number;
  energy_note?: string;
}

function mapDayPlan(r: Raw): DayPlanResult {
  const arr = (v: unknown): Raw[] => (Array.isArray(v) ? (v as Raw[]) : []);
  return {
    blocks: arr(r.blocks).map((b) => ({
      itemId: String(b.item_id ?? ""),
      title: String(b.title ?? ""),
      start: String(b.start ?? ""),
      end: String(b.end ?? ""),
      energy: b.energy ? String(b.energy) : undefined,
      rationale: b.rationale ? String(b.rationale) : undefined,
      previouslyScheduled: Boolean(b.previously_scheduled),
      carriedOver: Boolean(b.carried_over),
    })),
    unplaced: arr(r.unplaced).map((u) => ({
      itemId: String(u.item_id ?? ""),
      title: String(u.title ?? ""),
      reason: String(u.reason ?? ""),
    })),
    evicted: arr(r.evicted).map((u) => ({
      itemId: String(u.item_id ?? ""),
      title: String(u.title ?? ""),
      reason: String(u.reason ?? ""),
    })),
    notes: r.notes ? String(r.notes) : undefined,
    usedMins: Number(r.used_mins ?? 0),
    capacityMins: Number(r.capacity_mins ?? 0),
    rankedBy: r.ranked_by === "priority" ? "priority" : "ai",
    rankNote: r.rank_note ? String(r.rank_note) : undefined,
  };
}

/** Ask the AI planner for a timeboxed day (priority/energy/capacity/deadline
 *  aware). Returns a proposal — the caller applies accepted blocks via PATCH. */
export async function apiPlanDay(req: PlanDayRequest): Promise<DayPlanResult> {
  return mapDayPlan(await lensPlan("plan", req));
}

/** Roll incomplete PAST time-blocks forward into the target day's open slots
 *  (deadline-aware). Returns a proposal — the caller applies it. */
export async function apiRollover(req: PlanDayRequest): Promise<DayPlanResult> {
  return mapDayPlan(await lensPlan("rollover", req));
}

/** Re-timebox the REST of today: repack today's not-yet-done FLEXIBLE blocks
 *  from now, around fixed/done blocks. The "I fell behind — fix my day" op.
 *  Returns a proposal — the caller applies it. */
export async function apiReplan(req: PlanDayRequest): Promise<DayPlanResult> {
  return mapDayPlan(await lensPlan("replan", req));
}

/** The AGENT-facing day planner (server-side geometry — no client windows).
 *  Used by the chat tool cards' "Apply" button so a plan the assistant proposed
 *  in chat can be committed with one click, via the exact endpoint the agent
 *  itself would call with apply=true. */
export async function apiAgentPlanToday(
  kind: "plan-today" | "replan-today" | "rollover-today",
  energyNote?: string,
): Promise<{ blocks?: unknown[]; notes?: string } | undefined> {
  return gatewayFetch(`/calendar/${kind}`, {
    method: "POST",
    body: JSON.stringify(
      kind === "plan-today"
        ? { apply: true, energy_note: energyNote || null }
        : { apply: true },
    ),
  });
}

/** Learned-estimate accuracy over recent TIMED blocks (actual vs planned) — the
 *  end-of-day review's "you run X% over" signal. `overPct` > 0 = under-estimates. */
export interface EstimateStats {
  samples: number;
  ratio: number;
  overPct: number;
}
export async function apiEstimateStats(): Promise<EstimateStats> {
  const r = await lensEstimateStats();
  return {
    samples: Number(r.samples ?? 0),
    ratio: Number(r.ratio ?? 1),
    overPct: Number(r.over_pct ?? 0),
  };
}

// ── Per-day Focus-OS state (★ One Thing + tomorrow-seeds) ────────────────────
// Persisted server-side (mig 92) so the AI planner / chat agent / digest can
// see the user's committed priority and it syncs across devices. localStorage
// (focusPrefs.ts) stays the instant cache; these sync it to the source of truth.

export interface DayState {
  day: string;
  oneThingId: string | null;
  seedIds: string[];
}

export async function apiGetDayState(day: string): Promise<DayState> {
  const r = await gatewayFetch<Raw>(
    `/calendar/day-state?day=${encodeURIComponent(day)}`,
  );
  return {
    day: String(r.day ?? day),
    oneThingId: r.one_thing_id ? String(r.one_thing_id) : null,
    seedIds: Array.isArray(r.seed_ids) ? r.seed_ids.map(String) : [],
  };
}

/** Partial upsert — only the provided fields change. Pass oneThingId: "" to
 *  clear the One Thing. */
export async function apiSetDayState(
  day: string,
  patch: { oneThingId?: string | null; seedIds?: string[] },
): Promise<void> {
  const body: Raw = { day };
  if (patch.oneThingId !== undefined) body.one_thing_id = patch.oneThingId ?? "";
  if (patch.seedIds !== undefined) body.seed_ids = patch.seedIds;
  await gatewayFetch<Raw>(`/calendar/day-state`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/** Archive (hide from active views) or un-archive a task. */
export async function apiArchiveItem(
  id: string,
  archived: boolean,
): Promise<MyTask> {
  return lensArchiveItem(id, archived);
}

export async function apiBulkDispose(
  ids: string[],
  disposition: Disposition
): Promise<MyTask[]> {
  // DONE completes each task for the project; anything else is my overlay.
  return lensBulkDispose(ids, disposition);
}

/** Archive (or un-archive) many tasks at once — the bulk "Archive selected"
 *  action. Local overlay; never touches the connected tool. */
export async function apiBulkArchive(
  ids: string[],
  archived: boolean
): Promise<MyTask[]> {
  return lensBulkArchive(ids, archived);
}

export interface OrganizeBody {
  kind: string;
  next_action?: string;
  outcome?: string;
  context?: string;
  energy?: string;
  time_estimate_mins?: number;
  due_at?: string;
  account_id?: string;
  project_id?: string;
  status?: string;
  assignee?: { name: string; email?: string; provider_user_id?: string };
  subtasks?: string[];
  /** S6g — the destination's required custom fields, for a promote. */
  custom_fields?: Record<string, unknown>;
  /** S6g — the owners after a promote. Absent leaves them alone. */
  assignees?: string[];
}

export async function apiOrganize(id: string, body: OrganizeBody): Promise<MyTask> {
  // One request, one transaction, on the gateway (S6a done-when 4).
  return lensOrganize(id, body);
}

/** The child subtasks of a task (local rows), in manual order. */
export async function apiListSubtasks(id: string): Promise<MyTask[]> {
  return lensListSubtasks(id);
}

/** Add child subtasks to an existing task; returns the full ordered child list. */
export async function apiAddSubtasks(
  id: string,
  titles: string[],
): Promise<MyTask[]> {
  return lensAddSubtasks(id, titles);
}

/** Soft-delete: the task vanishes from every view but stays intact server-side
 *  for a lossless undo. Call apiPurgeItem after the undo window to finalize. */
export async function apiDeleteItem(id: string): Promise<void> {
  return lensTrashItem(id);
}

/** Undo a soft delete — returns the restored task, exactly as it was. */
export async function apiRestoreItem(id: string): Promise<MyTask> {
  return lensRestoreItem(id);
}

/** Finalize a soft delete. Idempotent — a row that's already gone is a no-op. */
export async function apiPurgeItem(id: string): Promise<void> {
  return lensPurgeItem(id);
}

// ── Local hierarchy (Spaces → Folders → Projects) ───────────────────────────

export interface LocalSpace {
  id: string;
  name: string;
}
export interface LocalFolder {
  id: string;
  spaceId: string;
  name: string;
}
export interface LocalProjectNode {
  id: string;
  outcome: string;
  spaceId?: string;
  folderId?: string;
  hasNextAction: boolean;
  status: string;
}
export interface LocalHierarchy {
  spaces: LocalSpace[];
  folders: LocalFolder[];
  projects: LocalProjectNode[];
}

// ── Areas (S6b) — what the local tree became ────────────────────────────────
//
// A member's own structure is their Areas: children of the personal root,
// FLAT (D65). The four functions below are the doors, and the group-D
// functions after them fold onto the same doors, so a caller that still
// speaks Space/Folder/Project gets Areas back without being rewritten.

/** My Areas. */
export async function fetchAreas(): Promise<LensArea[]> {
  return lensFetchAreas();
}

/**
 * My personal root's id and name (S6b repair), or null for a member who has
 * never captured. One door, so "which project is my root" has one answer.
 */
export async function fetchMyRoot(): Promise<{ id: string; name: string } | null> {
  return lensFetchMyRoot();
}

// ── Continuity with Projects (S6e) ──────────────────────────────────────────
//
// Two reads the Projects app answers for My Tasks: the tasks a board assigned
// to me, and the projects I lead.

/** Tasks assigned to me on a board that I have not looked at yet. */
export async function fetchUntriaged(): Promise<MyTask[]> {
  return lensFetchUntriaged();
}

/** The projects I lead, with their open counts and my own open tasks. */
export async function fetchLedProjects(): Promise<LensLedProject[]> {
  return lensFetchLed();
}

/** One task's lanes, through the door the assignee arm can pass. */
export async function fetchMyTaskLanes(id: string): Promise<LensLane[]> {
  return lensMyTaskLanes(id);
}

/** Mint an Area. */
export async function apiCreateArea(name: string): Promise<LensArea> {
  return lensCreateArea(name);
}

export async function apiRenameArea(id: string, name: string): Promise<LensArea> {
  return lensRenameArea(id, name);
}

/** Remove an Area. The answer says whether it was deleted or archived. */
export async function apiDeleteArea(id: string): Promise<LensAreaRemoval> {
  return lensDeleteArea(id);
}

/** An Area in the shape the retiring tree's callers still read. */
function areaAsLocalProject(a: LensArea): LocalProjectNode {
  return {
    id: a.id,
    outcome: a.name,
    hasNextAction: a.openTasks > 0,
    status: a.archived ? "DONE" : "ACTIVE",
  };
}

/**
 * The local tree is my Areas: one flat level, no spaces and no folders, each
 * Area a project node.
 */
export async function fetchLocalHierarchy(): Promise<LocalHierarchy> {
  const areas = await lensFetchAreas();
  return {
    spaces: [],
    folders: [],
    projects: areas.filter((a) => !a.archived).map(areaAsLocalProject),
  };
}

// A space or a folder cannot exist (D65), so S8 PR 1 deleted the two doors
// that created them, with their store actions. An Area is the one level.

export async function apiCreateLocalProject(req: {
  outcome: string;
  spaceId?: string;
  folderId?: string;
  purpose?: string;
}): Promise<LocalProjectNode> {
  // A "local project" IS an Area. The placement and the purpose have no home
  // there (an Area has a name and nothing else), and they are dropped
  // knowingly: nothing can create a space or folder to name.
  return areaAsLocalProject(await lensCreateArea(req.outcome));
}

/** Stage one attachment → descriptor for the capture payload. */
export async function apiUploadAttachment(file: File): Promise<TaskAttachment> {
  // An attachment belongs to a TASK, and at capture time there is none yet:
  // the descriptor holds the file, and `lensCapture` uploads it once the
  // task exists (lens.ts header, S6a).
  return lensStageAttachment(file);
}

/** A peak/trough window in the day: the AI planner puts high-energy work in
 *  'high' windows, admin in 'low' ones. */
export interface EnergyWindow {
  start_hour: number;
  end_hour: number;
  energy: "low" | "medium" | "high";
}

export interface TaskSettings {
  chatModel: string;
  clarifyModel: string;
  atomizeModel: string;
  emailCaptureModel: string;
  captureDedup: boolean;
  autoSyncOnOpen: boolean;
  clarifyUseLlm: boolean;
  backgroundSync: boolean;
  mirrorDoneTasks: boolean;
  /** hours from now within which a due task is URGENT (drives the matrix's ⏰
   *  axis). Overdue is always urgent. */
  urgentWindowHours: number;
  // Calendar/timeboxing prefs (spec §5): the plannable day window, a soft daily
  // focus budget, inter-block buffer, and the user's energy windows.
  dayStartHour: number;
  dayEndHour: number;
  dailyCapacityMins: number;
  bufferMins: number;
  energyWindows: EnergyWindow[];
  /** IANA timezone (for the nightly auto roll-over's local-day boundary). */
  timezone: string;
  /** auto-roll incomplete past blocks into today, once per local day. */
  autoRollover: boolean;
  // Planning prefs (migration 93) — "how should the AI organize my day".
  /** standing instruction the LLM planner obeys every run ("" → server default). */
  planningPrompt: string;
  /** insert a break after this many continuous focus minutes (0 = off). */
  maxFocusRunMins: number;
  /** the length of that inserted break. */
  breakMins: number;
  /** optional protected lunch window (local hours); null = no protected lunch. */
  lunchStartHour: number | null;
  lunchEndHour: number | null;
  /** recurring windows: block (protected) or focus (themed). Flexible ideal-week. */
  dayTemplates: DayTemplate[];
}

/** A recurring calendar window (migration 94). Snake-keyed to match the wire
 *  shape (like EnergyWindow) so it needs no per-field mapping. */
export interface DayTemplate {
  /** weekday numbers 0=Sun … 6=Sat; empty = every day. */
  days: number[];
  start_hour: number;
  end_hour: number;
  /** block = protected (no tasks); focus = preferred for a kind of work. */
  kind: "block" | "focus";
  label: string;
  /** for focus windows: the kind of work ("deep", "calls", "meetings", …). */
  theme: string;
}

function mapSettings(r: Raw): TaskSettings {
  return {
    chatModel: String(r.chat_model ?? "tier-powerful"),
    clarifyModel: String(r.clarify_model ?? "tier-balanced"),
    atomizeModel: String(r.atomize_model ?? "tier-fast"),
    emailCaptureModel: String(r.email_capture_model ?? "tier-fast"),
    captureDedup: r.capture_dedup !== false,
    autoSyncOnOpen: r.auto_sync_on_open !== false,
    clarifyUseLlm: r.clarify_use_llm !== false,
    backgroundSync: r.background_sync !== false,
    mirrorDoneTasks: r.mirror_done_tasks === true,
    urgentWindowHours: Number(r.urgent_window_hours ?? 48) || 48,
    dayStartHour: Number(r.day_start_hour ?? 7) || 7,
    dayEndHour: Number(r.day_end_hour ?? 22) || 22,
    dailyCapacityMins: Number(r.daily_capacity_mins ?? 360) || 360,
    bufferMins: Number(r.buffer_mins ?? 0) || 0,
    energyWindows: Array.isArray(r.energy_windows)
      ? (r.energy_windows as EnergyWindow[])
      : [],
    timezone: String(r.timezone ?? "UTC"),
    autoRollover: r.auto_rollover !== false,
    planningPrompt: String(r.planning_prompt ?? ""),
    maxFocusRunMins: Number(r.max_focus_run_mins ?? 90),
    breakMins: Number(r.break_mins ?? 10),
    lunchStartHour:
      r.lunch_start_hour == null ? null : Number(r.lunch_start_hour),
    lunchEndHour: r.lunch_end_hour == null ? null : Number(r.lunch_end_hour),
    dayTemplates: Array.isArray(r.day_templates)
      ? (r.day_templates as DayTemplate[])
      : [],
  };
}

export async function fetchTaskSettings(): Promise<TaskSettings> {
  return mapSettings(await gatewayFetch<Raw>(`/settings`));
}

/** Partial update — only the provided fields change. */
export async function updateTaskSettings(
  patch: Partial<TaskSettings>
): Promise<TaskSettings> {
  const body: Raw = {};
  if (patch.chatModel !== undefined) body.chat_model = patch.chatModel;
  if (patch.clarifyModel !== undefined) body.clarify_model = patch.clarifyModel;
  if (patch.atomizeModel !== undefined) body.atomize_model = patch.atomizeModel;
  if (patch.emailCaptureModel !== undefined)
    body.email_capture_model = patch.emailCaptureModel;
  if (patch.captureDedup !== undefined) body.capture_dedup = patch.captureDedup;
  if (patch.autoSyncOnOpen !== undefined)
    body.auto_sync_on_open = patch.autoSyncOnOpen;
  if (patch.clarifyUseLlm !== undefined)
    body.clarify_use_llm = patch.clarifyUseLlm;
  if (patch.backgroundSync !== undefined)
    body.background_sync = patch.backgroundSync;
  if (patch.mirrorDoneTasks !== undefined)
    body.mirror_done_tasks = patch.mirrorDoneTasks;
  if (patch.urgentWindowHours !== undefined)
    body.urgent_window_hours = patch.urgentWindowHours;
  if (patch.dayStartHour !== undefined) body.day_start_hour = patch.dayStartHour;
  if (patch.dayEndHour !== undefined) body.day_end_hour = patch.dayEndHour;
  if (patch.dailyCapacityMins !== undefined)
    body.daily_capacity_mins = patch.dailyCapacityMins;
  if (patch.bufferMins !== undefined) body.buffer_mins = patch.bufferMins;
  if (patch.energyWindows !== undefined)
    body.energy_windows = patch.energyWindows;
  if (patch.timezone !== undefined) body.timezone = patch.timezone;
  if (patch.autoRollover !== undefined) body.auto_rollover = patch.autoRollover;
  if (patch.planningPrompt !== undefined)
    body.planning_prompt = patch.planningPrompt;
  if (patch.maxFocusRunMins !== undefined)
    body.max_focus_run_mins = patch.maxFocusRunMins;
  if (patch.breakMins !== undefined) body.break_mins = patch.breakMins;
  if (patch.lunchStartHour !== undefined)
    body.lunch_start_hour = patch.lunchStartHour;
  if (patch.lunchEndHour !== undefined)
    body.lunch_end_hour = patch.lunchEndHour;
  if (patch.dayTemplates !== undefined)
    body.day_templates = patch.dayTemplates;
  return mapSettings(
    await gatewayFetch<Raw>(`/settings`, {
      method: "PUT",
      body: JSON.stringify(body),
    })
  );
}

export interface AtomizedItem {
  title: string;
  verdict: "new" | "similar" | "duplicate";
  matchId?: string;
  matchTitle?: string;
  matchDisposition?: string;
  matchSource?: string;
  score: number;
}

/** Split a mind-dump / paragraph into atomic captures, each checked against
 *  the user's open items for duplicates (LLM-backed server-side, with a
 *  deterministic fallback — the caller shouldn't care which ran). */
export async function apiAtomize(
  text: string,
  opts?: { dedup?: boolean; excludeIds?: string[] }
): Promise<{ items: AtomizedItem[]; usedLlm: boolean }> {
  const res = await gatewayFetch<Raw>(`/ai/atomize`, {
    method: "POST",
    body: JSON.stringify({
      text,
      dedup: opts?.dedup ?? true,
      exclude_ids: opts?.excludeIds ?? [],
    }),
  });
  const items = ((res.items as Raw[]) ?? []).map((r) => ({
    title: String(r.title ?? ""),
    verdict: (["new", "similar", "duplicate"].includes(String(r.verdict))
      ? String(r.verdict)
      : "new") as AtomizedItem["verdict"],
    matchId: r.match_id ? String(r.match_id) : undefined,
    matchTitle: r.match_title ? String(r.match_title) : undefined,
    matchDisposition: r.match_disposition ? String(r.match_disposition) : undefined,
    matchSource: r.match_source ? String(r.match_source) : undefined,
    score: Number(r.score ?? 0),
  }));
  return { items, usedLlm: Boolean(res.used_llm) };
}

export async function apiClarifyPropose(
  id: string,
  /** true → re-clarify an already-processed task. */
  reclarify = false,
  /** Optional freeform guidance the user typed while clarifying — steers the
   *  proposed title / project / steps for this pass. */
  note?: string,
): Promise<ClarifyProposal> {
  const q = reclarify ? "?reclarify=true" : "";
  const r = await gatewayFetch<Raw>(`/items/${id}/clarify${q}`, {
    method: "POST",
    body: JSON.stringify({ note: note?.trim() || null }),
  });
  const accountId = r.account_id ? String(r.account_id) : undefined;
  return {
    actionable: Boolean(r.actionable),
    disposition: String(r.disposition ?? "NEXT") as ClarifyDisposition,
    nextAction: String(r.next_action ?? ""),
    outcome: r.outcome ? String(r.outcome) : undefined,
    context: r.context ? String(r.context) : undefined,
    energy: (r.energy ?? undefined) as ClarifyProposal["energy"],
    timeEstimateMins: r.time_estimate_mins
      ? Number(r.time_estimate_mins)
      : undefined,
    isTwoMinute: Boolean(r.is_two_minute),
    suggestedAssignee: asPerson(r.suggested_assignee),
    assigneeLoad: asAssigneeLoad(r.suggested_assignee),
    target: accountId
      ? { source: "SYNCED", accountId }
      : { source: "LOCAL", provider: "local" },
    projectId: r.project_id ? String(r.project_id) : undefined,
    projectInferred: Boolean(r.project_inferred),
    targetSpaceId: r.target_space_id ? String(r.target_space_id) : undefined,
    targetFolderId: r.target_folder_id ? String(r.target_folder_id) : undefined,
    confidence: String(r.confidence ?? "medium") as Confidence,
    rationale: String(r.rationale ?? ""),
    status: r.status ? String(r.status) : undefined,
    complexity: (["single", "subtasks", "project"].includes(String(r.complexity))
      ? String(r.complexity)
      : undefined) as ClarifyProposal["complexity"],
    suggestedSubtasks: Array.isArray(r.subtasks)
      ? (r.subtasks as unknown[]).map(String).filter(Boolean)
      : undefined,
    /** true when the server locked a SYNCED task's destination (reclarify). */
    lockedDestination: Boolean(r.locked_destination),
    isVague: Boolean(r.is_vague),
    suggestedTitle: r.suggested_title ? String(r.suggested_title) : undefined,
    dueDate: r.due_date ? String(r.due_date) : undefined,
    important: Boolean(r.important),
    leveraged: Boolean(r.leveraged),
    deepWork: Boolean(r.deep_work),
    weightReason: r.leveraged
      ? "Looks high-leverage — a potential 100x outcome."
      : r.important
        ? "Reads as important — something stalls if it slips."
        : "No strong importance/leverage signal.",
    duplicate: r.duplicate
      ? (() => {
          const d = r.duplicate as Raw;
          return {
            itemId: String(d.item_id ?? ""),
            title: String(d.title ?? ""),
            providerUrl: d.provider_url ? String(d.provider_url) : undefined,
            providerStatus: d.provider_status
              ? String(d.provider_status)
              : undefined,
            projectName: d.project_name ? String(d.project_name) : undefined,
            verdict: d.verdict === "duplicate" ? "duplicate" as const : "similar" as const,
            score: Number(d.score ?? 0),
          };
        })()
      : undefined,
    parentSuggestion: r.parent_suggestion
      ? {
          itemId: String((r.parent_suggestion as Raw).item_id ?? ""),
          title: String((r.parent_suggestion as Raw).title ?? ""),
        }
      : undefined,
  };
}

/** Fold an inbox capture into an existing synced task (dedup "add to existing")
 *  instead of creating a duplicate. Returns the enriched target task. */
export async function apiMergeInto(id: string, targetId: string): Promise<MyTask> {
  return lensMergeInto(id, targetId);
}

/** File an inbox capture as a SUB-STEP of an existing task (clarify "this is a
 *  step of X"). Returns the parent task (now with the new child). */
export async function apiFileUnder(id: string, parentId: string): Promise<MyTask> {
  return lensFileUnder(id, parentId);
}

// ── Project planning (§7, Phase 3): a brief → phases → tasks → subtasks ───────

export interface PlanTask {
  title: string;
  description?: string;
  assigneeName?: string;
  assignee?: Person;
  assigneeOverloaded?: boolean;
  effortHours?: number;
  priority?: string;
  dueOffsetDays?: number;
  context?: string;
  energy?: string;
  subtasks: string[];
}
export interface PlanPhase {
  name: string;
  tasks: PlanTask[];
}
export interface ProjectPlan {
  name: string;
  description?: string;
  phases: PlanPhase[];
  notes?: string;
}
export interface ApplyPlanResult {
  projectId: string;
  providerRef?: string;
  tasksCreated: number;
  subtasksCreated: number;
  target: string;
}

function mapPlanTask(raw: Raw): PlanTask {
  return {
    title: String(raw.title ?? ""),
    description: raw.description ? String(raw.description) : undefined,
    assigneeName: raw.assignee_name ? String(raw.assignee_name) : undefined,
    assignee: asPerson(raw.assignee),
    assigneeOverloaded: Boolean(raw.assignee_overloaded),
    effortHours:
      raw.effort_hours != null ? Number(raw.effort_hours) : undefined,
    priority: raw.priority ? String(raw.priority) : undefined,
    dueOffsetDays:
      raw.due_offset_days != null ? Number(raw.due_offset_days) : undefined,
    context: raw.context ? String(raw.context) : undefined,
    energy: raw.energy ? String(raw.energy) : undefined,
    subtasks: Array.isArray(raw.subtasks)
      ? (raw.subtasks as unknown[]).map(String).filter(Boolean)
      : [],
  };
}

function mapPlan(r: Raw): ProjectPlan {
  return {
    name: String(r.name ?? ""),
    description: r.description ? String(r.description) : undefined,
    phases: ((r.phases as Raw[]) ?? []).map((ph) => ({
      name: String(ph.name ?? "Phase"),
      tasks: ((ph.tasks as Raw[]) ?? []).map(mapPlanTask),
    })),
    notes: r.notes ? String(r.notes) : undefined,
  };
}

/** Draft a full project plan from a brief (proposal only — no writes). */
export async function apiPlanProject(
  name: string,
  description?: string,
  target: "local" = "local",
): Promise<ProjectPlan> {
  return mapPlan(
    await gatewayFetch<Raw>(`/plan`, {
      method: "POST",
      body: JSON.stringify({ name, description: description || null, target }),
    }),
  );
}

/** Materialise a (possibly edited) plan in the one store. */
export async function apiApplyPlan(
  plan: ProjectPlan,
  opts: {
    target: "local";
    accountId?: string;
    spaceId?: string;
    folderId?: string;
  },
): Promise<ApplyPlanResult> {
  const wirePlan = {
    name: plan.name,
    description: plan.description ?? null,
    notes: plan.notes ?? null,
    phases: plan.phases.map((ph) => ({
      name: ph.name,
      tasks: ph.tasks.map((t) => ({
        title: t.title,
        description: t.description ?? null,
        assignee_name: t.assigneeName ?? null,
        assignee: t.assignee
          ? {
              name: t.assignee.name,
              email: t.assignee.email ?? null,
              provider_user_id: t.assignee.providerUserId ?? null,
            }
          : null,
        effort_hours: t.effortHours ?? null,
        priority: t.priority ?? null,
        due_offset_days: t.dueOffsetDays ?? null,
        context: t.context ?? null,
        energy: t.energy ?? null,
        subtasks: t.subtasks,
      })),
    })),
  };
  const r = await gatewayFetch<Raw>(`/plan/apply`, {
    method: "POST",
    body: JSON.stringify({
      plan: wirePlan,
      target: opts.target,
      account_id: opts.accountId ?? null,
      space_id: opts.spaceId ?? null,
      folder_id: opts.folderId ?? null,
    }),
  });
  return {
    projectId: String(r.project_id ?? ""),
    providerRef: r.provider_ref ? String(r.provider_ref) : undefined,
    tasksCreated: Number(r.tasks_created ?? 0),
    subtasksCreated: Number(r.subtasks_created ?? 0),
    target: String(r.target ?? opts.target),
  };
}

/** Rephrase a task's title more clearly (the always-available "Improve title"
 *  affordance) and flag whether it's vague. `title` overrides the item's
 *  stored title when the user is editing it live in the card. */
export async function apiSuggestTitle(
  id: string,
  title?: string,
): Promise<{ isVague: boolean; suggestedTitle?: string }> {
  const q = title ? `?title=${encodeURIComponent(title)}` : "";
  const r = await gatewayFetch<Raw>(`/items/${id}/suggest-title${q}`, {
    method: "POST",
  });
  return {
    isVague: Boolean(r.is_vague),
    suggestedTitle: r.suggested_title ? String(r.suggested_title) : undefined,
  };
}

/** The fields an enrich pass proposed for a task's MISSING slots. Any subset. */
export interface EnrichFields {
  context?: string;
  energy?: "low" | "medium" | "high";
  timeEstimateMins?: number;
  dueAt?: string;
  assignee?: Person;
}

/** Ask the assistant to fill a task's missing details (context/energy/time/
 *  due/assignee). Proposes only — the caller applies via apiPatchItem. */
export async function apiEnrichItem(id: string): Promise<EnrichFields> {
  const r = await gatewayFetch<Raw>(`/items/${id}/enrich`, { method: "POST" });
  const f = (r.fields ?? {}) as Raw;
  return {
    context: f.context ? String(f.context) : undefined,
    energy: (["low", "medium", "high"].includes(String(f.energy))
      ? String(f.energy)
      : undefined) as EnrichFields["energy"],
    timeEstimateMins: f.time_estimate_mins
      ? Number(f.time_estimate_mins)
      : undefined,
    dueAt: f.due_at ? String(f.due_at) : undefined,
    assignee: asPerson(f.assignee),
  };
}

/** Auto-assign @context to actionable tasks that have none. Writes directly;
 *  returns the count set. */
export async function apiBackfillContext(): Promise<{
  scanned: number;
  updated: number;
}> {
  const r = await gatewayFetch<Raw>(`/ai/backfill-context`, { method: "POST" });
  return { scanned: Number(r.scanned ?? 0), updated: Number(r.updated ?? 0) };
}

/**
 * Hand a task to a teammate: they own it, it moves to MY Waiting-For, and the
 * server stamps the since-when.
 */
export async function apiDelegateItem(
  id: string,
  body: {
    assignee: { name: string; email?: string };
    next_action?: string;
    due_at?: string;
    expected_by?: string;
  },
): Promise<MyTask> {
  return lensDelegateItem(id, body);
}
