// Gateway client for the /tasks API (proxied via /api/tasks/[...path]).
// Mirrors the email app's lib/api.ts: snake_case backend ↔ camelCase UI types.
// The store hydrates from here when the gateway is reachable and silently
// falls back to the bundled mock data when it isn't (UI-first demo mode).

import { GtdItem, GtdProject, Person, OrgPerson, OrgPersonWrite, ResumeIngestResult, Source, ProviderKind, Disposition, TaskAttachment, WorkspaceHierarchySpace } from "./types";
import type { ClarifyProposal, ClarifyDisposition, Confidence } from "./clarify";
import type { ConnectedProvider } from "./mockData";

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

/** A raw person array → Person[]; drops blanks. */
function personList(v: unknown): Person[] {
  return Array.isArray(v) ? (v.map(asPerson).filter(Boolean) as Person[]) : [];
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

function mapItem(raw: Raw): GtdItem {
  return {
    id: String(raw.id ?? ""),
    source: (raw.source === "SYNCED" ? "SYNCED" : "LOCAL") as Source,
    provider: (raw.provider ?? undefined) as ProviderKind | undefined,
    accountId: raw.account_id ? String(raw.account_id) : undefined,
    title: String(raw.title ?? ""),
    notes: raw.notes ? String(raw.notes) : undefined,
    disposition: String(raw.disposition ?? "INBOX") as Disposition,
    nextAction: raw.next_action ? String(raw.next_action) : undefined,
    context: raw.context ? String(raw.context) : undefined,
    energy: (raw.energy ?? undefined) as GtdItem["energy"],
    timeEstimateMins: raw.time_estimate_mins
      ? Number(raw.time_estimate_mins)
      : undefined,
    isTwoMinute: Boolean(raw.is_two_minute),
    important: Boolean(raw.important),
    leveraged: Boolean(raw.leveraged),
    deepWork: Boolean(raw.deep_work),
    keptMine: Boolean(raw.kept_mine),
    projectId: raw.project_id ? String(raw.project_id) : undefined,
    isMine: Boolean(raw.is_mine ?? true),
    waitingOn: asPerson(raw.waiting_on),
    delegatedAt: raw.delegated_at ? String(raw.delegated_at) : undefined,
    expectedBy: raw.expected_by ? String(raw.expected_by) : undefined,
    lastNudgedAt: raw.last_nudged_at ? String(raw.last_nudged_at) : undefined,
    assignee: asPerson(raw.assignee),
    // Full owner set; fall back to the single assignee (mock rows / not-yet-
    // migrated data) so there's always at least the primary owner.
    assignees: (() => {
      const list = personList(raw.assignees);
      if (list.length) return list;
      const one = asPerson(raw.assignee);
      return one ? [one] : [];
    })(),
    providerStatus: raw.provider_status ? String(raw.provider_status) : undefined,
    workflowStage: raw.workflow_stage ? String(raw.workflow_stage) : undefined,
    sortKey: raw.sort_key == null ? undefined : Number(raw.sort_key),
    parentItemId: raw.parent_item_id ? String(raw.parent_item_id) : undefined,
    subtaskCount: raw.subtask_count == null ? 0 : Number(raw.subtask_count),
    archivedAt: raw.archived_at ? String(raw.archived_at) : undefined,
    providerUrl: raw.provider_url ? String(raw.provider_url) : undefined,
    syncState: (raw.sync_state ?? "local") as GtdItem["syncState"],
    dueAt: raw.due_at ? String(raw.due_at) : undefined,
    isHardDate: Boolean(raw.is_hard_date),
    scheduledStart: raw.scheduled_start ? String(raw.scheduled_start) : undefined,
    scheduledEnd: raw.scheduled_end ? String(raw.scheduled_end) : undefined,
    // Defaults to flexible (movable) — matches the column default (mig 79).
    flexible: raw.flexible == null ? true : Boolean(raw.flexible),
    actualStart: raw.actual_start ? String(raw.actual_start) : undefined,
    actualEnd: raw.actual_end ? String(raw.actual_end) : undefined,
    createdAt: String(raw.created_at ?? ""),
    attachments: Array.isArray(raw.attachments)
      ? (raw.attachments as TaskAttachment[])
      : undefined,
    origin: raw.origin && typeof raw.origin === "object"
      ? {
          kind: String((raw.origin as Raw).kind ?? ""),
          accountId: (raw.origin as Raw).account_id ? String((raw.origin as Raw).account_id) : undefined,
          emailId: (raw.origin as Raw).email_id ? String((raw.origin as Raw).email_id) : undefined,
          subject: (raw.origin as Raw).subject ? String((raw.origin as Raw).subject) : undefined,
          fromName: (raw.origin as Raw).from_name ? String((raw.origin as Raw).from_name) : undefined,
          fromEmail: (raw.origin as Raw).from_email ? String((raw.origin as Raw).from_email) : undefined,
        }
      : undefined,
    updatedAt: String(raw.updated_at ?? ""),
    completedAt: raw.completed_at ? String(raw.completed_at) : undefined,
    clarifiedAt: raw.clarified_at ? String(raw.clarified_at) : undefined,
    deferUntil: raw.defer_until ? String(raw.defer_until) : undefined,
  };
}

function mapProject(raw: Raw): GtdProject {
  return {
    id: String(raw.id ?? ""),
    source: (raw.source === "SYNCED" ? "SYNCED" : "LOCAL") as Source,
    provider: (raw.provider ?? undefined) as ProviderKind | undefined,
    accountId: raw.account_id ? String(raw.account_id) : undefined,
    providerRef: raw.provider_ref ? String(raw.provider_ref) : undefined,
    spaceId: raw.space_id ? String(raw.space_id) : undefined,
    folderId: raw.folder_id ? String(raw.folder_id) : undefined,
    outcome: String(raw.outcome ?? ""),
    purpose: raw.purpose ? String(raw.purpose) : undefined,
    status: String(raw.status ?? "ACTIVE") as GtdProject["status"],
    hasNextAction: Boolean(raw.has_next_action),
  };
}

export interface TaskAccount {
  id: string;
  provider: string;
  hierarchy: WorkspaceHierarchySpace[];
  workspaceId: string;
  label: string;
  syncEnabled: boolean;
  syncStatus: string;
  syncError?: string;
  lastSyncedAt?: string;
  statuses: string[];
  members: Person[];
  projectCount: number;
}

function mapAccount(raw: Raw): TaskAccount {
  return {
    hierarchy: Array.isArray(raw.hierarchy) ? (raw.hierarchy as WorkspaceHierarchySpace[]) : [],
    id: String(raw.id ?? ""),
    provider: String(raw.provider ?? ""),
    workspaceId: String(raw.workspace_id ?? ""),
    label: String(raw.label ?? ""),
    syncEnabled: Boolean(raw.sync_enabled ?? true),
    syncStatus: String(raw.sync_status ?? "idle"),
    syncError: raw.sync_error ? String(raw.sync_error) : undefined,
    lastSyncedAt: raw.last_synced_at ? String(raw.last_synced_at) : undefined,
    statuses: Array.isArray(raw.statuses) ? raw.statuses.map(String) : [],
    members: Array.isArray(raw.members)
      ? (raw.members.map(asPerson).filter(Boolean) as Person[])
      : [],
    projectCount: Number(raw.project_count ?? 0),
  };
}

/** Live accounts → the destination entries the Clarify UI renders. */
export function accountToProviderEntry(a: TaskAccount): ConnectedProvider {
  return {
    id: a.id,
    label: a.label || a.provider,
    provider: a.provider as ProviderKind,
    source: "SYNCED",
    statuses: a.statuses,
  };
}

// ── Calls ────────────────────────────────────────────────────────────────────

export async function fetchItems(
  view = "all",
  source: "" | "local" | "synced" = "",
): Promise<GtdItem[]> {
  const qs = source ? `?view=${view}&source=${source}` : `?view=${view}`;
  const rows = await gatewayFetch<Raw[]>(`/items${qs}`);
  return rows.map(mapItem);
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

/** Pull the connected tool's comments/attachments/subtasks for one task.
 *  Returns empty sections for a LOCAL / not-yet-pushed item. */
export async function apiItemDetail(id: string): Promise<ProviderTaskDetail> {
  const r = await gatewayFetch<Raw>(`/items/${id}/detail`);
  const asPersonList = (v: unknown): Person[] =>
    Array.isArray(v)
      ? (v.map(asPerson).filter(Boolean) as Person[])
      : [];
  return {
    comments: (Array.isArray(r.comments) ? r.comments : []).map((c) => {
      const raw = c as Raw;
      return {
        id: String(raw.id ?? ""),
        author: String(raw.author ?? "Someone"),
        text: String(raw.text ?? ""),
        createdAtMs: raw.created_at_ms ? Number(raw.created_at_ms) : undefined,
      };
    }),
    attachments: (Array.isArray(r.attachments) ? r.attachments : []).map((a) => {
      const raw = a as Raw;
      return {
        kind: "link" as const,
        name: String(raw.name ?? "attachment"),
        url: String(raw.url ?? ""),
        mime: raw.mime ? String(raw.mime) : undefined,
        size: raw.size ? Number(raw.size) : undefined,
      };
    }),
    subtasks: (Array.isArray(r.subtasks) ? r.subtasks : []).map((s) => {
      const raw = s as Raw;
      return {
        providerTaskId: String(raw.provider_task_id ?? ""),
        title: String(raw.title ?? "Untitled"),
        status: raw.status ? String(raw.status) : undefined,
        statusType: raw.status_type ? String(raw.status_type) : undefined,
        providerUrl: raw.provider_url ? String(raw.provider_url) : undefined,
        assignees: asPersonList(raw.assignees),
      };
    }),
    error: r.error ? String(r.error) : undefined,
  };
}

export async function fetchProjects(): Promise<GtdProject[]> {
  const rows = await gatewayFetch<Raw[]>(`/projects`);
  return rows.map(mapProject);
}

export async function fetchAccounts(): Promise<TaskAccount[]> {
  const rows = await gatewayFetch<Raw[]>(`/accounts`);
  return rows.map(mapAccount);
}

/** The org's people (roles/skills/capacity — §6.1), mapped to picker Persons. */
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
): Promise<GtdItem> {
  return mapItem(
    await gatewayFetch<Raw>(`/items`, {
      method: "POST",
      body: JSON.stringify({
        title,
        notes: notes ?? null,
        attachments:
          attachments && attachments.length > 0
            ? attachments.map((a) => ({
                kind: a.kind,
                name: a.name,
                url: a.url,
                attachment_id: a.attachmentId ?? null,
                mime: a.mime ?? null,
                size: a.size ?? null,
              }))
            : null,
        defer_until: dates?.deferUntil ?? null,
        due_at: dates?.dueAt ?? null,
        is_hard_date: dates?.isHardDate ?? false,
      }),
    })
  );
}

export async function apiCaptureBatch(titles: string[]): Promise<GtdItem[]> {
  const rows = await gatewayFetch<Raw[]>(`/items/batch`, {
    method: "POST",
    body: JSON.stringify({ titles }),
  });
  return rows.map(mapItem);
}

export async function apiPatchItem(
  id: string,
  patch: {
    title?: string;
    notes?: string;
    disposition?: Disposition;
    defer_until?: string;
    next_action?: string;
    context?: string;
    energy?: string;
    time_estimate_mins?: number;
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
): Promise<GtdItem> {
  return mapItem(
    await gatewayFetch<Raw>(`/items/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    })
  );
}

/** The ordered ClickUp statuses of a synced task's OWN list — the stage-picker
 *  options for the detail panel, so a task shows just its project's pipeline,
 *  not the whole-workspace union. Empty for a LOCAL / not-yet-pushed task. */
export async function apiItemStageOptions(id: string): Promise<string[]> {
  const r = await gatewayFetch<Raw>(`/items/${id}/stage-options`);
  return Array.isArray(r.statuses)
    ? (r.statuses as unknown[]).map(String)
    : [];
}

/** Items to render on the calendar grid for the window [fromIso, toIso):
 *  scheduled time-blocks + deadline items. See calendar_timeboxing.md. */
export async function apiCalendarRange(
  fromIso: string,
  toIso: string,
): Promise<GtdItem[]> {
  const qs = `?from=${encodeURIComponent(fromIso)}&to=${encodeURIComponent(toIso)}`;
  const rows = await gatewayFetch<Raw[]>(`/calendar${qs}`);
  return rows.map(mapItem);
}

// ── AI day-planner (Plan my day) ─────────────────────────────────────────────
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
  return mapDayPlan(
    await gatewayFetch<Raw>(`/calendar/plan`, {
      method: "POST",
      body: JSON.stringify(req),
    }),
  );
}

/** Roll incomplete PAST time-blocks forward into the target day's open slots
 *  (deadline-aware). Returns a proposal — the caller applies it. */
export async function apiRollover(req: PlanDayRequest): Promise<DayPlanResult> {
  return mapDayPlan(
    await gatewayFetch<Raw>(`/calendar/rollover`, {
      method: "POST",
      body: JSON.stringify(req),
    }),
  );
}

/** Re-timebox the REST of today: repack today's not-yet-done FLEXIBLE blocks
 *  from now, around fixed/done blocks. The "I fell behind — fix my day" op.
 *  Returns a proposal — the caller applies it. */
export async function apiReplan(req: PlanDayRequest): Promise<DayPlanResult> {
  return mapDayPlan(
    await gatewayFetch<Raw>(`/calendar/replan`, {
      method: "POST",
      body: JSON.stringify(req),
    }),
  );
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
  const r = await gatewayFetch<Raw>(`/calendar/estimate-stats`);
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
): Promise<GtdItem> {
  return mapItem(
    await gatewayFetch<Raw>(`/items/${id}/archive`, {
      method: "POST",
      body: JSON.stringify({ archived }),
    })
  );
}

export async function apiBulkDispose(
  ids: string[],
  disposition: Disposition
): Promise<GtdItem[]> {
  const rows = await gatewayFetch<Raw[]>(`/items/bulk`, {
    method: "POST",
    body: JSON.stringify({ ids, disposition }),
  });
  return rows.map(mapItem);
}

/** Archive (or un-archive) many tasks at once — the bulk "Archive selected"
 *  action. Local overlay; never touches the connected tool. */
export async function apiBulkArchive(
  ids: string[],
  archived: boolean
): Promise<GtdItem[]> {
  const rows = await gatewayFetch<Raw[]>(`/items/bulk-archive`, {
    method: "POST",
    body: JSON.stringify({ ids, archived }),
  });
  return rows.map(mapItem);
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
}

export async function apiOrganize(id: string, body: OrganizeBody): Promise<GtdItem> {
  return mapItem(
    await gatewayFetch<Raw>(`/items/${id}/organize`, {
      method: "POST",
      body: JSON.stringify(body),
    })
  );
}

/** The child subtasks of a task (local rows), in manual order. */
export async function apiListSubtasks(id: string): Promise<GtdItem[]> {
  const rows = await gatewayFetch<Raw[]>(`/items/${id}/subtasks`);
  return rows.map(mapItem);
}

/** Add child subtasks to an existing task; returns the full ordered child list. */
export async function apiAddSubtasks(
  id: string,
  titles: string[],
): Promise<GtdItem[]> {
  const rows = await gatewayFetch<Raw[]>(`/items/${id}/subtasks`, {
    method: "POST",
    body: JSON.stringify({ titles }),
  });
  return rows.map(mapItem);
}

export async function apiPushItem(id: string): Promise<GtdItem> {
  return mapItem(await gatewayFetch<Raw>(`/items/${id}/push`, { method: "POST" }));
}

/** Soft-delete: the task vanishes from every view but stays intact server-side
 *  for a lossless undo. Call apiPurgeItem after the undo window to finalize
 *  (and propagate the deletion to ClickUp for synced tasks). */
export async function apiDeleteItem(id: string): Promise<void> {
  await gatewayFetch<void>(`/items/${id}`, { method: "DELETE" });
}

/** Undo a soft delete — returns the restored task, exactly as it was. */
export async function apiRestoreItem(id: string): Promise<GtdItem> {
  return mapItem(
    await gatewayFetch<Raw>(`/items/${id}/restore`, { method: "POST" })
  );
}

/** Finalize a soft delete: remove the row and (for a synced task) propagate the
 *  deletion to ClickUp. Idempotent — a row that's already gone is a no-op. */
export async function apiPurgeItem(id: string): Promise<void> {
  await gatewayFetch<void>(`/items/${id}/purge`, { method: "POST" });
}

// ⚠️ `apiListWorkspaces` (POST /providers/{provider}/workspaces) and
// `apiConnectWorkspace` (POST /accounts) were deleted 2026-08-25 with their one
// caller, `WorkspacesModal` (D52, WS-39 S1 repair round 1). Both endpoints
// build a provider before doing anything, and the registry is empty — so each
// was a request whose only possible answer was 400 "Unknown provider". The
// gateway routes stay until S3a retires the GTD store that owns them.

export async function apiDeleteAccount(id: string): Promise<void> {
  await gatewayFetch<void>(`/accounts/${id}`, { method: "DELETE" });
}

export async function apiRefreshSchema(id: string): Promise<TaskAccount> {
  return mapAccount(
    await gatewayFetch<Raw>(`/accounts/${id}/schema/refresh`, { method: "POST" })
  );
}

/** LIVE workspace-member pull — the delegate picker's freshness call.
 *  People removed in the tool disappear from the returned account. */
export async function apiRefreshMembers(id: string): Promise<TaskAccount> {
  return mapAccount(
    await gatewayFetch<Raw>(`/accounts/${id}/members/refresh`, { method: "POST" })
  );
}

/** Create a NEW project (ClickUp list) under a space/folder — an explicit
 *  user-approved provider write from the picker's "create project" action. */
export async function apiCreateAccountProject(
  accountId: string,
  req: { name: string; spaceId: string; folderId?: string }
): Promise<{ projectId: string; providerRef: string; name: string }> {
  const r = await gatewayFetch<Raw>(`/accounts/${accountId}/projects`, {
    method: "POST",
    body: JSON.stringify({
      name: req.name,
      space_id: req.spaceId,
      folder_id: req.folderId ?? null,
    }),
  });
  return {
    projectId: String(r.project_id ?? ""),
    providerRef: String(r.provider_ref ?? ""),
    name: String(r.name ?? req.name),
  };
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

/** The LOCAL Space→Folder→Project tree (SYNCED projects live on their account
 *  hierarchy, not here). */
export async function fetchLocalHierarchy(): Promise<LocalHierarchy> {
  const r = await gatewayFetch<Raw>(`/hierarchy`);
  return {
    spaces: ((r.spaces as Raw[]) ?? []).map((s) => ({
      id: String(s.id ?? ""),
      name: String(s.name ?? ""),
    })),
    folders: ((r.folders as Raw[]) ?? []).map((f) => ({
      id: String(f.id ?? ""),
      spaceId: String(f.space_id ?? ""),
      name: String(f.name ?? ""),
    })),
    projects: ((r.projects as Raw[]) ?? []).map((p) => ({
      id: String(p.id ?? ""),
      outcome: String(p.outcome ?? ""),
      spaceId: p.space_id ? String(p.space_id) : undefined,
      folderId: p.folder_id ? String(p.folder_id) : undefined,
      hasNextAction: Boolean(p.has_next_action),
      status: String(p.status ?? "ACTIVE"),
    })),
  };
}

/** Create a NEW provider folder (ClickUp: space → folder → list) under a
 *  space — an explicit user-approved provider write from the picker's
 *  "new folder" action. */
export async function apiCreateAccountFolder(
  accountId: string,
  req: { name: string; spaceId: string }
): Promise<{ folderId: string; spaceId: string; name: string }> {
  const r = await gatewayFetch<Raw>(`/accounts/${accountId}/folders`, {
    method: "POST",
    body: JSON.stringify({ name: req.name, space_id: req.spaceId }),
  });
  return {
    folderId: String(r.folder_id ?? ""),
    spaceId: String(r.space_id ?? req.spaceId),
    name: String(r.name ?? req.name),
  };
}

export async function apiCreateSpace(name: string): Promise<LocalSpace> {
  const r = await gatewayFetch<Raw>(`/spaces`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  return { id: String(r.id ?? ""), name: String(r.name ?? name) };
}

export async function apiCreateFolder(
  spaceId: string,
  name: string,
): Promise<LocalFolder> {
  const r = await gatewayFetch<Raw>(`/folders`, {
    method: "POST",
    body: JSON.stringify({ space_id: spaceId, name }),
  });
  return {
    id: String(r.id ?? ""),
    spaceId: String(r.space_id ?? spaceId),
    name: String(r.name ?? name),
  };
}

export async function apiCreateLocalProject(req: {
  outcome: string;
  spaceId?: string;
  folderId?: string;
  purpose?: string;
}): Promise<LocalProjectNode> {
  const r = await gatewayFetch<Raw>(`/local-projects`, {
    method: "POST",
    body: JSON.stringify({
      outcome: req.outcome,
      space_id: req.spaceId ?? null,
      folder_id: req.folderId ?? null,
      purpose: req.purpose ?? null,
    }),
  });
  return {
    id: String(r.id ?? ""),
    outcome: String(r.outcome ?? req.outcome),
    spaceId: r.space_id ? String(r.space_id) : undefined,
    folderId: r.folder_id ? String(r.folder_id) : undefined,
    hasNextAction: Boolean(r.has_next_action),
    status: String(r.status ?? "ACTIVE"),
  };
}

/** Upload one attachment (multipart through the proxy) → descriptor for the
 *  capture payload. */
export async function apiUploadAttachment(file: File): Promise<TaskAttachment> {
  const fd = new FormData();
  fd.append("file", file, file.name);
  const res = await fetch(`/api/tasks/attachments`, { method: "POST", body: fd });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { detail?: string }).detail || `Upload failed (${res.status})`
    );
  }
  const r = (await res.json()) as Raw;
  return {
    kind: (r.kind === "image" ? "image" : "file") as TaskAttachment["kind"],
    name: String(r.name ?? file.name),
    url: String(r.url ?? ""),
    attachmentId: r.attachment_id ? String(r.attachment_id) : undefined,
    mime: r.mime ? String(r.mime) : undefined,
    size: r.size != null ? Number(r.size) : undefined,
  };
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
  workflowStages: string[];
  /** hours from now within which a due task is URGENT (drives the matrix's ⏰
   *  axis). Overdue is always urgent. */
  urgentWindowHours: number;
  /** ClickUp status → Next-Actions stage. {normalizedStatus: stage}. Governs how
   *  a synced task groups on the board and (reversed) which upstream status a
   *  drag writes back. */
  statusStageMap: Record<string, string>;
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
    workflowStages: Array.isArray(r.workflow_stages)
      ? (r.workflow_stages as unknown[]).map(String).filter(Boolean)
      : ["TODO", "IN PROCESS", "WAITING FOR", "DONE"],
    urgentWindowHours: Number(r.urgent_window_hours ?? 48) || 48,
    statusStageMap:
      r.status_stage_map && typeof r.status_stage_map === "object"
        ? Object.fromEntries(
            Object.entries(r.status_stage_map as Record<string, unknown>).map(
              ([k, v]) => [k, String(v)],
            ),
          )
        : {},
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
  if (patch.workflowStages !== undefined)
    body.workflow_stages = patch.workflowStages;
  if (patch.urgentWindowHours !== undefined)
    body.urgent_window_hours = patch.urgentWindowHours;
  if (patch.statusStageMap !== undefined)
    body.status_stage_map = patch.statusStageMap;
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

/** One unique upstream status paired with the stage it maps to (auto-guessed
 *  when the user hasn't set it yet). */
export interface StatusCatalogEntry {
  status: string;
  stage: string;
  mapped: boolean;
}

export interface StatusCatalog {
  stages: string[];
  entries: StatusCatalogEntry[];
  unmapped: number;
}

/** The unique ClickUp statuses across the user's connected projects + their
 *  (mapped or auto-guessed) Next-Actions stage — powers the mapping settings. */
export async function fetchStatusCatalog(): Promise<StatusCatalog> {
  const r = await gatewayFetch<Raw>(`/status-catalog`);
  return {
    stages: Array.isArray(r.stages)
      ? (r.stages as unknown[]).map(String)
      : [],
    entries: Array.isArray(r.entries)
      ? (r.entries as Raw[]).map((e) => ({
          status: String(e.status ?? ""),
          stage: String(e.stage ?? ""),
          mapped: Boolean(e.mapped),
        }))
      : [],
    unmapped: Number(r.unmapped ?? 0) || 0,
  };
}

export interface SyncResult {
  accountId: string;
  label: string;
  pulled: number;
  created: number;
  updated: number;
  completed: number;
  skipped: number;
  error?: string;
}

/** Pull existing provider tasks into the GTD mirror (one account, or all). */
export async function apiSyncTasks(opts?: {
  accountId?: string;
  full?: boolean;
}): Promise<SyncResult[]> {
  const res = await gatewayFetch<Raw[]>(`/sync`, {
    method: "POST",
    body: JSON.stringify({
      account_id: opts?.accountId ?? null,
      full: opts?.full ?? false,
    }),
  });
  return (res ?? []).map((r) => ({
    accountId: String(r.account_id ?? ""),
    label: String(r.label ?? ""),
    pulled: Number(r.pulled ?? 0),
    created: Number(r.created ?? 0),
    updated: Number(r.updated ?? 0),
    completed: Number(r.completed ?? 0),
    skipped: Number(r.skipped ?? 0),
    error: r.error ? String(r.error) : undefined,
  }));
}

/** Server-side AI clarify proposal for one inbox item (§2.2 agent seam).
 *  Richer than the local heuristic: org-knowledge capability matching
 *  (people skills + availability), server project auto-match, and the
 *  destination/stage defaults. Same shape as lib/clarify.ts's proposal. */
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
  /** true → re-clarify an already-processed task (preserves a SYNCED task's
   *  ClickUp destination binding server-side). */
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
export async function apiMergeInto(id: string, targetId: string): Promise<GtdItem> {
  return mapItem(
    await gatewayFetch<Raw>(`/items/${id}/merge-into`, {
      method: "POST",
      body: JSON.stringify({ target_id: targetId }),
    }),
  );
}

/** File an inbox capture as a SUB-STEP of an existing task (clarify "this is a
 *  step of X"). Returns the parent task (now with the new child). */
export async function apiFileUnder(id: string, parentId: string): Promise<GtdItem> {
  return mapItem(
    await gatewayFetch<Raw>(`/items/${id}/file-under`, {
      method: "POST",
      body: JSON.stringify({ parent_id: parentId }),
    }),
  );
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
  target: "local" | "clickup" = "local",
): Promise<ProjectPlan> {
  return mapPlan(
    await gatewayFetch<Raw>(`/plan`, {
      method: "POST",
      body: JSON.stringify({ name, description: description || null, target }),
    }),
  );
}

/** Materialise a (possibly edited) plan — LOCAL, or push to a ClickUp list. */
export async function apiApplyPlan(
  plan: ProjectPlan,
  opts: {
    target: "local" | "clickup";
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

/** Auto-assign @context to actionable tasks that have none (the synced ClickUp
 *  tasks that arrive context-less). Writes directly; returns the count set. */
export async function apiBackfillContext(): Promise<{
  scanned: number;
  updated: number;
}> {
  const r = await gatewayFetch<Raw>(`/ai/backfill-context`, { method: "POST" });
  return { scanned: Number(r.scanned ?? 0), updated: Number(r.updated ?? 0) };
}

/** Promote a LOCAL task to a ClickUp task delegated to a teammate — re-homes it
 *  onto the chosen workspace/project and pushes it upstream in one call. */
export async function apiDelegateItem(
  id: string,
  body: {
    assignee: { name: string; email?: string; provider_user_id?: string };
    account_id: string;
    project_id: string;
    next_action?: string;
    status?: string;
    due_at?: string;
  },
): Promise<GtdItem> {
  return mapItem(
    await gatewayFetch<Raw>(`/items/${id}/delegate`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  );
}
