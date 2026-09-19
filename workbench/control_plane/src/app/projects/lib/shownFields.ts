/**
 * Projects · the shown-fields contract (WS-27x).
 *
 * ONE vocabulary, defined here and read by both consumers: the table's columns
 * AND the chip gate (`card.visibleChips`). The whole reason the spreadsheet
 * layout and the chip row shipped as one ticket is that the column set IS the
 * contract — a field hidden from the table must also earn no chip, and two
 * lists of "the fields a view shows" would disagree within a release.
 *
 * The gateway mirrors this set in `filters.SHOWN_FIELDS`
 * (`normalise_view_config` drops keys it does not know), so a key added here
 * without being added there is a preference the server silently strips on the
 * next save. The round-trip tests on both sides exist to make that loud.
 *
 * **`shown_fields` is a SET, not a sequence.** Column order is the
 * vocabulary's own (core keys in declaration order, then the project's custom
 * fields in their registry order — `table.tableColumns`), never the stored
 * list's, so a hand-shuffled config cannot make two viewers' tables disagree
 * about where the Status column is.
 */

export const FIELD_KEYS = [
  "status",
  "type",
  "assignees",
  "start_date",
  "due_at",
  "importance",
  "subtasks",
  "blocked",
  "tags",
  "attachments",
  "estimate",
  "created_at",
] as const;

export type FieldKey = (typeof FIELD_KEYS)[number];

export const FIELD_LABELS: Record<FieldKey, string> = {
  status: "Status",
  type: "Type",
  assignees: "Assignees",
  start_date: "Start",
  due_at: "Due",
  importance: "Priority",
  subtasks: "Subtasks",
  blocked: "Blocked",
  tags: "Tags",
  attachments: "Files",
  estimate: "Estimate",
  created_at: "Created",
};

/**
 * A project's custom fields ride the same list as `custom.<field_key>` — the
 * exact spelling `patch_task` already files a custom edit under on the
 * timeline, so there is one way to name a custom field across the app.
 *
 * Checked by SHAPE rather than against a registry, on both sides of the wire:
 * `shown_fields` is normalised by pure functions (here and in `filters.py`)
 * that do not hold the project's field definitions, and a view must survive a
 * field being deleted after it was saved — the table simply has no column to
 * draw for it, which is the honest rendering.
 */
export const CUSTOM_FIELD_PREFIX = "custom.";

/** The set a view with no stored `shown_fields` means. */
export const DEFAULT_SHOWN: readonly string[] = [
  "status",
  // WS-27bh. ⚠️ Shown by DEFAULT, and that is the acceptance criterion rather
  // than a preference: §9.9.2 asks that "an Epic is distinguishable from a
  // Task at a glance", and a chip behind a picker nobody opens is not.
  // A member who does not want it turns it off in Fields, and a member with a
  // SAVED view keeps exactly the columns they saved — this list is only the
  // default for a view that has expressed no opinion.
  "type",
  "assignees",
  "due_at",
  "importance",
  "subtasks",
  "blocked",
  "tags",
];

/** Whether `key` is a field a view may show — core key or a `custom.<key>`. */
export function isFieldKey(key: unknown): key is string {
  if (typeof key !== "string") return false;
  if ((FIELD_KEYS as readonly string[]).includes(key)) return true;
  return (
    key.startsWith(CUSTOM_FIELD_PREFIX) && key.length > CUSTOM_FIELD_PREFIX.length
  );
}

/** How a custom field definition spells itself in a `shown_fields` list. */
export function customFieldKey(def: { field_key: string }): string {
  return `${CUSTOM_FIELD_PREFIX}${def.field_key}`;
}

/**
 * A stored config value → the shown list, or `null` when nothing was stored.
 *
 * `null`, not the default: "absent" and "explicitly empty" are different
 * facts. A view saved before shown-fields existed must read as the default
 * set, while a view whose owner hid every column must come back with every
 * column hidden — collapsing the two would un-hide someone's deliberate
 * choice on the next apply. Unknown and non-string keys are dropped
 * (hand-edits, or a newer client's vocabulary), duplicates collapse to the
 * first appearance.
 */
export function sanitizeShownFields(raw: unknown): string[] | null {
  if (!Array.isArray(raw)) return null;
  const seen = new Set<string>();
  const out: string[] = [];
  for (const key of raw) {
    if (!isFieldKey(key) || seen.has(key)) continue;
    seen.add(key);
    out.push(key);
  }
  return out;
}

/** Set equality — order is presentation the vocabulary owns, not the list. */
export function sameFieldSet(
  a: readonly string[],
  b: readonly string[]
): boolean {
  if (a.length !== b.length) return false;
  const set = new Set(a);
  return b.every((key) => set.has(key));
}

/** Toggle one field in or out of the shown set. */
export function toggleField(shown: readonly string[], key: string): string[] {
  return shown.includes(key)
    ? shown.filter((k) => k !== key)
    : [...shown, key];
}
