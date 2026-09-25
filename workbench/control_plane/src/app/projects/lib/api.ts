import { describeFailure, detailText, readJsonBody } from "@/lib/apiError";
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

/**
 * What `DELETE /nodes/{id}` reports back.
 *
 * ⚠️ **These counts are read by the server BEFORE the delete and are the only
 * honest ones available** — afterwards the rows are gone. They are what the
 * toast reports. They are NOT what the confirmation dialog shows: that has to
 * ask before the write, which is `summary()`'s job.
 *
 * `cascaded.projects` INCLUDES the project named in the call, so it is one
 * larger than the "subprojects" the dialog counted. The wording on each side
 * says which it means.
 */
export interface DeleteProjectResult {
  deleted: string;
  cascaded: { projects: number; tasks: number; grants: number };
}

/** WS-27bl — the body both move endpoints take. */
export interface MoveRequest {
  task_ids: string[];
  destination_project_id: string;
  /** Old status id → new status id. The member's answer beats the automatic one. */
  status_map?: Record<string, string>;
  /** Source `field_key` → destination `field_key`. */
  field_map?: Record<string, string>;
  /** The member saw what would be dropped and agreed (D-PM-29). */
  accept_drops?: boolean;
  /**
   * The field keys the preview SHOWED as dropping.
   *
   * ⚠️ Not redundant with `accept_drops`. That flag is a bare yes to a
   * question asked earlier; if the destination changes in between, the drop
   * set grows and a stale yes would accept the extra loss too. Sending the
   * shown set lets the server answer 409 instead.
   */
  accepted_drops?: string[];
}

export interface MoveMapRow<T> {
  from: T;
  to: T | null;
}

/**
 * What `POST /tasks/move/preview` answers.
 *
 * ⚠️ `drops` is keyed by the SOURCE `field_key` and lists the tasks that would
 * actually lose a value — which is NOT the same as `orphan_fields`. A field
 * with no home costs nothing on a task that never filled it in, and warning
 * about that would be a warning about nothing.
 */
export interface MovePlan {
  source_project_id: string;
  destination_project_id: string;
  source_root_id: string;
  destination_root_id: string;
  crosses_status_set: boolean;
  crosses_root: boolean;
  task_count: number;
  statuses: MoveMapRow<{ id: string; name: string; category: string }>[];
  /**
   * The DESTINATION's own lanes, for the per-row override.
   *
   * ⚠️ Carried on the plan rather than fetched by the card, because the page
   * only ever holds the SELECTED project's statuses — and the destination is
   * never the selected project.
   */
  destination_statuses: { id: string; name: string; category: string }[];
  /** Destination fields the selection does not satisfy (migration 192). */
  required_missing: string[];
  field_map: Record<string, string>;
  orphan_fields: { field_key: string; name: string; field_type: string }[];
  drops: Record<
    string,
    { task_id: string; task_number?: number | null; value: unknown }[]
  >;
  types: MoveMapRow<{ id: string; name?: string | null }>[];
  tags: { carried: string[]; unregistered: string[] };
}

export interface MoveResult {
  moved: number;
  task_ids: string[];
  destination_project_id: string;
  dropped_fields: string[];
}

export interface ProjectRow {
  id: string;
  name: string;
  description?: string | null;
  parent_project_id?: string | null;
  /**
   * The SUBTREE roll-up `GET /projects/tree` stamps on every node: how many
   * tasks live at or under it, how many were delivered, and how many were
   * abandoned.
   *
   * ⚠️ Optional because only `/tree` carries them. `/nodes` returns the same
   * rows FLAT and rolls nothing up, so a consumer of that list reads
   * `undefined` rather than a confident zero. The ring still draws, empty,
   * rather than the row changing shape once a number arrives.
   *
   * ⚠️ **`done` is the DELIVERED count — the `done` category alone.**
   * `cancelled` is reported separately because the completion rule subtracts
   * it from the DENOMINATOR rather than adding it to the numerator. That is
   * the rule `NodeDashboard` beside this tree has always printed. An earlier
   * version folded the two together, and drew a full ring for a project whose
   * work had all been abandoned.
   *
   * They agree with `nodes/{id}/summary` by construction: the same join, the
   * same visibility clause. Measured 2026-09-23 against a real gateway, space
   * and child alike.
   */
  tasks?: number | null;
  done?: number | null;
  cancelled?: number | null;
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
  /**
   * The node's OWN tasks — the ones that sit in no subproject.
   *
   * ⚠️ **`tasks` above counts these, and no `children` row does.** The
   * subtree walk starts BELOW the node, so a project carrying subprojects
   * AND its own work showed a total that no row on the page added up to
   * (owner report, 2026-09-17). This block is the row that was missing.
   *
   * The server guarantees `own.tasks + sum(children[].tasks) === tasks`.
   *
   * ⚠️ Typed optional because the server sends it ALWAYS, and a client must
   * still survive the deploy window where it does not. Absent means "the
   * server did not say", which is not zero — `NodeDashboard` draws neither.
   */
  own?: {
    tasks: number;
    overdue: number;
    by_category: Record<string, number>;
  };
}

/**
 * WS-27bk §9.12.7 — the four analytics questions, as three endpoints.
 *
 * ⚠️ **Every number here is a SERVER aggregate, and none may be recomputed in
 * the browser.** The task list is paginated, so a count taken over the rows on
 * screen is a count of one page. It looks plausible and it is wrong, and
 * nothing on the way says so. `analytics.py`'s module header is the other half
 * of this rule.
 *
 * All three take an OPTIONAL node. Omit it for the portfolio, which is what
 * the Analytics pane passes — that pane holds no node id at all.
 */
/**
 * WS-27bk wave 7 — will this land, when, and what would have to be true.
 *
 * ⚠️ **Built with NO time tracking.** Owner direction 2026-09-17: derive the
 * temporal shape of a project from what we already store — the activity
 * spine, `estimate_mins`, `due_at`, and `people`. Nothing here is a
 * logged hour, and nothing may be labelled as one.
 */
export interface VelocityForecast {
  /** Whole weeks of history read. The current week is excluded — it is partial. */
  weeks_sampled: number;
  finished_per_week: number;
  /** ⚠️ The half a naive forecast ignores. Work ARRIVING. */
  created_per_week: number;
  /** finished minus created. Zero or less and the backlog never empties. */
  net_per_week: number;
  remaining_tasks: number;
  weeks_remaining: number | null;
  /** ISO date, or null. Null is a FINDING — read `verdict` for which one. */
  finish_date: string | null;
  verdict:
    | "converging"
    /** ⚠️ Scope is growing at least as fast as delivery. No date exists. */
    | "not_converging"
    | "no_history"
    | "nothing_left";
}

export interface CapacityForecast {
  /** Stated weekly hours of the people holding open work here. */
  hours_per_week: number;
  hours_left: number;
  /** 0–1. Share of open tasks carrying an estimate. */
  estimate_coverage: number;
  weeks_remaining: number | null;
  finish_date: string | null;
  verdict: "ok" | "no_estimates" | "no_capacity" | "nothing_left";
}

export interface OutlookReport {
  project_id: string | null;
  scope: "portfolio" | "node";
  weeks: number;
  /** What WILL happen, at the rate this team actually goes. */
  velocity: VelocityForecast;
  /** What the plan would NEED, if the estimates are right. */
  capacity: CapacityForecast;
  plan: {
    planned_finish: string | null;
    /** ⚠️ `dated` of `tasks`. A planned finish over 4 of 71 is about 4. */
    dated: number;
    tasks: number;
    /** Projected minus planned, in days. Negative is early. */
    slip_days: number | null;
  };
  people: {
    holding_open_work: number;
    with_stated_capacity: number;
    with_schedule_only: number;
    hours_per_week: number;
    /** An engagement ending inside the window — a risk no velocity can see. */
    leaving_within_90d: number;
  };
}

/**
 * WS-27bm S7a — who holds the open work, and whether they have the hours.
 *
 * ⚠️ **The HR half is ABSENT for a caller without `admin:members:read`, not
 * null.** Every HR key is optional here for that reason, and `hr_visible`
 * says which answer arrived. A reader must test for the key, never compare a
 * missing figure with zero: zero would read as "free".
 *
 * ⚠️ **Two scopes.** `open_tasks`, `overdue` and the estimate left are THIS
 * scope, and equal Load's figures for it. The hours are measured over all the
 * work the caller can see, and `all_work` carries those counts.
 */
export interface CapacityRow {
  /** `null` is the unassigned row, which the server always sends, last. */
  assignee: string | null;
  name: string | null;
  kind: "person" | "agent" | "unassigned";
  in_directory: boolean;
  open_tasks: number;
  overdue: number;
  due_next_7d: number;
  later: number;
  estimated_hours_left: number;
  estimated: number;
  // ── The HR half. Absent without the grant. ─────────────────────────────
  all_work?: {
    open_tasks: number;
    overdue: number;
    unestimated: number;
    in_progress: number;
  };
  contracted_hours_per_week?: number;
  working_hours_this_week?: number | null;
  working_hours_horizon?: number | null;
  /** The four below are ALSO absent when `hours_basis` is false. */
  committed_hours_this_week?: number;
  committed_hours_horizon?: number;
  spare_hours_this_week?: number | null;
  spare_hours_horizon?: number | null;
  hours_basis?: boolean;
  /** Why the hours are missing, when they are. */
  hours_note?: string | null;
  absences?: { kind: string; starts_on: string; ends_on: string }[];
  end_date?: string | null;
  leaving_in_window?: boolean;
  at_risk?: {
    task_id: string;
    title: string;
    due_on: string;
    needed_hours: number;
    available_hours: number;
    shortfall_hours: number;
  }[];
  pill?: "behind" | "at_risk" | "overloaded" | "idle" | "on_track";
  pill_reason?: string;
  flags?: string[];
  max_concurrent_tasks?: number | null;
  over_concurrency?: boolean;
  skills?: { skill: string; level: string | null }[];
}

export interface CapacityWindow {
  starts_on: string;
  ends_on: string;
}

export interface CapacityReport {
  project_id: string | null;
  scope: "portfolio" | "node";
  horizon_days: number;
  hr_visible: boolean;
  /** The pill's Monday-to-Sunday week, and the spare-hours horizon. */
  windows: {
    week: CapacityWindow & { used_for: string };
    horizon: CapacityWindow & { days: number; used_for: string };
  };
  /**
   * WS-27bn R2b: these three are absent in a REPORT's capacity section, and
   * `CapacityPanel` reads none of them.
   */
  task_scope?: string;
  hours_scope?: string;
  partial?: boolean;
  /** Counted over TASKS, as Load counts it. The rows sum past it. */
  total_tasks: number;
  people_total: number;
  rows: CapacityRow[];
}

/**
 * WS-27bm S7c — where the plan interferes with itself.
 *
 * ⚠️ **The four HR kinds are ABSENT for a caller without
 * `admin:members:read`**, from `rows`, from `by_kind` and from `kinds`. A
 * reader tests `hr_visible`, never a zero.
 *
 * ⚠️ **The rows are the server's decision.** `lib/conflicts.ts` chooses words
 * and hues, and never decides whether a pair is in conflict.
 */
export type ConflictKind =
  | "dependency_order"
  | "blocker_late"
  | "parallel_person"
  | "overcommitted"
  | "absent_on_due"
  | "over_concurrency"
  | "leaving";

export interface ConflictRow {
  kind: ConflictKind;
  severity: "high" | "medium";
  task_ids: string[];
  people: { email: string; name: string | null }[];
  /** One sentence from the server. It carries task titles. */
  sentence: string;
  /** The day the row is about, and the day the server sorts by. */
  due_on: string | null;
  /** `parallel_person` only: the busiest day, and how many tasks it holds. */
  day?: string;
  tasks_total?: number;
  /** `overcommitted` only. */
  shortfall_hours?: number;
  needed_hours?: number;
  available_hours?: number;
}

export interface ConflictsReport {
  project_id: string | null;
  scope: "portfolio" | "node";
  /** WS-27bn R2b: optional, with `partial`, `kinds` and `truncated` below.
   *  A REPORT's conflicts section carries none of the four. */
  include_subtree?: boolean;
  horizon_days: number;
  hr_visible: boolean;
  /** Bounds the dated kinds. The kinds in `ignored_by` read no window. */
  window: {
    starts_on: string;
    ends_on: string;
    days: number;
    ignored_by: ConflictKind[];
  };
  partial?: boolean;
  /** The kinds THIS caller may see. */
  kinds?: ConflictKind[];
  /** Every row, before the cap. */
  total: number;
  by_kind: Partial<Record<ConflictKind, number>>;
  truncated?: boolean;
  rows: ConflictRow[];
}

export interface StuckReport {
  project_id: string | null;
  scope: "portfolio" | "node";
  /**
   * Open tasks by how long they have sat untouched. Bands are DISJOINT.
   *
   * ⚠️ **The server sends a LIST of `{band, n}`, and this said
   * `Record<string, number>` until 2026-09-17.** The panel then asked
   * `"under_7d" in data.stale`, which on an array tests indices, so it was
   * always false and the histogram drew an empty bar with no legend.
   * Nothing threw and nothing failed a typecheck — `api.call` casts.
   *
   * Typed loosely on purpose, and read through `analyticsRead.staleBands`,
   * which accepts either shape. The same class of defect has now landed
   * three times in this one panel.
   */
  stale?: { band: string; n: number }[] | Record<string, number>;
  /**
   * WS-27bn R2b. Optional, because a REPORT's `stuck` section carries only
   * the overdue half. `StuckPanel` then draws no band chart and no blocked
   * list, and it never says "No open work" about work it was not sent.
   */
  blocked?: {
    id: string;
    title: string;
    task_number: number | null;
    due_at: string | null;
  }[];
  blocked_total?: number;
  /**
   * ⚠️ Overdue BY PROJECT, which is what §9.12.7(a) asks for.
   *
   * The server answered a bare integer here until 2026-09-17 while this type
   * declared a list. Nothing threw: `number.length` is undefined and
   * `undefined > 0` is false, so the Overdue section rendered nothing at all.
   * A total also cannot answer the question the section is for, which is
   * WHERE the late work is.
   */
  overdue: { project_id: string; name: string; overdue: number }[];
  overdue_total: number;
}

export interface LoadRow {
  /** `null` is the UNASSIGNED bar — usually the real finding, never a gap. */
  assignee: string | null;
  open_tasks: number;
  overdue: number;
  /**
   * Seven ROLLING days, which is why it is not called `this_week`. A calendar
   * week shrinks as the week runs, and it needs a timezone a subtree may not
   * share. See the route's docstring.
   *
   * WS-27bn R2b: optional, so a report body from a server before R2b still
   * draws. `LoadPanel` drops an absent bucket and says nothing about it.
   */
  due_next_7d?: number;
  later?: number;
  /**
   * ⚠️ **ESTIMATED effort, never logged effort.** Nothing in this product
   * records hours worked — `pm_tasks` has `estimate_mins` and no actual.
   * This is what somebody GUESSED the plate weighs.
   */
  est_mins?: number;
  /**
   * How many of `open_tasks` carry an estimate at all. The coverage, and it
   * is not optional: 40h over 3 of 30 tasks is not 40h of work.
   */
  estimated?: number;
}

/**
 * Estimated effort in scope, in both directions.
 *
 * ⚠️ **`basis` is always `"estimate"` today, and the field exists so a client
 * cannot quietly start calling this logged time.** There is no time tracking
 * in the product. "Spent" is the estimate of work that reached `done`.
 *
 * ⚠️ **Counted over TASKS.** A two-assignee task is on two plates in
 * `people` and is one task here, so these never add up from those rows.
 */
export interface EffortReport {
  left_mins: number;
  /** Open tasks carrying an estimate. */
  left_estimated: number;
  /** All open tasks. `left_estimated / left_tasks` is the coverage. */
  left_tasks: number;
  spent_mins: number;
  spent_estimated: number;
  spent_tasks: number;
  basis: "estimate";
}

export interface LoadReport {
  project_id: string | null;
  scope: "portfolio" | "node";
  /**
   * ⚠️ Counted over TASKS, so it is SMALLER than the sum of `open_tasks`. A
   * task with two assignees is on both plates and counts for both. Never
   * derive this by adding the rows.
   */
  total_tasks: number;
  /** Absent in a REPORT's section (WS-27bn R2b). The panel then omits it. */
  people_total?: number;
  people: LoadRow[];
  /**
   * ⚠️ Optional because the SERVER sends it always, and a client must still
   * survive the deploy window where it does not. Absent means "not said",
   * which is never the same as zero.
   */
  effort?: EffortReport;
  /** People with a name on open work. Excludes the unassigned bucket. */
  people_named?: number;
}

export interface ThroughputWeek {
  /** ISO Monday, in UTC. A date, never an instant. */
  week_start: string;
  /**
   * WS-27bn R2b. Every figure below `week_start` is optional, because a
   * REPORT's throughput section sends the weekly count only, and its summary
   * has no `completed`. Absent is "not sent", never zero. `ThroughputPanel`
   * hides a figure it was not sent.
   */
  completed: number;
  cancelled?: number;
  /** Completions that HAD a recorded start. The median's denominator. */
  measured?: number;
  /** Completions with no `in_progress` ever recorded. Not a zero — absent. */
  no_start?: number;
  /** `null` when nothing measurable finished. ⚠️ Never render null as 0. */
  median_hours?: number | null;
  p90_hours?: number | null;
}

export interface ThroughputReport {
  project_id: string | null;
  scope: "portfolio" | "node";
  weeks: number;
  series: ThroughputWeek[];
  /** The last bucket is the current week, and the week is not over. */
  current_week_partial: boolean;
  summary: Partial<Omit<ThroughputWeek, "week_start">>;
}

export interface FinishedProject {
  project_id: string;
  name: string;
  completed: number;
  cancelled: number;
  /** WS-27bn R2b: a report passes it through, so it may be absent in a deploy window. */
  median_hours?: number | null;
}

export interface FinishedReport {
  project_id: string | null;
  scope: "portfolio" | "node";
  weeks: number;
  /**
   * ⚠️ The REPORT's window, not the dashboard's. `true` drops the running
   * week, because a weekly report describes a week that ended — see the
   * route's `_window`.
   */
  skip_current_week: boolean;
  /**
   * ⚠️ Both dates come from the SERVER and must not be re-derived here. A
   * client that works them out from its own clock disagrees across a timezone
   * or a midnight, and two copies of one report then name different weeks.
   */
  period_start: string;
  /** INCLUSIVE — the last day the window contains. */
  period_end: string;
  projects: FinishedProject[];
  total_completed: number;
  total_cancelled: number;
  /** Absent in a REPORT's section, which the panel does not read anyway. */
  median_hours?: number | null;
}

/**
 * WS-27bk §9.12.8 — a saved report definition.
 *
 * ⚠️ It holds the QUESTION, never the answer. `config` is the period and the
 * sections; the numbers arrive only from `renderReport`, which re-runs
 * §9.12.7's own SQL. A cached total here would be the second set of numbers
 * §9.12.8 exists to prevent.
 */
export interface ReportRow {
  id: string;
  /** `null` is the PORTFOLIO — the scope a weekly report usually wants. */
  project_id: string | null;
  scope: "portfolio" | "node";
  name: string;
  config: ReportConfig;
  created_by: string;
  created_at: string;
  /**
   * WS-27bn R2. True when the caller wrote this report. The SERVER decides,
   * from the authenticated caller. "Your reports" reads it.
   */
  mine?: boolean;
}

/**
 * What a report asks: the period, the scope's depth and the sections.
 *
 * ⚠️ The server validates it in `normalise_report_config` and PATCH
 * REPLACES it. So a client always sends the whole object, never a part.
 */
export interface ReportConfig {
  weeks: number;
  /** On for a report: a weekly report describes a week that ENDED. */
  skip_current_week: boolean;
  include_subtree: boolean;
  sections: string[];
  /**
   * WS-27bn R2. The template the report started from, as an origin label.
   * Absent on a report that started from no template. The server refuses an
   * unknown key and a coming-soon key with 422.
   */
  template?: string;
}

/**
 * WS-27bn R2 — one entry of `GET /projects/reports/templates`.
 *
 * ⚠️ The client holds NO copy of the catalogue. `reports.py` `TEMPLATES` is
 * the one list, and this type only describes its entries.
 */
export interface ReportTemplate {
  key: string;
  name: string;
  /** The question the template answers, in plain words. */
  question: string;
  scope_kinds: string[];
  available: boolean;
  /** Only on a live template. */
  sections?: string[];
  weeks?: number;
  skip_current_week?: boolean;
  /** Only on a coming-soon template: what it waits for. */
  waits_for?: string;
}

/**
 * WS-27bn R1 — the `report` in a preview. No row exists, so it has no id.
 * `RenderedBody` reads only `name` and `scope`.
 */
export interface ReportStub {
  id: null;
  project_id: string | null;
  scope: "portfolio" | "node";
  name: string;
  config: ReportConfig;
}

/** What `POST /projects/reports/preview` answers: a render with a stub. */
export type PreviewReportBody = Omit<RenderedReportBody, "report"> & {
  report: ReportStub;
};

/** What `GET /projects/reports/{id}/render` answers. */
export interface RenderedReportBody {
  report: ReportRow;
  /** ⚠️ From the SERVER. Never re-derived from the browser's clock. */
  period_start: string;
  /** INCLUSIVE — the last day the window contains. */
  period_end: string;
  sections: {
    finished?: {
      projects: {
        project_id: string;
        name: string;
        completed: number;
        cancelled: number;
        /**
         * WS-27bn R2b: passed through from `finished_sql`. Optional, so a
         * body from a server before R2b still draws.
         */
        median_hours?: number | null;
      }[];
      total_completed: number;
      total_cancelled: number;
    };
    throughput?: {
      series: { week_start: string; completed: number }[];
      median_hours: number | null;
      measured: number;
      /** WS-27bn R2b: passed through from `_CYCLE_MEASURES`. */
      p90_hours?: number | null;
      no_start?: number;
      cancelled?: number;
    };
    /**
     * WS-27bn R3a. Opt-in. The outlook route's own body, for the report's
     * scope, over the route's own weeks of history (not the report period).
     */
    outlook?: OutlookReport;
    load?: {
      people: {
        assignee: string | null;
        open_tasks: number;
        overdue: number;
        /** WS-27bn R2b: passed through from `load_sql`. */
        due_next_7d?: number;
        later?: number;
      }[];
      total_tasks: number;
    };
    /** WS-27bm S7a. Opt-in: present only when the report asked for it. */
    capacity?: {
      people: CapacityRow[];
      people_total: number;
      total_tasks: number;
      hr_visible: boolean;
      horizon_days: number;
      windows: CapacityReport["windows"];
    };
    stuck?: {
      overdue: { project_id: string; name: string; overdue: number }[];
      overdue_total: number;
      /**
       * WS-27bn R3a. The route's ageing bands, as `{band, n}`. Optional, so
       * a body from a server before R3a still draws.
       */
      stale?: { band: string; n: number }[];
    };
    /** WS-27bm S7c. Opt-in: present only when the report asked for it. */
    conflicts?: {
      rows: ConflictRow[];
      total: number;
      by_kind: ConflictsReport["by_kind"];
      hr_visible: boolean;
      horizon_days: number;
      window: ConflictsReport["window"];
    };
  };
}

/**
 * WS-27bh — a registered task type. `pm_task_types` since migration 146.
 *
 * `icon` is a Lucide name and `color` goes through `resolveHue`, the same
 * path a tag's colour takes — never a palette of its own.
 */
export interface TaskTypeRow {
  id: string;
  name: string;
  icon?: string | null;
  color?: string | null;
  is_default?: boolean | null;
  is_system?: boolean | null;
}

export interface TaskRow {
  id: string;
  project_id: string;
  root_project_id: string;
  task_number?: number | null;
  parent_task_id?: string | null;
  type_id?: string | null;
  /**
   * WS-27bh — `manual | import | email | agent | automation` (migration 146).
   * `TaskModel` has always sent it; nothing declared it, so nothing read it.
   */
  source?: string | null;
  /**
   * When somebody filed this task, or null.
   *
   * ⚠️ On `TaskModel` since WS-27w and declared by no client until now, so
   * every surface treated an archived task as live. It only ever ARRIVED on
   * a read that asked for archived tasks, which is why nothing noticed.
   */
  archived_at?: string | null;
  /**
   * Migration 210 — the task this one was folded into, or absent.
   *
   * ⚠️ **A task carrying this is ALWAYS archived**, and the database
   * enforces it (`pm_tasks_merged_is_archived`). So a surface may read this
   * as "and it is on the Archived shelf" with no second check — which is
   * what lets the archive list merged tasks without a query of its own.
   *
   * Its content is NOT here any more: comments, history and attachments
   * moved to the task it names.
   */
  merged_into_task_id?: string | null;
  merged_at?: string | null;
  merged_by?: string | null;
  status_id: string;
  title: string;
  description?: string | null;
  /** The matrix's Important is `importance >= 2` (D78, `IMPORTANT_AT`). */
  importance?: number | null;
  /** D78 — the matrix's shared Leveraged input. Migration 218. */
  leveraged?: boolean | null;
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
  /**
   * D77 — minutes every member has timed on this task (their overlay
   * actuals, summed). Only the single read (`GET /tasks/{id}`) carries it;
   * the list endpoint does not, so a board row reads `undefined`.
   */
  time_spent_mins?: number;
}

export interface StatusRow {
  id: string;
  project_id: string;
  name: string;
  color: string;
  position: number;
  category: string;
  /**
   * ⚠️ Still on the wire, read by nothing (owner directive 2026-09-06).
   *
   * The first lane by position is where work starts. A flag was a second
   * answer to a question the order already answered, and it was the answer
   * nobody could see — on the dev database it sat on `backlog` for every
   * space, leaving three of four category columns with no answer at all.
   * The column is dropped in a later release (R6).
   */
  is_default: boolean;
}

/** Where a project's lanes come from — `projectsApi.statusSet`. */
export interface StatusSetInfo {
  project_id: string;
  /** Does this node carry its own set, rather than using an ancestor's? */
  owns: boolean;
  /** Where the lanes come from TODAY. Equals this node when `owns`. */
  owner_id: string;
  owner_name: string;
  /**
   * Where inheriting would take it — the status owner of the PARENT.
   *
   * ⚠️ Not the same as `owner_*`, and the difference is the whole reason this
   * field exists. While a node inherits, the two agree, which is why one field
   * served both for ten days. Once it owns its set, `owner_name` is the node
   * ITSELF — so a control labelling the inherit option with it offered
   * "Inherit from Mobile App" while configuring Mobile App (owner report,
   * 2026-09-16).
   *
   * `null` on a space, which has nothing above it.
   */
  inherit_from_id: string | null;
  inherit_from_name: string | null;
  /**
   * Projects under the OWNER that keep their own lanes — an edit here does not
   * reach them.
   *
   * ⚠️ Read of the owner, not of this node. An inheriting project is editing
   * its ancestor's set, so the breakaways that matter are that set's.
   *
   * Immediate breakaways only. A project that overrode a project that overrode
   * this one was never getting these lanes, and listing it would grow this
   * list with the tree rather than with the decisions somebody took.
   */
  overrides: { id: string; name: string }[];
  /** False on a space: there is nothing above it to inherit from. */
  can_inherit: boolean;
  /** A set it owned before, kept so switching back restores those lanes. */
  has_dormant_set: boolean;
  /** Does the caller hold `projects:settings:write`? */
  may_edit: boolean;
}

export interface StatusSetChange {
  mode: "inherit" | "own";
  /** `own` only — the node whose lanes to duplicate. */
  copy_from?: string;
  /** `{old status id: target lane NAME}`. See `setStatusSet` for why a name. */
  mapping?: Record<string, string>;
}

/** One lane the scope's tasks sit in now, and where they would land. */
export interface StatusMove {
  status_id: string;
  name: string;
  category: string;
  tasks: number;
  /** The lane survives the switch by name AND category, so nothing moves. */
  unchanged: boolean;
  /** The pre-filled target lane NAME, or null when the rule found none. */
  suggested: string | null;
  /** Does the suggested lane close a task? Null when there is no suggestion. */
  closes: boolean | null;
  closed_now: boolean;
}

export interface StatusSetPreview {
  lanes: StatusRow[];
  moves: StatusMove[];
  moving: number;
  completing: number;
  reopening: number;
}

export interface StatusSetResult {
  project_id: string;
  owns: boolean;
  owner_id: string;
  moved: number;
  completed: number;
  reopened: number;
}

export interface ActivityRow {
  id: string;
  task_id?: string | null;
  /**
   * Migration 208 — the comment this comment answers, or absent/null for a
   * top-level entry.
   *
   * ONE level: the gateway refuses a reply to a reply, so a client never has
   * to render a third rung. Carried on every activity shape rather than on a
   * comment-only type, because the timeline is one stream and a second row
   * shape is the split that makes a caller guess which one it holds.
   */
  parent_id?: string | null;
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
  /** Migration 192. The move dialog asks for these before a task may enter. */
  required?: boolean;
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
    readonly status: number,
    /**
     * The gateway's `detail`, as it came. S6c: a 422 from
     * `assert_required_fields_present` carries `{error, message, fields}`,
     * and the move dialog draws `fields` as inputs so the member answers
     * the exact field the refusal named rather than reading its name.
     */
    readonly detail?: unknown
  ) {
    super(message);
    this.name = "ProjectsApiError";
  }
}

/** The field definitions a structured 422 named, or none. */
export function refusedFields(err: unknown): FieldRow[] {
  const detail = err instanceof ProjectsApiError ? err.detail : undefined;
  const fields = (detail as { fields?: unknown } | undefined)?.fields;
  return Array.isArray(fields) ? (fields as FieldRow[]) : [];
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

/**
 * The analytics scope, as a query string.
 *
 * ⚠️ An ABSENT `project_id` is the portfolio. Sending `project_id=` empty
 * is not the same thing — FastAPI would read it as a string and answer 404 for
 * a node that cannot exist. So the key is omitted rather than blanked.
 */
function scopeQuery(nodeId?: string, weeks?: number): string {
  const parts: string[] = [];
  if (nodeId) parts.push(`project_id=${encodeURIComponent(nodeId)}`);
  if (weeks) parts.push(`weeks=${weeks}`);
  return parts.length ? `?${parts.join("&")}` : "";
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

  /**
   * ⚠️ **PARSE DEFENSIVELY, AND PARSE AFTER THE STATUS — not before it.**
   *
   * This read used to be `text ? JSON.parse(text) : null`, one line above the
   * `!res.ok` branch. So any error response that was not JSON threw a raw
   * `SyntaxError` HERE, and the careful message below was unreachable for
   * exactly the failures a person most needs explained.
   *
   * It is not a hypothetical shape. Starlette's unhandled-exception response
   * is the nine bytes `Internal Server Error` as `text/plain`, and the proxy
   * relays the upstream content type faithfully. A member saw:
   *
   *     Unexpected token 'I', "Internal S"... is not valid JSON
   *
   * which names the parser rather than the fault, points at no server, and
   * suggests nothing to do. A 502 from a restarting gateway reads the same
   * way. Measured on production 2026-09-20, where a stuck deploy loop was
   * restarting the gateway every five minutes.
   */
  const parsed = readJsonBody(text);
  const body = parsed.value;

  if (!res.ok) {
    throw new ProjectsApiError(
      detailText(body?.detail) || describeFailure(res.status, text),
      res.status,
      body?.detail
    );
  }

  // A 2xx whose body is not JSON is still broken, and silently returning the
  // raw text as `T` would hand a string to code expecting rows — a crash one
  // frame later, blaming the wrong place.
  if (!parsed.ok) {
    throw new ProjectsApiError(
      `The server answered ${res.status} with a reply this app could not read.`,
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
  if (method !== "GET" && !isReadOnlyPost(method, path)) {
    invalidate(PROJECTS_CACHE);
  }

  return body as T;
}

/**
 * A POST that writes nothing: a preview. It drops no cached read.
 *
 * WS-27bn R1. The report builder previews on each change. While a preview
 * counted as a write, each one dropped every cached Projects read, and every
 * mounted lens read the server again for no change. The chat manifest's
 * `READ_ONLY_POSTS` holds the same rule: each entry ends in `/preview`.
 */
export function isReadOnlyPost(method: string, path: string): boolean {
  if (method.toUpperCase() !== "POST") return false;
  return path.split("?")[0].endsWith("/preview");
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
   * §9.12.7 analytics. `nodeId` omitted means the PORTFOLIO.
   *
   * ⚠️ These return finished numbers. Do not post-process them into other
   * numbers in the browser — see the note on `StuckReport`.
   */
  stuck: (nodeId?: string) =>
    call<StuckReport>(`analytics/stuck${scopeQuery(nodeId)}`),
  load: (nodeId?: string) =>
    call<LoadReport>(`analytics/load${scopeQuery(nodeId)}`),
  throughput: (nodeId?: string, weeks?: number) =>
    call<ThroughputReport>(
      `analytics/throughput${scopeQuery(nodeId, weeks)}`
    ),
  finished: (nodeId?: string, weeks?: number) =>
    call<FinishedReport>(`analytics/finished${scopeQuery(nodeId, weeks)}`),
  outlook: (nodeId?: string) =>
    call<OutlookReport>(`analytics/outlook${scopeQuery(nodeId)}`),
  /** WS-27bm S7a. The server's default horizon (14 days) unless one is named. */
  capacity: (nodeId?: string, horizonDays?: number) => {
    const scope = scopeQuery(nodeId);
    const horizon = horizonDays ? `horizon_days=${horizonDays}` : "";
    const query = horizon ? (scope ? `${scope}&${horizon}` : `?${horizon}`) : scope;
    return call<CapacityReport>(`analytics/capacity${query}`);
  },
  /** WS-27bm S7c. The server's default horizon (14 days) unless one is named. */
  conflicts: (nodeId?: string, horizonDays?: number) => {
    const scope = scopeQuery(nodeId);
    const horizon = horizonDays ? `horizon_days=${horizonDays}` : "";
    const query = horizon ? (scope ? `${scope}&${horizon}` : `?${horizon}`) : scope;
    return call<ConflictsReport>(`analytics/conflicts${query}`);
  },

  /** §9.12.8 — saved report definitions, and the render of one. */
  reports: () => call<{ reports: ReportRow[] }>("reports"),
  /** WS-27bn R2. The template catalogue, in the server's order. */
  reportTemplates: () =>
    call<{ templates: ReportTemplate[] }>("reports/templates"),
  createReport: (body: {
    name: string;
    project_id?: string | null;
    config?: ReportConfig;
  }) =>
    call<ReportRow>("reports", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /**
   * WS-27bn R1. ⚠️ `config` is REPLACED on the server, never merged. Send
   * the whole config, or the fields left out go back to the defaults.
   */
  patchReport: (id: string, body: { name?: string; config?: ReportConfig }) =>
    call<ReportRow>(`reports/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  renderReport: (id: string) =>
    call<RenderedReportBody>(`reports/${id}/render`),
  /**
   * WS-27bn R1. Render a config that is not saved. The server writes no row
   * and computes every figure. The browser computes none.
   */
  previewReport: (body: {
    project_id: string | null;
    name: string;
    config: ReportConfig;
  }) =>
    call<PreviewReportBody>("reports/preview", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteReport: (id: string) =>
    call<void>(`reports/${id}`, { method: "DELETE" }),

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

  /**
   * The lanes this node USES, plus how many tasks sit in each.
   *
   * `counts` is keyed by status id and scoped to the projects this set
   * governs. It arrives with the lanes rather than from a second call because
   * every consumer needs both at once — the editor prints the number on the
   * row, and no delete can be offered safely without it.
   */
  statuses: (projectId: string) =>
    call<{
      rows: StatusRow[];
      total: number;
      counts: Record<string, number>;
      owner_id: string;
    }>(`nodes/${projectId}/statuses`),

  /**
   * ── Which set a project uses (migration 196) ───────────────────────────────
   *
   * A project uses the set of the NEAREST node at or above it that owns one.
   * A space always owns one, so the walk always ends. That is the whole model:
   * inherit means "own nothing", override means "own a set", switch back means
   * "stop owning", and copy means "own a set that started as a duplicate".
   */
  statusSet: (projectId: string) =>
    call<StatusSetInfo>(`nodes/${projectId}/status-set`),

  /**
   * What a switch WOULD move, without moving it.
   *
   * Read-only on purpose — the card is opened far more often than it is
   * confirmed, and a preview that wrote anything would make opening it a
   * decision.
   */
  previewStatusSet: (projectId: string, payload: StatusSetChange) =>
    call<StatusSetPreview>(`nodes/${projectId}/status-set/preview`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /**
   * Switch, and move the tasks, in ONE transaction.
   *
   * ⚠️ `mapping` travels as `{old status id: target lane NAME}`, not an id.
   * When the switch is a copy the destination lanes do not exist yet — the
   * human chooses before the rows are written — so an id is unavailable at
   * exactly the moment of the decision. Names are unique per set, so one wire
   * shape serves inherit, copy and a dormant set alike.
   */
  setStatusSet: (projectId: string, payload: StatusSetChange) =>
    call<StatusSetResult>(`nodes/${projectId}/status-set`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /**
   * ── The status vocabulary is WRITEABLE, and until now only from SQL ────────
   *
   * `admin.py` has shipped the full CRUD since migration 146, and this client
   * carried only the read above. So a member could not add, rename, recolour,
   * reorder or retire a lane without a database write, and
   * `project_management_app.md` §9.12.3 was describing a "status manager" that
   * had a server and no surface.
   *
   * ⚠️ Statuses used to be ROOT-scoped, and are not any more (migration 196,
   * owner directive 2026-09-06). Pass any node and the server resolves the
   * nearest node that OWNS a set — the root until something overrides.
   *
   * The old reason for refusing an override survives and is why the override
   * is safe: the category is the only vocabulary two spaces share, and every
   * cross-project number rests on it. Every lane in every set still carries
   * one, so only the NAMES became local.
   */
  createStatus: (projectId: string, payload: Record<string, unknown>) =>
    call<StatusRow>(`nodes/${projectId}/statuses`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /** Rename, recolour, re-categorise, reposition, or make default. */
  patchStatus: (statusId: string, payload: Record<string, unknown>) =>
    call<StatusRow>(`statuses/${statusId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  /**
   * Retire a lane. **409 while any task still sits in it**, and the message
   * names the count — `pm_tasks.status_id` is `ON DELETE RESTRICT`, so the
   * alternative was an opaque 500. The editor shows that sentence verbatim
   * rather than "could not delete": the number is what tells the owner whether
   * to move three tasks or reconsider.
   */
  /**
   * `moveTo` is the answer the 409 above only asked for. Without it the
   * refusal stands, so a caller that has not adopted it behaves as before.
   */
  deleteStatus: (statusId: string, moveTo?: string) =>
    call<{ deleted: string; tasks_affected: number }>(
      `statuses/${statusId}${moveTo ? `?move_to=${moveTo}` : ""}`,
      { method: "DELETE" }
    ),

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
      /** How many tasks in scope carry no dates at all. Always the TRUE total. */
      undated: number;
      /**
       * P-22 — the undated tasks themselves, for the TIMELINE only.
       *
       * Empty unless `include_undated`, and always present, on the same ruling
       * as `links`. The calendar never asks: a cell is a day, and a task with
       * no day has nowhere to be drawn there.
       *
       * ⚠️ Capped separately from `rows`, so `undated` can exceed this list's
       * length. The count is the truth; this is as much of it as one screen
       * can hold.
       */
      unscheduled: TaskRow[];
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

  /**
   * The largest page the gateway will serve, and therefore the largest this
   * client may ask for.
   *
   * 🔴 **This is mirrored from Python, and asking for more is a 422 that
   * empties the surface.** `core.py::MAX_PAGE_SIZE` is 100, and `Page` binds
   * `page_size` as `Query(50, ge=1, le=MAX_PAGE_SIZE)`. FastAPI refuses the
   * request before the handler runs, so the panel does not get a short list
   * — it gets nothing, and draws "No comments yet" over a task full of them.
   *
   * That shipped in the first draft of this feature and no test saw it: the
   * live harness builds `Page` by hand and calls the handler directly, and
   * the visual rig stubs the API and answers 200 whatever the query says.
   * `tests/unit/test_projects_timeline_page_size.py` reads THIS line and
   * compares it to the Python constant, which is the only fence that spans
   * the two.
   *
   * ⚠️ A thread longer than this loses its older roots off the page, and
   * `threadComments` promotes the orphaned replies to top level. That is the
   * honest degradation and it is deliberate; paging a thread as a unit would
   * mean ordering by root, which breaks a timeline's one promise.
   */
  MAX_TIMELINE_PAGE: 100,

  /**
   * One task's activity stream, newest first.
   *
   * `kind` narrows it to what people WROTE or to what HAPPENED — the panel
   * reads the two separately (owner request, 2026-09-21), and each list has
   * to paginate on its own. Filtering one `all` page in the browser would
   * make "show N older" promise rows of the wrong kind. Omitted, it is the
   * whole stream, which is what every caller before today asked for.
   *
   * `pageSize` exists so the comments list can ask for more than the default
   * 50 without a second endpoint. ⚠️ It cannot exceed
   * {@link MAX_TIMELINE_PAGE} — see that constant.
   */
  timeline: (
    taskId: string,
    opts: { kind?: "all" | "comments" | "events"; pageSize?: number } = {},
  ) => {
    const query = new URLSearchParams();
    if (opts.kind && opts.kind !== "all") query.set("kind", opts.kind);
    if (opts.pageSize) query.set("page_size", String(opts.pageSize));
    const suffix = query.toString() ? `?${query}` : "";
    return call<{ rows: ActivityRow[]; total: number }>(
      `tasks/${taskId}/timeline${suffix}`,
    );
  },

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
   * WS-27bl §9.13 — what a move WOULD do. Writes nothing.
   *
   * ⚠️ Always call this before `moveTasks`. The server resolves both
   * vocabularies and only it can see them at once; the card renders what it
   * says rather than guessing, so "what you were shown" and "what happened"
   * are one computation.
   */
  previewMove: (payload: MoveRequest) =>
    call<MovePlan>("tasks/move/preview", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  moveTasks: (payload: MoveRequest) =>
    call<MoveResult>("tasks/move", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /**
   * H-8 — the unrecoverable cascade, given a control at last.
   *
   * ⚠️ Every caller must go through `DeleteProjectDialog`. There is no
   * confirmation on this side and none on the server's: the route deletes what
   * it is given. The dialog is the whole guard.
   */
  deleteProject: (projectId: string) =>
    call<DeleteProjectResult>(`nodes/${projectId}`, { method: "DELETE" }),

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

  /**
   * "Suggested" in the task panel's picker (WS-27bm S7b, projects_ai_chat.md
   * §13.4). At most three people ranked FOR THIS TASK by the server. Without
   * `admin:members:read` the answer says `hr_visible: false` and carries no
   * `candidates` key, and the picker then shows no heading.
   */
  taskCandidates: (taskId: string) =>
    call<import("./candidates").CandidatesResponse>(`tasks/${taskId}/candidates`),

  createTask: (payload: Record<string, unknown>) =>
    call<TaskRow>("tasks", { method: "POST", body: JSON.stringify(payload) }),

  /**
   * Assignee addresses → the names people read. Unknown ones are absent.
   *
   * `pm_tasks` stores an assignee as an ADDRESS, so a task read carries no
   * name and every surface fell back to the local part. Owner reported it
   * on 2026-09-21: `priya` reads as a name, `p.sharma` does not.
   */
  personNames: (emails: readonly string[]) =>
    call<{ names: Record<string, string> }>(
      `people/names?emails=${encodeURIComponent(emails.join(","))}`,
    ),

  patchTask: (taskId: string, payload: Record<string, unknown>) =>
    call<TaskRow>(`tasks/${taskId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  /**
   * File a task out of every default list, board, calendar and search.
   *
   * **Any status.** Archive is a shelf — hidden now, reversible, may come
   * back — so it does not require an outcome first. The category guard that
   * refused open tasks was removed on 2026-09-21 (owner ruling); the gateway's
   * `archive_note` carries the reasoning.
   *
   * ⚠️ An archived task leaves the CURRENT-state analytics (open counts,
   * overdue, the forecast) while staying in the historical ones. That is
   * correct for a shelf and is why the archive has a view of its own.
   */
  archiveTask: (taskId: string) =>
    call<TaskRow>(`tasks/${taskId}/archive`, { method: "POST" }),

  /** Bring one back. No guard in this direction. */
  unarchiveTask: (taskId: string) =>
    call<TaskRow>(`tasks/${taskId}/unarchive`, { method: "POST" }),

  /**
   * Delete a task for good, and say what went with it.
   *
   * ⚠️ Subtasks are PROMOTED, not destroyed — `parent_task_id` SET NULLs — so
   * `subtasks_promoted` is not a cascade count but its opposite. Reporting it
   * as deleted would reassure in the wrong direction.
   */
  deleteTask: (taskId: string) =>
    call<{
      deleted: string;
      cascaded: {
        activities: number;
        assignees: number;
        links: number;
        subtasks_promoted: number;
      };
    }>(`tasks/${taskId}`, { method: "DELETE" }),

  setAssignees: (taskId: string, assignees: string[]) =>
    call<{ task_id: string; assignees: string[] }>(`tasks/${taskId}/assignees`, {
      method: "PUT",
      body: JSON.stringify({ assignees }),
    }),

  /**
   * Post a comment, or — with `parentId` — a reply to one.
   *
   * The gateway refuses a reply to a reply (422), a reply to a system event,
   * and a reply across tasks. The panel only ever offers Reply on a root, so
   * those refusals are a contract rather than a flow anybody meets.
   */
  comment: (taskId: string, body: string, parentId?: string) =>
    call<ActivityRow>(`tasks/${taskId}/comments`, {
      method: "POST",
      body: JSON.stringify(
        parentId ? { body, parent_id: parentId } : { body },
      ),
    }),

  /**
   * Fold `sources` into `targetId`. The target is the task that SURVIVES.
   *
   * ⚠️ One way, and re-running it does not undo it. Every source's comments,
   * history, attachments, assignees, tags, watchers and subtasks move to the
   * target, and each source becomes an archived stub pointing at it — which
   * is why every caller opens a card first and never merges on a click.
   *
   * The gateway refuses a cross-project merge, a merge into a stub, and a
   * task already merged. Same-project only is the owner's ruling of
   * 2026-09-21: a cross-project merge is a Move plus a merge, and `/move`
   * already owns the status remapping.
   */
  mergeTasks: (targetId: string, sources: readonly string[]) =>
    call<TaskRow & { merged: string[] }>(`tasks/${targetId}/merge`, {
      method: "POST",
      body: JSON.stringify({ sources }),
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

  /**
   * WS-27bh — the EFFECTIVE task types for a node: org-wide ∪ root-local,
   * with a root-local name shadowing an org-wide one (WS-27bj).
   *
   * The endpoint has existed since WS-27bj and nothing called it. A card can
   * draw `type_id` only by resolving it, and this is the registry that does.
   */
  types: (projectId: string) =>
    call<{ rows: TaskTypeRow[]; total: number }>(`nodes/${projectId}/types`),

  createTag: (projectId: string, payload: Record<string, unknown>) =>
    call<TagRow>(`nodes/${projectId}/tags`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /** Rename or recolour. A rename rewrites every task wearing the tag. */
  /**
   * How much a rename would rewrite, BEFORE it is asked for (D-PM-33).
   *
   * Shaped like `moveTasksPreview` rather than reading the count out of the
   * write's response, where it would arrive after the decision it informs.
   */
  tagImpact: (tagId: string) =>
    call<{
      tag: string;
      scope: "organization" | "project";
      tasks: number;
      projects: number;
    }>(`tags/${tagId}/impact`),

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
 * Watching a PROJECT (WS-27bk §9.12.2(b)).
 *
 * The same three verbs against a node, because a member who has learned the
 * task toggle has learned this one.
 *
 * ⚠️ `inherited` is what stops the toggle from lying. Watching a parent
 * already covers this project, so a control that read "not watching" would
 * invite a second subscription that changes nothing. The server resolves the
 * ancestor chain; the surface never walks it.
 */
export type ProjectWatchState = {
  project_id: string;
  watchers: string[];
  watching: boolean;
  inherited: boolean;
};

export const projectWatchersApi = {
  get: (projectId: string) =>
    call<ProjectWatchState>(`nodes/${projectId}/watchers`),

  watch: (projectId: string) =>
    call<{ project_id: string; watching: boolean }>(
      `nodes/${projectId}/watch`,
      { method: "PUT" }
    ),

  unwatch: (projectId: string) =>
    call<{ project_id: string; watching: boolean }>(
      `nodes/${projectId}/watch`,
      { method: "DELETE" }
    ),
};

/**
 * ⚠️ `importApi` was REMOVED 2026-08-24 (D52, board WS-39 S1) along with both
 * gateway endpoints. Metorite is the project-management system of record, so
 * there is nothing to import from. Do not re-add a client here — the
 * old task store's move into `pm_*` (D53) was a backfill migration, not an API.
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

