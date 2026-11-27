// ── The Change history of the CRM set-up (WS-CRM CRM-M1) ───────────────────
//
// Spec: crm_conversational.md §8 CRM-M1. `GET /crm/change-history` gives each
// change of the CRM set-up, newest first: who made it, when, through what, a
// one-line summary, and the value before and after. This file turns one row
// into the words of the section. Pure, so the test holds it without a browser.
//
// ⚠️ The section shows no Undo in PR 1. The gateway route exists, and its R8
// fence holds it. A new UI action needs a chat tool (owner rule,
// 2026-11-01), and the chat Undo is CRM-M2. So the button lands with it.
//
// Words: no id, no HTTP code and no engine name reaches the screen.

/** One row of `GET /crm/change-history`. */
export interface ChangeRow {
  id: string;
  /** ISO 8601, in UTC. */
  at: string;
  actor_email: string;
  actor_name?: string | null;
  via: string;
  approved_by?: string | null;
  ladder_level: string;
  area: string;
  area_label: string;
  action: "create" | "change" | "delete";
  entity_id?: string | null;
  summary: string;
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
  mode?: string | null;
  reversible: boolean;
  undoes_id?: string | null;
  undone_by?: string | null;
  can_undo?: boolean;
}

/** The body of `GET /crm/change-history`, the list shape of the CRM. */
export interface ChangeList {
  rows: ChangeRow[];
  total: number;
}

/** The rows of one read. The route allows 100. */
export const CHANGE_PAGE_SIZE = 50;

export const CHANGE_HISTORY_TITLE = "Change history";
export const CHANGE_HISTORY_NOTE =
  "Each change to the CRM setup: who made it, when, and the value before and after.";
export const CHANGE_HISTORY_EMPTY =
  "No changes yet. A change to access, record names, stages, lost reasons, pipelines, saved views or business rules shows here.";
export const CHANGE_HISTORY_LOADING = "Loading the change history…";

/** Through what a change came, as a person reads it. */
const VIA_WORDS: Record<string, string> = {
  ui: "CRM settings",
  chat: "Chat",
  whatsapp: "WhatsApp",
  ai_proposal: "AI suggestion",
  system: "Metorite",
};

export function viaLabel(via: string): string {
  return VIA_WORDS[via] ?? "CRM settings";
}

/** Who made the change: the name in the directory, else the address. */
export function whoLabel(row: Pick<ChangeRow, "actor_name" | "actor_email">): string {
  return (row.actor_name || "").trim() || row.actor_email;
}

/** Each field of a change, as a person reads it. A key with no word is not shown. */
const FIELD_WORDS: Record<string, string> = {
  name: "Name",
  label: "Name",
  color: "Colour",
  ui_color: "Colour",
  position: "Place",
  ui_position: "Place",
  type: "Type",
  is_default: "Default",
  probability: "Win chance",
  ui_probability: "Win chance",
  required_fields: "Required fields",
  max_dwell_days: "Warning days",
  hidden: "Hidden",
  access: "Who can open the CRM",
  access_groups: "Groups",
  mode: "CRM setup",
  labels: "Record names",
  layout: "Layout",
  filters: "Filters",
  sort: "Sort",
  columns: "Columns",
  shared: "Shared",
  body: "Rule",
  scope: "For",
  stage: "Stage",
};

/** The fields that a create or a delete shows: the name of the thing. */
const NAME_KEYS = ["name", "label", "body", "stage"] as const;

const MODE_WORDS: Record<string, string> = { native: "Native", mirror: "Zoho mirror" };
const ACCESS_WORDS: Record<string, string> = {
  admins: "Admins only",
  everyone: "Everyone with CRM access",
  groups: "The chosen groups",
};
const SCOPE_WORDS: Record<string, string> = { org: "Everyone", me: "Only its author" };

/** The longest value one line shows. */
const VALUE_MAX = 80;

function cut(text: string): string {
  const one = text.split(/\s+/).join(" ").trim();
  return one.length > VALUE_MAX ? `${one.slice(0, VALUE_MAX - 1)}…` : one;
}

/** One value of a change in plain words. Empty reads as "None". */
export function valueText(key: string, value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return key === "mode" ? "Not chosen" : "None";
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (key === "mode" && typeof value === "string") return MODE_WORDS[value] ?? value;
  if (key === "access" && typeof value === "string") return ACCESS_WORDS[value] ?? value;
  if (key === "scope" && typeof value === "string") return SCOPE_WORDS[value] ?? value;
  if ((key === "probability" || key === "ui_probability") && typeof value === "number") {
    return `${value}%`;
  }
  if (key === "access_groups" && Array.isArray(value)) {
    return value.length ? cut(value.map((g) => String(g).replace(/^group:/, "")).join(", ")) : "None";
  }
  if (key === "labels" && typeof value === "object") {
    const names = Object.values(value as Record<string, { label?: string }>)
      .map((entry) => entry?.label)
      .filter(Boolean);
    return names.length ? cut(names.join(", ")) : "The default names";
  }
  if (Array.isArray(value)) {
    return value.length ? cut(value.map(String).join(", ")) : "None";
  }
  if (typeof value === "object") {
    const count = Object.keys(value as object).length;
    return count === 0 ? "None" : count === 1 ? "1 setting" : `${count} settings`;
  }
  return cut(String(value));
}

/**
 * One line of a change: the field, its old value and its new value. A create
 * has no old value, so `before` is null and the line shows only the new one.
 */
export interface ChangeLine {
  key: string;
  label: string;
  before: string | null;
  after: string;
}

/**
 * What one change did, field by field. A change shows each changed field. A
 * create and a delete show only the name of the thing, because a new stage
 * has ten fields and the person asked what was added.
 */
export function changeLines(row: ChangeRow): ChangeLine[] {
  const before = row.before ?? {};
  const after = row.after ?? {};
  const keys =
    row.action === "change"
      ? Object.keys(after)
      : NAME_KEYS.filter((k) => k in (row.action === "create" ? after : before));
  return keys
    .filter((key) => key in FIELD_WORDS)
    .map((key) => ({
      key,
      label: FIELD_WORDS[key],
      before: row.action === "create" ? null : valueText(key, before[key]),
      after: row.action === "delete" ? "Removed" : valueText(key, after[key]),
    }));
}

/** The state of a row, for its badge, or null for a plain change. */
export type ChangeState = "undone" | "undo" | "final";

export function changeState(row: ChangeRow): ChangeState | null {
  if (row.undone_by) return "undone";
  if (row.undoes_id) return "undo";
  if (!row.reversible) return "final";
  return null;
}

export const CHANGE_STATE_WORDS: Record<ChangeState, string> = {
  undone: "Undone",
  undo: "Undo of a change",
  final: "Cannot be undone",
};

/** What the section draws: one of three notices, or the rows. */
export type ListState = "loading" | "failed" | "empty" | "rows";

export function listState(res: { data?: ChangeList | null; loading: boolean; error: string | null }): ListState {
  if (res.data && res.data.rows.length > 0) return "rows";
  if (res.data) return "empty";
  if (res.error) return "failed";
  return "loading";
}

/** The line under the list when the history holds more than one read. */
export function moreLine(shown: number, total: number): string | null {
  if (total <= shown) return null;
  return `Showing the ${shown} newest of ${total} changes.`;
}
