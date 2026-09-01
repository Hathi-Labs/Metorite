import { cacheKey, invalidate } from "@/lib/dataCache";

import type { Rule as RecurrenceRule } from "./recurrence";

/**
 * Projects · the browser's client for /api/projects/*.
 *
 * Every call goes through the BFF proxy, never at the gateway directly — the
 * proxy is what carries the session identity the whole grant model scopes on.
 */

/** What `POST /nodes/{id}/archive|unarchive` reports back. */
export interface ArchiveResult {
  project_id: string;
  archived: boolean;
  /** Projects actually stamped or cleared — 0 when already in that state. */
  projects: number;
  /** Open tasks in the subtree. Reported, never acted on (D-PM-26). */
  open_tasks: number;
}

export interface ProjectRow {
  id: string;
  name: string;
  description?: string | null;
  parent_project_id?: string | null;
  /**
   * 'project' | 'folder' (migration 193). Absent/null reads as 'project' —
   * resolve through `nodeKind()` in lib/tree.ts, never directly.
   */
  kind?: string | null;
  status?: string | null;
  lead?: string | null;
  /** Sibling order, a float. `ORDER BY position NULLS LAST, name` on the server. */
  position?: number | null;
  // ⚠️ Provenance of rows imported BEFORE the 2026-08-24 retirement (D52).
  // Nothing writes these any more; the columns survive under R6 (D52.3) and
  // are dropped in a later, owner-gated release. Do not read them in new code.
  clickup_id?: string | null;
  clickup_kind?: string | null;
  /**
   * WS-27z — the lifecycle policy. ROOT-project settings (the subtree
   * inherits); `null` months = that policy is off, which is the default.
   */
  archive_after_months?: number | null;
  close_after_months?: number | null;
  timezone?: string | null;
  /**
   * WS-27bg — the ARCHIVE axis, which is not a run state (D-PM-25). Set means
   * filed out of the default surfaces; `archived_root_id` names which project's
   * archive filed it, so a subproject archived on its own survives its parent's
   * restore.
   */
  archived_at?: string | null;
  archived_root_id?: string | null;
  children?: ProjectRow[];
}

/** One direct child in a node's roll-up. */
export interface SummaryChild {
  id: string;
  name: string;
  kind: string;
  status?: string | null;
  archived: boolean;
  /** A space's chosen marker (migration 194) — portfolio children only. */
  icon?: string | null;
  icon_slot?: number | null;
  /** Tasks in this child's WHOLE subtree, visible to the caller. */
  tasks: number;
  overdue: number;
  by_category: Record<string, number>;
}

/**
 * What `GET /nodes/{id}/summary` returns — the roll-up a space or folder
 * shows instead of a board, and the aggregate a parent project folds into
 * its own views (migration 194 / owner directive 2026-08-31).
 */
export interface NodeSummary {
  id: string;
  name: string;
  level: "portfolio" | "space" | "folder" | "project" | "subproject";
  /** Tasks in the whole subtree, the node's own included. */
  tasks: number;
  overdue: number;
  by_category: Record<string, number>;
  /** Descendant PROJECTS. Folders are not counted — they hold no work. */
  projects: number;
  children: SummaryChild[];
}

export interface TaskRow {
  id: string;
  project_id: string;
  root_project_id: string;
  task_number?: number | null;
  parent_task_id?: string | null;
  type_id?: string | null;
  status_id: string;
  title: string;
  description?: string | null;
  importance?: number | null;
  estimate_mins?: number | null;
  /**
   * WS-27q — a floating calendar date (`DATE`, not an instant), which is why
   * it is never routed through `new Date()`: that would read it as midnight
   * UTC and move it a day west of Greenwich. A column that has existed since
   * migration 146 and had no surface until the calendar.
   */
  start_date?: string | null;
  due_at?: string | null;
  completed_at?: string | null;
  tags?: string[];
  created_at?: string | null;
  assignees?: string[];
  view_position?: number | null;
  view_group_key?: string | null;
  /**
   * WS-27l — values keyed by `field_key`. Always an object, never absent: the
   * column is `NOT NULL DEFAULT '{}'`, so a missing key means the field is
   * unset rather than that the values have not loaded.
   */
  custom_fields?: Record<string, unknown>;
  /**
   * WS-27s — the two counts a card draws, aggregated for the whole page rather
   * than fetched per row. Always present on the list endpoint; optional here
   * because the same type describes a row from `getTask`, where the panel reads
   * the full relations block instead.
   */
  subtasks?: { done: number; total: number };
  blocked_by_count?: number;
}

export interface StatusRow {
  id: string;
  project_id: string;
  name: string;
  color: string;
  position: number;
  category: string;
  is_default: boolean;
}

export interface ActivityRow {
  id: string;
  task_id?: string | null;
  type: string;
  body?: string | null;
  meta?: Record<string, unknown> | null;
  created_by?: string | null;
  created_at?: string | null;
  /**
   * WS-27j — people this comment named who could not be notified, because they
   * cannot see the task. Present on the POST response only; the timeline does
   * not carry it, since who was reachable is a fact about the moment of
   * posting rather than about the comment.
   */
  not_notified?: string[];
}

export interface ViewRow {
  id: string;
  project_id: string;
  name: string;
  view_type: string;
  /**
   * Filters and grouping, in the gateway's key names. Read it through
   * `grouping.fromConfig` rather than indexing into it — the server drops keys
   * it does not know, so a view written by a newer client comes back thinner
   * than it went in, and every field here is optional in practice.
   */
  config: Record<string, unknown>;
  position?: number | null;
}

/** WS-27m — a registered tag. `task_count` is present on the list endpoint. */
export interface TagRow {
  id: string;
  /**
   * `null` means ORG-WIDE (WS-27bj / D-PM-16): the tag belongs to the whole
   * organization rather than this project, and the list is `org-wide ∪
   * root-local` with root-local shadowing.
   *
   * ⚠️ **This is the ONLY declaration of `TagRow`, and it must stay that way.**
   * `lib/tags.ts` carried a second copy until H-6 collapsed it, and the two
   * were assignable only for as long as they happened to agree. Widening this
   * field and not its twin is what surfaced the duplication. `lib/tags.ts` now
   * re-exports this type, so both import paths still resolve here.
   */
  project_id: string | null;
  name: string;
  color: string;
  description?: string | null;
  task_count?: number;
  /** How many tasks a rename rewrote. Present on the PATCH response only. */
  retagged?: number;
}

/** WS-27l — a custom field definition. Shape mirrors the gateway's row. */
export interface FieldRow {
  id: string;
  /** `null` means ORG-WIDE — see `TagRow.project_id` (WS-27bj / D-PM-16). */
  project_id: string | null;
  field_key: string;
  name: string;
  description?: string | null;
  field_type:
    | "text"
    | "number"
    | "date"
    | "select"
    | "multi_select"
    | "boolean"
    | "url";
  options: string[];
  position: number;
  created_by?: string | null;
}

export interface GrantRow {
  id: string;
  project_id: string;
  subject: string;
}

export class ProjectsApiError extends Error {
  constructor(
    message: string,
    readonly status: number
  ) {
    super(message);
    this.name = "ProjectsApiError";
  }
}

/**
 * The one request seam for `/api/projects/*`.
 *
 * Exported as `projectsCall` (WS-39 S3a-client) because the Tasks lens speaks
 * to the same proxy and must not grow a second wrapper: a parallel fetch
 * helper is a parallel error type, a parallel place to forget the
 * Content-Type, and a second answer to "what does a 404 from this API mean".
 * Tasks reading the Projects client is not a layering breach — under D53 the
 * Tasks app IS a lens over Projects.
 */
/**
 * The cache namespace every Projects read is keyed under.
 *
 * One prefix, so a write can invalidate the whole family in one call — see
 * the note on `call` below.
 */
export const PROJECTS_CACHE = "projects/";

/** The key for a read. `useCachedResource` takes this, `call` does the fetch. */
export function projectsKey(
  path: string,
  params?: Record<string, unknown>
): string {
  return cacheKey(`${PROJECTS_CACHE}${path}`, params);
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/projects/${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    // The API answers 404 for "not yours" as well as "no such thing" (R5), so
    // the message is deliberately not embellished here — the UI must not
    // invent a distinction the server refuses to make.
    throw new ProjectsApiError(
      body?.detail ?? `Request failed (${res.status})`,
      res.status
    );
  }

  /**
   * ⚠️ A WRITE DROPS EVERY CACHED PROJECTS READ.
   *
   * Bluntly, on purpose. One task edit legitimately changes the board, the
   * table, the timeline, the calendar window, the triage counts and the
   * subtree roll-up — they are lenses on ONE store (D52/D53/D54), so a write
   * that refreshed only the lens you were looking at would leave the other
   * five to disagree with it until something else happened to reload them.
   * That disagreement is the exact bug a single task store exists to prevent,
   * and a per-endpoint invalidation map is how it creeps back in: the map goes
   * stale, and then it lies.
   *
   * The re-read is cheap BECAUSE the cache is stale-while-revalidate — every
   * dropped view keeps painting its last known rows while the new ones land.
   * Nothing blanks. Only a mount with nothing cached ever shows a skeleton.
   */
  const method = (init?.method ?? "GET").toUpperCase();
  if (method !== "GET") invalidate(PROJECTS_CACHE);

  return body as T;
}

export { call as projectsCall };

export const projectsApi = {
  tree: () => call<{ rows: ProjectRow[]; total: number }>("tree"),

  /** The subtree roll-up behind every dashboard and every aggregate view. */
  summary: (nodeId: string) =>
    call<NodeSummary>(`nodes/${nodeId}/summary`),

  /** The same shape one level up — every space the caller can see. */
  portfolio: () => call<NodeSummary>("summary"),

  /**
   * WS-27bk §9.12.4 — re-parent or reorder a node.
   *
   * `parent_project_id: null` makes it a space. `position` is a fractional
   * index between two siblings, omitted when only the parent changes.
   *
   * ⚠️ The server re-stamps `root_project_id` across the whole subtree, so a
   * move changes which status set every task below it reads. That is why the
   * caller must refetch rather than patch the tree in place.
   */
  moveNode: (
    projectId: string,
    parentProjectId: string | null,
    position?: number,
  ) =>
    call<ProjectRow>(`nodes/${projectId}/move`, {
      method: "POST",
      body: JSON.stringify(
        position === undefined
          ? { parent_project_id: parentProjectId }
          : { parent_project_id: parentProjectId, position },
      ),
    }),

  grants: (projectId: string) =>
    call<{ rows: GrantRow[]; total: number }>(`nodes/${projectId}/grants`),

  statuses: (projectId: string) =>
    call<{ rows: StatusRow[]; total: number }>(`nodes/${projectId}/statuses`),

  tasks: (params: Record<string, string | number | boolean | undefined>) => {
    const qs = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") qs.set(key, String(value));
    }
    return call<{ rows: TaskRow[]; total: number }>(`tasks?${qs.toString()}`);
  },

  /**
   * WS-27q — every task whose schedule overlaps a window.
   *
   * Deliberately NOT `tasks` with a date filter: that endpoint is paginated,
   * and a month read at `page_size=50` draws forty of its ninety tasks and
   * leaves the rest of the days looking empty. `truncated` is the endpoint
   * telling us when the cap was reached, so the view can say so rather than
   * present a plausible-looking short month.
   */
  calendar: (params: Record<string, string | number | boolean | undefined>) => {
    const qs = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") qs.set(key, String(value));
    }
    return call<{
      from: string;
      to: string;
      rows: TaskRow[];
      /**
       * WS-27t — the `blocks` edges with BOTH ends in the window, so an arrow
       * always has two bars to join. Empty unless `include_links`, and always
       * present: a missing key and an empty list read the same to a careless
       * client.
       */
      links: { id: string; blocker_id: string; blocked_id: string }[];
      truncated: boolean;
      cap: number;
      undated: number;
    }>(`calendar?${qs.toString()}`);
  },

  /**
   * WS-27r — ranked hits across every project the caller can see.
   *
   * Not `tasks?q=`: that endpoint is paginated and its ordering is a column
   * allowlist, neither of which a search box wants. `query` is echoed back so
   * a slow response to an earlier keystroke can be recognised and dropped.
   */
  search: (q: string) =>
    call<{
      rows: import("./search").Hit[];
      total: number;
      truncated: boolean;
      query: string;
    }>(`search?q=${encodeURIComponent(q)}`),

  task: (taskId: string) => call<TaskRow>(`tasks/${taskId}`),

  timeline: (taskId: string) =>
    call<{ rows: ActivityRow[]; total: number }>(`tasks/${taskId}/timeline`),

  createProject: (payload: Record<string, unknown>) =>
    call<ProjectRow>("nodes", { method: "POST", body: JSON.stringify(payload) }),

  /** WS-27z — root-project settings (lifecycle policy) ride the plain PATCH. */
  patchProject: (projectId: string, payload: Record<string, unknown>) =>
    call<ProjectRow>(`nodes/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  /**
   * WS-27bg — the archive axis. Both are idempotent and BOTH report what they
   * touched (`projects`, `open_tasks`), because a route that changes what a
   * caller can see owes them the number.
   */
  archiveProject: (projectId: string) =>
    call<ArchiveResult>(`nodes/${projectId}/archive`, { method: "POST" }),

  unarchiveProject: (projectId: string) =>
    call<ArchiveResult>(`nodes/${projectId}/unarchive`, { method: "POST" }),

  /**
   * The directory-backed assignee picker (WS-28e, people_center_app.md §6.1).
   * People and agents in one response; the HR half (load, skills, contracted)
   * follows the CALLER's grants and `hr_visible` says which emptiness an empty
   * field is. Warnings are shown, never enforced — the picker warns and still
   * lets you assign.
   */
  suggestAssignees: (q: string, due?: string | null) =>
    call<import("./assignees").PickerResponse>(
      `assignees?q=${encodeURIComponent(q)}${due ? `&due=${due}` : ""}`
    ),

  createTask: (payload: Record<string, unknown>) =>
    call<TaskRow>("tasks", { method: "POST", body: JSON.stringify(payload) }),

  patchTask: (taskId: string, payload: Record<string, unknown>) =>
    call<TaskRow>(`tasks/${taskId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  setAssignees: (taskId: string, assignees: string[]) =>
    call<{ task_id: string; assignees: string[] }>(`tasks/${taskId}/assignees`, {
      method: "PUT",
      body: JSON.stringify({ assignees }),
    }),

  comment: (taskId: string, body: string) =>
    call<ActivityRow>(`tasks/${taskId}/comments`, {
      method: "POST",
      body: JSON.stringify({ body }),
    }),

  /**
   * WS-27p — subtasks and links in both directions, plus derived blocked-ness.
   *
   * ONE call rather than three: the panel needs all of it at once, and three
   * round trips to fill one block is three chances to paint a half-drawn
   * dependency section.
   */
  relations: (taskId: string) =>
    call<import("./relations").Relations>(`tasks/${taskId}/relations`),

  createLink: (taskId: string, targetTaskId: string, linkType: string) =>
    call<{ id: string }>(`tasks/${taskId}/links`, {
      method: "POST",
      body: JSON.stringify({ target_task_id: targetTaskId, link_type: linkType }),
    }),

  deleteLink: (taskId: string, linkId: string) =>
    call<{ deleted: string }>(`tasks/${taskId}/links/${linkId}`, {
      method: "DELETE",
    }),

  /** WS-27o — this task's repeat rule, or `{rule: null}`. */
  recurrence: (taskId: string) =>
    call<{ rule: RecurrenceRule | null }>(`tasks/${taskId}/recurrence`),

  /** Set or replace it. A task has at most one rule, so this is a PUT. */
  setRecurrence: (taskId: string, payload: Record<string, unknown>) =>
    call<{ rule: RecurrenceRule }>(`tasks/${taskId}/recurrence`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  /** Stop the series. Everything it already created stays. */
  clearRecurrence: (taskId: string) =>
    call<{ cleared: boolean; cascaded?: { tasks_detached: number } }>(
      `tasks/${taskId}/recurrence`,
      { method: "DELETE" }
    ),

  /**
   * WS-27n — one edit applied to many tasks.
   *
   * Answers per-task outcomes rather than a single success: a selection can
   * span projects, so a status name valid in one and absent from another is a
   * fact about that task, not a reason to fail the batch.
   */
  bulkEdit: (payload: Record<string, unknown>) =>
    call<{
      requested: number;
      applied: number;
      results: Array<{ task_id: string; changed: string[]; status?: string | null }>;
      skipped: Array<{ task_id: string; reason: string }>;
      failed: Array<{ task_id: string; reason: string }>;
    }>("tasks/bulk", { method: "POST", body: JSON.stringify(payload) }),

  tags: (projectId: string) =>
    call<{ rows: TagRow[]; total: number }>(`nodes/${projectId}/tags`),

  createTag: (projectId: string, payload: Record<string, unknown>) =>
    call<TagRow>(`nodes/${projectId}/tags`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /** Rename or recolour. A rename rewrites every task wearing the tag. */
  patchTag: (tagId: string, payload: Record<string, unknown>) =>
    call<TagRow>(`tags/${tagId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  /** Fold one tag into another. The source is deleted; the target absorbs it. */
  mergeTag: (tagId: string, intoTagId: string) =>
    call<{ merged: string; into: string; retagged: number }>(
      `tags/${tagId}/merge`,
      { method: "POST", body: JSON.stringify({ into_tag_id: intoTagId }) }
    ),

  /** Deletes the tag AND takes it off every task — the count comes back. */
  deleteTag: (tagId: string) =>
    call<{
      deleted: string;
      name: string;
      cascaded: { tasks_untagged: number };
    }>(`tags/${tagId}`, { method: "DELETE" }),

  fields: (projectId: string) =>
    call<{ rows: FieldRow[]; total: number }>(`nodes/${projectId}/fields`),

  createField: (projectId: string, payload: Record<string, unknown>) =>
    call<FieldRow>(`nodes/${projectId}/fields`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  patchField: (fieldId: string, payload: Record<string, unknown>) =>
    call<FieldRow>(`fields/${fieldId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  /** Deletes the definition AND every value filed under it — the count comes back. */
  deleteField: (fieldId: string) =>
    call<{
      deleted: string;
      field_key: string;
      cascaded: { values_cleared: number };
    }>(`fields/${fieldId}`, { method: "DELETE" }),

  views: (projectId: string) =>
    call<{ rows: ViewRow[]; total: number }>(`nodes/${projectId}/views`),

  createView: (projectId: string, payload: Record<string, unknown>) =>
    call<ViewRow>(`nodes/${projectId}/views`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  patchView: (viewId: string, payload: Record<string, unknown>) =>
    call<ViewRow>(`views/${viewId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  deleteView: (viewId: string) =>
    call<{ deleted: string; cascaded: { positions: number } }>(`views/${viewId}`, {
      method: "DELETE",
    }),

  setPositions: (
    viewId: string,
    positions: Array<{ task_id: string; position: number; group_key?: string | null }>
  ) =>
    call<{ view_id: string; written: number }>(`views/${viewId}/positions`, {
      method: "PUT",
      body: JSON.stringify({ positions }),
    }),
};

// `myWorkApi` (WS-27e, the personal lens) was REMOVED with the My work
// surface (owner directive 2026-08-31). /tasks is the personal lens, and
// the gateway's `my/*` routes still serve it there.

export interface AttachmentRow {
  attachment_id: string;
  kind: "image" | "file";
  name: string;
  mime: string;
  size: number;
  added_by?: string | null;
  created_at?: string | null;
  url: string;
}

/**
 * Attachments (WS-27i).
 *
 * The upload is a raw multipart POST rather than a `call()` — that helper
 * forces JSON. It still goes through the BFF proxy, so identity travels the
 * same way everything else does.
 */
export const attachmentsApi = {
  list: (taskId: string) =>
    call<{ rows: AttachmentRow[]; total: number }>(`tasks/${taskId}/attachments`),

  upload: async (taskId: string, file: File): Promise<AttachmentRow> => {
    const body = new FormData();
    body.append("file", file);
    const res = await fetch(`/api/projects/tasks/${taskId}/attachments`, {
      method: "POST",
      body,
    });
    const text = await res.text();
    const parsed = text ? JSON.parse(text) : null;
    if (!res.ok) {
      throw new ProjectsApiError(
        parsed?.detail ?? `Upload failed (${res.status})`,
        res.status
      );
    }
    return parsed as AttachmentRow;
  },

  detach: (taskId: string, attachmentId: string) =>
    call<{ removed: number }>(`tasks/${taskId}/attachments/${attachmentId}`, {
      method: "DELETE",
    }),
};

export interface NotificationRow {
  id: string;
  kind: "assigned" | "mention" | "comment";
  task_id: string;
  actor: string;
  excerpt?: string | null;
  created_at?: string | null;
  read_at?: string | null;
  task_title?: string | null;
  task_number?: number | null;
  project_id?: string | null;
}

/**
 * Notifications (WS-27j).
 *
 * No `recipient` parameter anywhere, and that is the contract rather than an
 * omission: the gateway takes the recipient from the session, so there is no
 * request shape that reads somebody else's bell.
 */
export const notificationsApi = {
  // `unread` split since WS-27v: mentions are the subset whose reason is a
  // mention, drawn distinctly on the bell.
  list: (unreadOnly = false) =>
    call<{
      rows: NotificationRow[];
      total: number;
      unread: { total: number; mentions: number };
    }>(`notifications${unreadOnly ? "?unread_only=true" : ""}`),

  markRead: (ids: string[]) =>
    call<{ marked: number }>("notifications/read", {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),

  markAllRead: () =>
    call<{ marked: number }>("notifications/read", {
      method: "POST",
      body: JSON.stringify({ all: true }),
    }),
};

/**
 * Watchers (WS-27v).
 *
 * The watcher is always the session's identity — no parameter, same contract
 * as the bell above: there is no request shape that subscribes somebody else.
 * Both writes are idempotent, so the toggle can be optimistic.
 */
export const watchersApi = {
  get: (taskId: string) =>
    call<{ watchers: string[]; watching: boolean }>(
      `tasks/${taskId}/watchers`
    ),

  watch: (taskId: string) =>
    call<{ task_id: string; watching: boolean }>(`tasks/${taskId}/watch`, {
      method: "PUT",
    }),

  unwatch: (taskId: string) =>
    call<{ task_id: string; watching: boolean }>(`tasks/${taskId}/watch`, {
      method: "DELETE",
    }),
};

/**
 * ⚠️ `importApi` was REMOVED 2026-08-24 (D52, board WS-39 S1) along with both
 * gateway endpoints. Metorite is the project-management system of record, so
 * there is nothing to import from. Do not re-add a client here — the
 * `gtd_*` → `pm_*` move D53 still needs is a backfill migration, not an API.
 */
/**
 * Intake — the front door (WS-27u).
 *
 * The queue is scoped server-side by the same grants as the tasks it wraps,
 * and a snoozed item reappears by being read (`snoozed_until <= now()`), so
 * nothing here polls or wakes anything. Every ruling answers `{task, intake}`
 * — the task flipped/archived IN PLACE plus the wrapper, which is permanent
 * provenance and is never deleted.
 */
export const intakeApi = {
  queue: (params: Record<string, string | number | boolean | undefined> = {}) => {
    const qs = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") qs.set(key, String(value));
    }
    const query = qs.toString();
    return call<{ rows: import("./intake").IntakeItem[]; total: number }>(
      `intake${query ? `?${query}` : ""}`
    );
  },

  capture: (payload: Record<string, unknown>) =>
    call<{ task: TaskRow; intake: import("./intake").IntakeInfo }>("intake", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /** Accept: the task's status flips in place; omit `statusId` for the default. */
  accept: (taskId: string, statusId?: string | null) =>
    call<{ task: TaskRow; intake: import("./intake").IntakeInfo }>(
      `intake/${taskId}/accept`,
      {
        method: "POST",
        body: JSON.stringify(statusId ? { status_id: statusId } : {}),
      }
    ),

  /** Decline: archives the task; the wrapper stays as the reason it exists. */
  decline: (taskId: string) =>
    call<{ task: TaskRow; intake: import("./intake").IntakeInfo }>(
      `intake/${taskId}/decline`,
      { method: "POST", body: "{}" }
    ),

  /** Duplicate: records WHICH task this repeats, then archives the capture. */
  duplicate: (taskId: string, duplicateOfTaskId: string) =>
    call<{ task: TaskRow; intake: import("./intake").IntakeInfo }>(
      `intake/${taskId}/duplicate`,
      {
        method: "POST",
        body: JSON.stringify({ duplicate_of_task_id: duplicateOfTaskId }),
      }
    ),

  /** Snooze: hidden from the queue until `until`, then reappears on its own. */
  snooze: (taskId: string, until: string) =>
    call<{ task: TaskRow; intake: import("./intake").IntakeInfo }>(
      `intake/${taskId}/snooze`,
      { method: "POST", body: JSON.stringify({ until }) }
    ),
};

