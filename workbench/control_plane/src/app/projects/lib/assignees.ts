/**
 * Projects · the assignee vocabulary, client side.
 *
 * Spec: `project-docs/specs/project_management_app.md` §3.7 · **D-PM-4**.
 *
 * An assignee is an **email or `agent:<name>`** — one vocabulary for both
 * species. That is what makes handing work to an agent the same action as
 * handing it to a colleague, rather than a parallel feature with its own field,
 * its own picker and its own permission story.
 *
 * These functions mirror the server's normalisation (`tasks.py::set_assignees`
 * strips and lowercases) so the chips a member sees before saving match the set
 * that comes back after. They deliberately do **not** reject anything the
 * server would accept: inventing a client-side rule the API does not enforce is
 * how a UI starts refusing valid data.
 */

export type AssigneeKind = "person" | "agent" | "unknown";

/** The prefix that makes an actor an agent. Lowercased by {@link normalize}. */
export const AGENT_PREFIX = "agent:";

/**
 * One assignee, normalised the way the server stores it.
 *
 * Lowercased because `pm_task_assignees` holds one row per `(task, assignee)`
 * and the API lowercases on write — leaving case here would show a member
 * "Priya@…" and "priya@…" as two people until the next reload proved otherwise.
 */
export function normalize(raw: string): string {
  return raw.trim().toLowerCase();
}

/**
 * What kind of actor a token names.
 *
 * `unknown` is a **hint, not a rejection** — the server accepts any non-empty
 * string, so the UI's job is to say "that does not look like an email or an
 * agent" and let the member decide. A typo'd address that silently assigns
 * work to nobody is the failure worth surfacing.
 */
export function classify(assignee: string): AssigneeKind {
  const value = normalize(assignee);
  if (value.startsWith(AGENT_PREFIX)) {
    return value.length > AGENT_PREFIX.length ? "agent" : "unknown";
  }
  // Deliberately loose: something@something.something. A stricter regex would
  // reject addresses that are legal and deliverable.
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) ? "person" : "unknown";
}

/**
 * Turn typed text into the assignee set to send.
 *
 * Splits on commas, semicolons and newlines — never on spaces, because a
 * pasted list often arrives as `"Priya <priya@x.com>, Sam"` and splitting on
 * whitespace would shred it into tokens that assign work to nobody.
 *
 * Order is preserved and duplicates are dropped **after** normalisation, so
 * `Priya@x.com, priya@x.com` is one assignee rather than a set the server
 * silently collapses behind the member's back.
 */
export function parseAssignees(input: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of input.split(/[,;\n]/)) {
    const value = normalize(part);
    if (!value || seen.has(value)) continue;
    seen.add(value);
    out.push(value);
  }
  return out;
}

/**
 * Add one assignee to a set, or return the set unchanged.
 *
 * Returning the *same array* when nothing changed is deliberate: it lets a
 * caller skip a PUT that would emit a `pm.task.assigned` event for an assignee
 * who is already there — which WS-27f keys agent dispatch off, so a re-assert
 * would re-dispatch a run.
 */
export function withAssignee(current: readonly string[], raw: string): string[] {
  const value = normalize(raw);
  if (!value) return current as string[];
  const existing = current.map(normalize);
  if (existing.includes(value)) return current as string[];
  return [...existing, value];
}

/** Remove one assignee. Removing somebody absent is a no-op, not an error. */
export function withoutAssignee(
  current: readonly string[],
  raw: string
): string[] {
  const value = normalize(raw);
  return current.map(normalize).filter((a) => a !== value);
}

/** The label for a chip: an agent shows its name, a person their address. */
export function assigneeLabel(assignee: string): string {
  const value = normalize(assignee);
  return value.startsWith(AGENT_PREFIX) ? value.slice(AGENT_PREFIX.length) : value;
}

// ── WS-28e: the directory-backed picker (people_center_app.md §6.1) ─────────

/** One suggestion row — a person or an agent, one vocabulary (D-PM-4). */
export interface PickerRow {
  /** The value the task stores: an email, or `agent:<name>`. */
  assignee: string;
  name: string;
  kind: "person" | "agent" | string;
  title?: string | null;
  department?: string | null;
  avatar?: string | null;
  /** False = directory-only (D-PC-12): can hold the task, cannot sign in. */
  has_login: boolean;
  away?: { kind: string; until: string } | null;
  /** HR tier — absent without `admin:members:read`. */
  top_skills: string[];
  load?: {
    open_tasks: number;
    estimated_hours: number;
    unestimated: number;
  } | null;
  contracted_hours?: number | null;
  /** §6.1's line: why this is or is not a good idea right now. Shown, never
   * enforced — the assigner knows things the record does not. */
  warnings: string[];
  description?: string | null;
}

export interface PickerResponse {
  people: PickerRow[];
  agents: PickerRow[];
  hr_visible: boolean;
}

/**
 * The one line under a suggestion: load when visible, "no login" when true,
 * warnings always. Empty string when there is nothing to say — a row with a
 * blank subtitle beats one reading "undefined".
 */
/**
 * The picker's two groups, from whatever the suggest endpoint returned.
 *
 * 🔴 **Defensive on purpose, and the reason is a measured crash.** The
 * component built these inline as `res.people` / `res.agents` and then read
 * `.length`. A response missing either key — an older server, a proxied
 * error page, a shape change — threw, the throw escaped to the layout
 * boundary, and **the whole task panel rendered empty**. A suggestion list
 * is a convenience; its worst case must be "no suggestions", never "no
 * panel".
 *
 * An empty group is dropped rather than drawn as a heading with nothing
 * under it.
 */
export function pickerGroups(
  res: PickerResponse | null,
): Array<{ heading: string; rows: PickerRow[] }> {
  if (!res) return [];
  return [
    { heading: "People", rows: Array.isArray(res.people) ? res.people : [] },
    { heading: "Agents", rows: Array.isArray(res.agents) ? res.agents : [] },
  ].filter((group) => group.rows.length > 0);
}


export function describePickerRow(row: PickerRow): string {
  const parts: string[] = [];
  if (!row.has_login && row.kind === "person") {
    // The task will not notify them — worth knowing BEFORE assigning, not
    // after a silent week (D-PC-12).
    parts.push("no login — cannot see the task");
  }
  if (row.load) {
    let load = `${row.load.open_tasks} open`;
    if (row.load.estimated_hours > 0 || row.contracted_hours) {
      load += ` · ${row.load.estimated_hours}h`;
      if (row.contracted_hours) load += ` of ${row.contracted_hours}h`;
    }
    if (row.load.unestimated > 0) {
      load += ` · ${row.load.unestimated} unestimated`;
    }
    parts.push(load);
  }
  parts.push(...row.warnings);
  return parts.join(" · ");
}
