/**
 * The fields of a confirmation card, as a member reads them.
 *
 * A gated tool sends its card body as `key: value` lines (`writes.py`
 * `_card_lines`, `guarded.py` `_guard_card`, the CRM card). The key is the
 * wire name (`tags_add`, `due_at`), and every member value is fenced in
 * «guillemets». The model never reads this text: the card goes to the
 * member only (`ask_tools.request_confirmation`). So the client owns how it
 * reads, and this module is the ONE place a card key becomes product words.
 *
 * Owner report, 2026-10-07: "tags_add → Bug", "Task 1: #28 «…»", and raw
 * marks on the card. So each key has:
 *
 * - **a label** in product words ("Add tags", "Due"), and
 * - **a kind**, which says how its value draws: a tag pill, a status chip, a
 *   task chip with its number muted, a person chip, a formatted date.
 *
 * ⚠️ **A new key needs a line in `CARD_FIELDS`.** Two fences hold that:
 * `cardFields.test.ts`, and the shared Python fakes
 * (`tests/unit/_projects_agent_fakes.py`, `_crm_agent_fakes.py`), which read
 * the keys out of THIS file and fail every card test that prints a key with
 * no line here. Do not mirror the map in Python. The fakes read it.
 *
 * Pure and framework-free. `components/CardFieldValue.tsx` draws the result.
 */

import { splitFenced, unfenced } from "@/lib/fencedText";

export type FieldKind =
  | "text"
  | "task"
  | "tag"
  | "status"
  | "category"
  | "person"
  | "project"
  | "date"
  | "minutes"
  | "flag"
  | "words";

export interface FieldSpec {
  label: string;
  kind?: FieldKind;
  /** Several values of this kind, one element each. */
  many?: boolean;
}

/**
 * Every key a card prints. A number in a key reads as `N` (`task 3` is
 * `task N`), and `field <Name>` is `field *`, a custom field by its name.
 *
 * ⚠️ Keep one key per line, quoted, as `"key": {`: the Python fakes parse
 * this literal. A `*_id` key whose value is a UUID is hidden whatever its
 * line says (`isHiddenField`), because a member cannot read an id.
 */
export const CARD_FIELDS: Record<string, FieldSpec> = {
  "N": { label: "Subtask" },
  "Note": { label: "Note" },
  "actual_end": { label: "Ended", kind: "date" },
  "actual_start": { label: "Started", kind: "date" },
  "after": { label: "After" },
  "amount": { label: "Amount" },
  "archive_after_months": { label: "Archive after (months)" },
  "archived": { label: "Archived", kind: "date" },
  "assignees": { label: "Assignees", kind: "person", many: true },
  "assignees_add": { label: "Assign", kind: "person", many: true },
  "assignees_remove": { label: "Unassign", kind: "person", many: true },
  "body": { label: "Text" },
  "capacity": { label: "Capacity" },
  "category": { label: "Category", kind: "category" },
  "close_after_months": { label: "Close after (months)" },
  "color": { label: "Colour", kind: "words" },
  "comment": { label: "Comment" },
  "config": { label: "Settings" },
  "contact": { label: "Contact" },
  "context": { label: "Context" },
  "copied from": { label: "Copied from", kind: "project" },
  "created_at": { label: "Created", kind: "date" },
  "current triage": { label: "Your triage now" },
  "deal": { label: "Deal" },
  "decision": { label: "Decision", kind: "words" },
  "deep_work": { label: "Deep work", kind: "flag" },
  "delegated_at": { label: "Delegated", kind: "date" },
  "dependency warnings": { label: "Order warnings" },
  "description": { label: "Description" },
  "disposition": { label: "Triage", kind: "words" },
  "dropped links": { label: "Links dropped" },
  "drops (values with no field in the destination)": { label: "Values dropped" },
  "due": { label: "Due", kind: "date" },
  "due_at": { label: "Due", kind: "date" },
  "duplicate of": { label: "Duplicate of", kind: "task" },
  "email": { label: "Email", kind: "person" },
  "energy": { label: "Energy", kind: "words" },
  "estimate_mins": { label: "Estimate", kind: "minutes" },
  "expected_by": { label: "Expected by", kind: "date" },
  "expected_close_date": { label: "Expected close", kind: "date" },
  "field": { label: "Field" },
  "field *": { label: "Field" },
  "field_type": { label: "Field type", kind: "words" },
  "file": { label: "File" },
  "filters": { label: "Filters" },
  "first due": { label: "First due" },
  "flexible": { label: "Flexible", kind: "flag" },
  "from": { label: "From", kind: "task" },
  "group by": { label: "Group by", kind: "words" },
  "icon": { label: "Icon" },
  "icon_slot": { label: "Icon colour" },
  "impact": { label: "Impact" },
  "important": { label: "Important", kind: "flag" },
  "into": { label: "Into", kind: "tag" },
  "is_default": { label: "Default", kind: "flag" },
  "is_epic": { label: "Epic", kind: "flag" },
  "is_hard_date": { label: "Hard date", kind: "flag" },
  "is_two_minute": { label: "Two-minute task", kind: "flag" },
  "kind": { label: "Kind", kind: "words" },
  "lands in": { label: "Lands in" },
  "lanes after": { label: "Lanes after", kind: "status", many: true },
  "lead": { label: "Lead" },
  "lead_name": { label: "Lead" },
  "leveraged": { label: "Leveraged", kind: "flag" },
  "link": { label: "Link" },
  "link_id": { label: "Link" },
  "link_type": { label: "Link type", kind: "words" },
  "links": { label: "Links" },
  "lost_reason_id": { label: "Lost reason" },
  "marks read": { label: "Marks read" },
  "mode": { label: "Mode", kind: "words" },
  "move to": { label: "Move to", kind: "status" },
  "moves": { label: "Moves" },
  "name": { label: "Name" },
  "next copy": { label: "Next copy" },
  "next_action": { label: "Next action" },
  "no longer the default": { label: "No longer the default" },
  "not in the directory": { label: "Not in the directory", kind: "person", many: true },
  "note": { label: "Note" },
  "notes": { label: "Notes" },
  "options": { label: "Options", kind: "words", many: true },
  "order": { label: "Order" },
  "organization": { label: "Organisation" },
  "organization_name": { label: "Organisation" },
  "owner_email": { label: "Owner", kind: "person" },
  "parent": { label: "Parent task", kind: "task" },
  "parent_id": { label: "Parent" },
  "parent_project_id": { label: "Parent project" },
  "parent_task_id": { label: "Parent task" },
  "place": { label: "Place" },
  "position": { label: "Position" },
  "priority": { label: "Priority" },
  "project": { label: "Project", kind: "project" },
  "project_id": { label: "Project" },
  "reopens": { label: "Reopens" },
  "repeat day": { label: "Repeat day" },
  "repeats": { label: "Repeats" },
  "report": { label: "Report" },
  "required": { label: "Required", kind: "flag" },
  "rule": { label: "Rule" },
  "scheduled_end": { label: "Ends", kind: "date" },
  "scheduled_start": { label: "Starts", kind: "date" },
  "scope": { label: "Scope" },
  "seen by": { label: "Seen by" },
  "source": { label: "Source", kind: "words" },
  "source N": { label: "Source", kind: "task" },
  "stage": { label: "Stage" },
  "start": { label: "Start", kind: "date" },
  "start_date": { label: "Start", kind: "date" },
  "status": { label: "Status", kind: "status" },
  "status set": { label: "Status set", kind: "project" },
  "status_category": { label: "Status category", kind: "category" },
  "status_id": { label: "Status" },
  "statuses": { label: "Statuses" },
  "subject": { label: "Subject" },
  "subtasks": { label: "Subtasks" },
  "survivor": { label: "Keeps", kind: "task" },
  "tag": { label: "Tag", kind: "tag" },
  "tags": { label: "Tags", kind: "tag", many: true },
  "tags_add": { label: "Add tags", kind: "tag", many: true },
  "tags_remove": { label: "Remove tags", kind: "tag", many: true },
  "task": { label: "Task", kind: "task" },
  "task N": { label: "Task", kind: "task" },
  "task N check": { label: "Check" },
  "tasks": { label: "Tasks" },
  "tasks renamed": { label: "Tasks renamed" },
  "timezone": { label: "Time zone" },
  "title": { label: "Title" },
  "to": { label: "To", kind: "task" },
  "type": { label: "Type", kind: "words" },
  "under": { label: "Under", kind: "project" },
  "undo": { label: "Undo" },
  "until": { label: "Until", kind: "date" },
  "updated_at": { label: "Updated", kind: "date" },
  "view": { label: "View" },
  "view_type": { label: "View type", kind: "words" },
  "visible to": { label: "Visible to" },
  "waiting since": { label: "Waiting since" },
  "waiting_on": { label: "Waiting on" },
};

/** The map key of a card key: digits read as `N`, a custom field as `field *`. */
export function cardKey(raw: string): string {
  const key = raw.trim();
  if (/^field\s+\S/.test(key) && !(key in CARD_FIELDS)) return "field *";
  return key.replace(/\d+/g, "N");
}

/** The custom field's own name, for `field <Name>`. */
function customFieldName(raw: string): string | null {
  const m = /^field\s+(.+)$/.exec(raw.trim());
  return m && !(raw.trim() in CARD_FIELDS) ? m[1] : null;
}

/** The spec of a card key. A key with no line reads as words, never raw. */
export function fieldSpec(raw: string): FieldSpec {
  const custom = customFieldName(raw);
  if (custom) return { label: custom };
  const spec = CARD_FIELDS[cardKey(raw)];
  if (spec) return spec;
  const words = raw.trim().replace(/_/g, " ");
  return { label: words.charAt(0).toUpperCase() + words.slice(1) };
}

/** The kinds a column label may take from this map. A task or a project
 *  needs its link, which a label does not carry, so it stays text. A
 *  "Category" label is text too: a model's "Category" is not a stage. */
const LABEL_KINDS: ReadonlySet<FieldKind> = new Set(["status", "tag", "person", "date"]);

const KIND_BY_LABEL: ReadonlyMap<string, FieldKind> = (() => {
  const out = new Map<string, FieldKind>();
  for (const spec of Object.values(CARD_FIELDS)) {
    const key = spec.label.toLowerCase();
    if (spec.kind && LABEL_KINDS.has(spec.kind) && !out.has(key)) out.set(key, spec.kind);
  }
  return out;
})();

/**
 * The kind a table column draws by, from its label: "Status" is a status,
 * "Tags" a tag, "Assignees" a person, "Due" a date. A label that this map
 * does not hold, or holds with no such kind, is text. The `dataGrid`
 * template reads it when the data names no kinds (`lib/dataGridLayout.ts`).
 */
export function kindForLabel(label: string): FieldKind | undefined {
  return KIND_BY_LABEL.get(label.trim().toLowerCase());
}

/**
 * A person as a name and an address. An assignee is a bare string that
 * nothing validates (D-PM-4), so the tools print "Priya (priya@x.io)",
 * "Priya <priya@x.io>", an address alone, or a name alone. A chip shows the
 * name, and keeps the address for its initials and its tooltip.
 */
export function personOf(text: string): { name: string; email?: string } {
  const value = text.trim();
  const m = /^(.+?)\s*[(<]\s*([^\s()<>@]+@[^\s()<>@]+\.[^\s()<>@]+)\s*[)>]$/.exec(value);
  if (m) return { name: m[1].trim(), email: m[2] };
  if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) return { name: value, email: value };
  return { name: value };
}

// ── The value ────────────────────────────────────────────────────────────────

/** One element of a value: a name with an optional `#n`, or a plain run. */
export interface ValueItem {
  text: string;
  number?: string;
  /** False for a plain run that no fence marked, such as `None`. */
  named: boolean;
}

/** A value, or a change from one value to another. */
export interface FieldValue {
  after: ValueItem[];
  before?: ValueItem[];
  /** The whole value as plain text, for a tooltip and a compare. */
  plain: string;
}

/**
 * Split at ` → ` outside the marks. Only the LAST arrow is the change: the
 * tools write `before → after`, and an arrow inside a fenced name is data.
 */
function splitChange(value: string): [string, string] | null {
  let depth = 0;
  let at = -1;
  for (let k = 0; k < value.length; k++) {
    const c = value[k];
    if (c === "«") depth += 1;
    else if (c === "»") depth = Math.max(0, depth - 1);
    else if (depth === 0 && value.startsWith(" → ", k)) at = k;
  }
  return at < 0 ? null : [value.slice(0, at), value.slice(at + 3)];
}

/** `['a', 'b']`, the Python form of a list, as its strings. */
function pythonList(side: string): string[] | null {
  const m = /^\[(.*)\]$/.exec(side.trim());
  if (!m) return null;
  return [...m[1].matchAll(/'([^']*)'|"([^"]*)"/g)].map((x) => x[1] ?? x[2]).filter(Boolean);
}

/** One side of a value as its elements. */
function itemsOf(side: string, many: boolean): ValueItem[] {
  const text = side.trim();
  // An empty value (`«»`) reads as none, never as nothing before an arrow.
  if (unfenced(text).trim() === "") return [{ text: "None", named: false }];
  const list = pythonList(text);
  if (list) return list.map((t) => ({ text: t, named: true }));
  const parts = splitFenced(text);
  const names = parts.filter((p) => p.kind === "name");
  const rest = parts
    .filter((p) => p.kind === "text")
    .map((p) => p.text)
    .join("")
    .replace(/[,\s]+/g, "");
  if (names.length > 0 && rest === "" && (many || names.length === 1)) {
    return names.map((p) => ({ text: p.text, number: p.kind === "name" ? p.number : undefined, named: true }));
  }
  if (many && names.length === 0 && text.includes(",")) {
    return text.split(",").map((t) => ({ text: t.trim(), named: true })).filter((i) => i.text);
  }
  return [{ text, named: false }];
}

/** Parse a card value into its elements, and its change when it is one. */
export function parseFieldValue(value: string, many = false): FieldValue {
  const plain = unfenced(value).trim();
  const change = splitChange(value);
  if (change) {
    return { before: itemsOf(change[0], many), after: itemsOf(change[1], many), plain };
  }
  return { after: itemsOf(value, many), plain };
}

// ── Formats ──────────────────────────────────────────────────────────────────

const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");
const ISO_DAY = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/;

/**
 * `2026-10-07` → `7 Oct 2026`, and a time when the value has one. Parsed by
 * hand, never through `new Date()`: a calendar date is not an instant, and
 * midnight UTC is the day before west of Greenwich (`outlook.ts` `shortDate`).
 * A value that is not a date is returned as it is.
 */
export function formatCardDate(value: string): string {
  const m = ISO_DAY.exec(value.trim());
  if (!m) return value;
  const month = MONTHS[Number(m[2]) - 1];
  if (!month) return value;
  const day = `${Number(m[3])} ${month} ${m[1]}`;
  return m[4] ? `${day}, ${m[4]}:${m[5]}` : day;
}

/** `90` → `1 h 30 min`. */
export function formatMinutes(value: string): string {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return value;
  const h = Math.floor(n / 60);
  const min = Math.round(n % 60);
  if (h === 0) return `${min} min`;
  return min ? `${h} h ${min} min` : `${h} h`;
}

/** `relates_to` → `Relates to`. */
export function formatWords(value: string): string {
  const words = value.replace(/_/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : words;
}

/** Python's `True`, `False` and `None`, and `yes`/`no`, as words. */
export function formatPlain(value: string): string {
  const v = value.trim();
  if (v === "None" || v === "") return "none";
  if (v === "True" || v.toLowerCase() === "yes") return "Yes";
  if (v === "False" || v.toLowerCase() === "no") return "No";
  return v;
}

/**
 * True when a field only repeats a line the card already shows (owner
 * report, 2026-10-07: "Impact" repeated the detail word for word).
 */
export function repeatsLine(value: string, shown: readonly (string | undefined)[]): boolean {
  const norm = (s: string) => unfenced(s).replace(/\s+/g, " ").trim().toLowerCase();
  const v = norm(value);
  return v !== "" && shown.some((s) => s !== undefined && norm(s) === v);
}
