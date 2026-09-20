/**
 * People Center · the browser's client for /api/people/*.
 *
 * Reads, plus the **self-service** write door WS-28g added (`PATCH /people/{id}`
 * and the CV upload). The ADMIN write path is unchanged and still goes through
 * `./write.ts` to `/api/tasks/people` — see that file's header for why the two
 * doors exist and why there is still only one write implementation behind them.
 *
 * The reads carry `can_manage`, `is_self` and `editable_fields`. Whether the
 * caller may write, and *which fields*, is a fact only the gateway can answer,
 * and the editor has to know it before it draws rather than discover it from a
 * 403 after the click (D-PC-4).
 */

import { describeFailure, detailText, readJsonBody } from "@/lib/apiError";

/** The §3.1 half of a person that every `feature:people` holder may read. */
export interface PersonProfileFields {
  preferred_name?: string | null;
  pronouns?: string | null;
  /** A `data:image/jpeg` URI of the server's 256×256 re-encode (D-PC-17). */
  avatar?: string | null;
  avatar_updated_at?: string | null;
  location?: string | null;
  timezone?: string | null;
  working_hours?: Record<string, unknown> | null;
  bio?: string | null;
  links?: Record<string, string> | null;
  languages?: string[];
  /** HR tier (§3.2) — null-projected without `admin:members:read` or self. */
  employee_id?: string | null;
  employment_type?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  seniority?: string | null;
  cost_center?: string | null;
  interests?: string[];
  max_concurrent_tasks?: number | null;
  /** Private tier (§3.5) — self or `admin:members:manage` only (D-PC-3). */
  phone?: string | null;
  emergency_contact?: Record<string, string> | null;
  personal_email?: string | null;
  /** `MM-DD`. Never a date of birth (D-PC-9). */
  birthday?: string | null;
}

export interface Absence {
  id: string;
  starts_on: string;
  ends_on: string;
  /** `away` | `holiday` | `partial` — three words, and no fourth (D-PC-7). */
  kind: string;
  hours_per_day?: number | null;
  note?: string | null;
  created_by?: string;
}

export interface PersonRow extends PersonProfileFields {
  id: string;
  /**
   * WS-28k, directory tier. Present on the LIST too — resolved in one query
   * for the page, because "do not chase them, they are away" is the most
   * useful thing on a directory row and an N+1 would make it the most
   * expensive.
   */
  away?: { kind: string; until: string } | null;
  name: string;
  email?: string | null;
  role?: string | null;
  title?: string | null;
  department?: string | null;
  team?: string | null;
  status: string;
  manager_id?: string | null;
  /** Null-projected without `admin:members:read` — see `hrVisible`. */
  skills?: string[];
  skills_source?: Record<string, string>;
  resume_summary?: string | null;
  years_experience?: number | null;
  capacity_hours_per_week?: number | null;
  current_load_hours_per_week?: number | null;
  available_hours_per_week?: number | null;
}

export interface PersonDetail extends PersonRow {
  has_login: boolean;
  email_conflict?: string | null;
  manager?: string | null;
  /** The ClickUp link — read under this name, written as `clickup_user_id`. */
  provider_user_id?: string | null;
  hr_visible: boolean;
  can_manage: boolean;
  /** True when this row is the caller's own (D-PC-1). */
  is_self?: boolean;
  /**
   * WS-28k. `away` is DIRECTORY tier — "do not expect a reply this week" is the
   * one thing a colleague most needs before chasing somebody — while the SPANS
   * and the hours are HR tier, because when and why somebody is off is capacity
   * information.
   */
  absences?: Absence[];
  hours_available_this_week?: number | null;
  /**
   * The effective work schedule (WS-28p) — org policy with this person's
   * override applied, `source` naming which layer decided each field. Computed
   * by the server; the client never recombines the two halves.
   */
  schedule?: import("./schedule").EffectiveSchedule | null;
  /** Derived from that schedule (D-PC-18). Null without the HR tier. */
  contracted_hours_per_week?: number | null;
  /** Set when the hand-typed capacity disagrees with the derived figure. */
  capacity_conflict?: number | null;
  /**
   * Which fields THIS caller may write on THIS row — the server's answer, and
   * the only one the UI is allowed to have. An empty array means read-only,
   * and read-only means the controls are ABSENT, never disabled.
   */
  editable_fields?: string[];
  /**
   * WS-28h — the structured capability half (§3.3), HR tier. `skills_detail`
   * enriches the flat chips with level/years/recency; `credentials` is the
   * "is this person actually qualified to sign this off" history. Both empty
   * for a caller outside the tier — and `hr_visible` already says which
   * emptiness this is.
   */
  skills_detail?: import("./skills").SkillDetail[];
  credentials?: import("./skills").Credential[];
  load?: {
    open_tasks: number;
    estimated_hours: number;
    unestimated: number;
  } | null;
}

export interface WorkRow {
  id: string;
  title: string;
  task_number?: number | null;
  due_at?: string | null;
  project_id?: string | null;
  project_name?: string | null;
  status_name: string;
  status_category: string;
}

export class PeopleApiError extends Error {
  constructor(
    message: string,
    readonly status: number
  ) {
    super(message);
    this.name = "PeopleApiError";
  }
}

async function call<T>(path: string): Promise<T> {
  // `path` is "" for the directory LIST, and "?q=…" when it carries filters.
  // Both must reach `/api/people` with NO trailing slash: the proxy route is
  // an optional catch-all, and Next 308-redirects `/api/people/` before the
  // route is ever consulted. Gluing the slash on unconditionally is the bug
  // that made the list answer with a 404 HTML page for its whole life.
  const suffix = path === "" || path.startsWith("?") ? path : `/${path}`;
  const res = await fetch(`/api/people${suffix}`);
  const text = await res.text();
  const body = readJsonBody(text).value;
  if (!res.ok) {
    throw new PeopleApiError(
      detailText(body?.detail) || describeFailure(res.status, text),
      res.status
    );
  }
  return body as T;
}

export const peopleApi = {
  directory: (params: Record<string, string | boolean | undefined> = {}) => {
    const qs = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "" && value !== false) {
        qs.set(key, String(value));
      }
    }
    const query = qs.toString();
    return call<{
      rows: PersonRow[];
      total: number;
      hr_visible: boolean;
      can_manage: boolean;
      /** Which row is the caller's, if any — the link target for "my profile". */
      self_person_id?: string | null;
    }>(`${query ? `?${query}` : ""}`);
  },

  /** The company's working week, plus whether this caller may edit it. */
  schedule: () =>
    call<{
      policy: import("./schedule").WorkPolicy;
      defaults: import("./schedule").WorkPolicy;
      contracted_hours_per_week: number;
      can_manage: boolean;
      updated_by?: string | null;
      updated_at?: string | null;
    }>("schedule"),

  /**
   * Save the policy — or, with `dryRun`, ask what it WOULD move and write
   * nothing. The settings page previews before it applies, because this figure
   * is the denominator of every load bar in the org.
   */
  saveSchedule: async (
    policy: import("./schedule").WorkPolicy,
    dryRun: boolean
  ) => {
    const res = await fetch("/api/people/schedule", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ policy, dry_run: dryRun }),
    });
    const text = await res.text();
    const parsed = readJsonBody(text).value;
    if (!res.ok) {
      throw new PeopleApiError(
        detailText(parsed?.detail) || describeFailure(res.status, text),
        res.status
      );
    }
    return parsed as {
      policy: import("./schedule").WorkPolicy;
      impact: import("./schedule").PolicyImpact;
      saved: boolean;
    };
  },

  /**
   * Set a display image. `target` is `"me"` or a person id — different
   * endpoints, because the self door is ungated (D-PC-15).
   *
   * The crop is **fractions of the source**, and the server squares and
   * resizes whatever it receives either way (D-PC-17), so this is a request
   * about framing rather than a control over shape.
   */
  uploadAvatar: async (
    target: string,
    file: File,
    crop: { x: number; y: number; size: number }
  ) => {
    const form = new FormData();
    form.append("file", file);
    form.append("crop_x", String(crop.x));
    form.append("crop_y", String(crop.y));
    form.append("crop_size", String(crop.size));
    const res = await fetch(`/api/people/${target}/avatar`, {
      method: "POST",
      body: form,
    });
    const text = await res.text();
    const parsed = readJsonBody(text).value;
    if (!res.ok) {
      // The gateway refuses a file with a sentence about that file — shown
      // verbatim, because the person who chose it is the one who has to
      // choose another.
      throw new PeopleApiError(
        detailText(parsed?.detail) || describeFailure(res.status, text),
        res.status
      );
    }
    return parsed as PersonDetail;
  },

  removeAvatar: async (target: string) => {
    const res = await fetch(`/api/people/${target}/avatar`, { method: "DELETE" });
    const text = await res.text();
    const parsed = readJsonBody(text).value;
    if (!res.ok) {
      throw new PeopleApiError(
        detailText(parsed?.detail) || describeFailure(res.status, text),
        res.status
      );
    }
    return parsed as PersonDetail;
  },

  /**
   * Record when you are away. Self-writable — requiring an admin to type it is
   * how the data ends up missing, and then every capacity figure that reads it
   * is quietly wrong (§5.8).
   */
  addAbsence: async (target: string, body: Omit<Absence, "id">) => {
    const res = await fetch(`/api/people/${target}/absences`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const text = await res.text();
    const parsed = readJsonBody(text).value;
    if (!res.ok) {
      throw new PeopleApiError(
        detailText(parsed?.detail) || describeFailure(res.status, text),
        res.status
      );
    }
    return parsed as Absence;
  },

  removeAbsence: async (target: string, absenceId: string) => {
    const res = await fetch(`/api/people/${target}/absences/${absenceId}`, {
      method: "DELETE",
    });
    if (!res.ok) {
      const text = await res.text();
      const parsed = readJsonBody(text).value;
      throw new PeopleApiError(
        detailText(parsed?.detail) || describeFailure(res.status, text),
        res.status
      );
    }
  },

  /**
   * The people-management dashboard rows (§5.7).
   *
   * 403 without `admin:members:read` — it is skills, capacity and hours for
   * everybody at once, so the gate is on the whole surface rather than on a
   * clause. The caller renders the refusal's own sentence: there is no useful
   * half of this page to draw for somebody who may not see the HR tier.
   */
  dashboard: () => call<import("./dashboard").DashboardResponse>("dashboard"),

  facets: () =>
    call<{
      departments: Array<{ department: string; total: number }>;
      teams: Array<{ department: string; team: string; total: number }>;
      statuses: string[];
    }>("facets"),

  person: (id: string) => call<PersonDetail>(id),

  /**
   * "Who can help with…" (§5.5). The ranking is the server's — deterministic,
   * every signal carrying its own points — and 403s without
   * `admin:members:read`, whose sentence renders verbatim.
   */
  capabilitySearch: (q: string) =>
    call<import("./search").CapabilityResponse>(
      `search?q=${encodeURIComponent(q)}`
    ),

  /**
   * The structured capability payload: rows + credentials + the VOCABULARIES
   * (levels, kinds), so the editor renders its selects without a client-side
   * copy that could drift — the D-PC-4 shape, applied to a word list.
   * `target` is `"me"` or a person id: different doors, one implementation.
   */
  skills: (target: string) =>
    call<{
      skills: import("./skills").SkillDetail[];
      credentials: import("./skills").Credential[];
      levels: string[];
      evidence: string[];
      credential_kinds: string[];
    }>(`${target}/skills`),

  /**
   * Replace the skills, whole. The server refuses a bad row BY NAME and
   * applies nothing (D-PC-5); the sentence is shown verbatim. Every save
   * rewrites the flat `skills[]` projection in the same transaction (D-PC-6),
   * so the chips everywhere else update with it.
   */
  saveSkills: async (
    target: string,
    rows: import("./skills").SkillDetail[]
  ) => {
    const res = await fetch(`/api/people/${target}/skills`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows }),
    });
    const text = await res.text();
    const parsed = readJsonBody(text).value;
    if (!res.ok) {
      throw new PeopleApiError(
        detailText(parsed?.detail) || describeFailure(res.status, text),
        res.status
      );
    }
    return parsed as {
      skills: import("./skills").SkillDetail[];
      credentials: import("./skills").Credential[];
    };
  },

  saveCredentials: async (
    target: string,
    rows: import("./skills").Credential[]
  ) => {
    const res = await fetch(`/api/people/${target}/credentials`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows }),
    });
    const text = await res.text();
    const parsed = readJsonBody(text).value;
    if (!res.ok) {
      throw new PeopleApiError(
        detailText(parsed?.detail) || describeFailure(res.status, text),
        res.status
      );
    }
    return parsed as {
      skills: import("./skills").SkillDetail[];
      credentials: import("./skills").Credential[];
    };
  },

  /**
   * The caller's own row — or WHY there isn't one (§5.3).
   *
   * Three states, kept apart deliberately: `no_directory_row` (nobody carries
   * your address; an admin can fix it) and `no_identity` (you are signed in
   * without an address) are different problems with different fixes, and an
   * empty form would report neither.
   */
  me: () =>
    call<{
      state: "resolved" | "no_directory_row" | "no_identity";
      email?: string | null;
      person?: PersonDetail | null;
      detail?: string | null;
    }>("me"),

  /**
   * Save a person through the SELF-SERVICE door.
   *
   * Sends only the keys the caller changed. The gateway answers 403 naming any
   * field outside their write classes and applies NOTHING (D-PC-5) — so a
   * partial save is impossible, and the message is shown verbatim because it is
   * the only form of it anyone can act on.
   *
   * `target` is the literal string `"me"` for your own row, or a person id for
   * somebody else's. They are DIFFERENT endpoints, not a convenience alias:
   * `/people/me` is served by a router with no feature gate, because the
   * directory is gated and your own row is not (D-PC-15). Callers pick by
   * `is_self`, so a colleague with no `feature:people` still saves.
   */
  update: async (target: string, body: Record<string, unknown>) => {
    const res = await fetch(`/api/people/${target}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const text = await res.text();
    const parsed = readJsonBody(text).value;
    if (!res.ok) {
      throw new PeopleApiError(
        detailText(parsed?.detail) || describeFailure(res.status, text),
        res.status
      );
    }
    return parsed as PersonDetail;
  },

  /**
   * Upload one's own CV. No `Content-Type` header — the browser sets it so the
   * multipart boundary matches the body it generated, and the proxy forwards
   * both unchanged.
   */
  uploadResume: async (target: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`/api/people/${target}/resume`, {
      method: "POST",
      body: form,
    });
    const text = await res.text();
    const parsed = readJsonBody(text).value;
    if (!res.ok) {
      throw new PeopleApiError(
        detailText(parsed?.detail) || describeFailure(res.status, text),
        res.status
      );
    }
    return parsed as {
      resume_id: string;
      added_skills: string[];
      person: PersonDetail;
    };
  },

  /**
   * WS-28j3 — the confirmed assign at the end of a suggestion (§5.7.4).
   *
   * NOT a second write path: these hit the Projects app's own endpoints
   * through its own proxy — the same GET the panel uses to read the task and
   * the same assignees PUT that replaces the set and emits
   * `pm.task.assigned`. The helper exists because the suggestion lives on a
   * People surface and runtime cross-app imports are not a thing this tree
   * does; the ENDPOINT is the seam, and there is exactly one.
   *
   * The PUT replaces the whole set, so the existing assignees ride along —
   * helping with a task must not silently unassign its holder.
   */
  assignHelper: async (taskId: string, helper: string) => {
    const read = await fetch(`/api/projects/tasks/${taskId}`);
    const task = read.ok ? await read.json() : null;
    const current: string[] = (task?.assignees ?? []).map((a: string) =>
      a.trim().toLowerCase()
    );
    const wanted = helper.trim().toLowerCase();
    if (current.includes(wanted)) return;
    const res = await fetch(`/api/projects/tasks/${taskId}/assignees`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ assignees: [...current, wanted] }),
    });
    if (!res.ok) {
      const text = await res.text();
      const parsed = readJsonBody(text).value;
      throw new PeopleApiError(
        detailText(parsed?.detail) || describeFailure(res.status, text),
        res.status
      );
    }
  },

  suggestions: () =>
    call<import("./suggestions").SuggestionsResponse>(
      "dashboard/suggestions"
    ),

  quality: () => call<import("./quality").QualityResponse>("quality"),

  overview: () => call<import("./overview").OverviewResponse>("overview"),

  chart: () => call<import("./chart").ChartResponse>("chart"),

  work: (id: string) =>
    call<{ rows: WorkRow[]; total: number; available: boolean }>(`${id}/work`),
};
