/**
 * The model of the sheet of line items (STU-D17a, `ai_studio.md` D17 and C13). Pure: no React.
 *
 * A parent object with a `lines` field shows its lines as a sheet in the Lines tab of its record
 * page. The sheet edits into a LOCAL BUFFER. No cell writes. One Save sends ONE
 * `door.run(object:<parent>.update, {id, data: {<lines key>: [...]}})`. The door saves the record and
 * its lines in one `apply_writes` call (C13): all or none, at most 49 lines.
 *
 * **The body.** A new row is `{data}` with the keys that the member set. A changed row is
 * `{id, data}` with the changed keys only. A row that nobody touched is not sent. A computed key,
 * the back-link and a lines field are not columns, so they never travel. Last writer wins: a line
 * has no version check, so a save overwrites the keys that it names.
 *
 * **The refusal.** The sheet judges no rule. The door does, and a refusal KEEPS every edit. A
 * "Line N of `key`: ..." sentence names the Nth line of the BODY that was sent. That is the order of
 * the buffer, not the order on screen after a sort, so `buildSave` returns the order of the rows.
 * A rule of the parent (the roll-up and its range) reads "Line 1 of `key`: Rule `r` of object
 * `parent`: <message>", so the sheet strips the prefix and shows one alert over the sheet.
 *
 * **The paste** lives in `sheetPaste.ts` and edits the same buffer.
 */
import { invalidate } from "@/lib/dataCache";
import { type Draft, draftToValue, labelOf } from "./blocks";
import { doorPrefix, type Row, type StudioDataDoor } from "./dataDoor";
import { checkValue, type FieldSpec } from "./fieldCheck";
import { isInlineEditable } from "./inlineEdit";
import { MAX_TABLE_COLUMNS, tableFields } from "./packagePreview";
import { ACTION_FAILED } from "./TypedActionButton";

/** The most lines in one save (`MAX_LINES_PER_SAVE`, C13). The cap counts new and changed rows together. */
export const MAX_SHEET_LINES = 49;

/** The key of the status column that the sheet adds to the grid. No object field has this key. */
export const STATE_KEY = "_state";

export const NOTHING_TO_SAVE = "There is nothing to save.";
export const SHEET_READ_ONLY = "You can read this sheet. Only the owner of the app can change it.";

const isObject = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const same = (a: unknown, b: unknown): boolean => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

export interface SheetColumn {
  field: FieldSpec;
  /** True when the cell edits. A viewer, a computed key and a type outside `INLINE_TYPES` give false. */
  editable: boolean;
}

/**
 * The columns of a sheet: the fields of the line object, with no back-link and no lines field.
 * A cell edits only for the owner, only for a scalar type, and never for a computed key.
 */
export function sheetColumns(
  child: readonly FieldSpec[], back: string, computed: ReadonlySet<string>, canWrite: boolean,
): SheetColumn[] {
  const shown = tableFields(child.filter((f) => f.key !== back)).slice(0, MAX_TABLE_COLUMNS);
  return shown.map((field) => ({ field, editable: canWrite && isInlineEditable(field, computed) }));
}

/** The parent keys that a roll-up of this lines child fills. The sheet shows them after a save. */
export function rollupTargets(parentRules: unknown, child: string, back: string): string[] {
  if (!Array.isArray(parentRules)) return [];
  const out: string[] = [];
  for (const r of parentRules) {
    if (!isObject(r) || r.kind !== "rollup" || r.source !== child || r.via !== back) continue;
    if (typeof r.target === "string" && !out.includes(r.target)) out.push(r.target);
  }
  return out;
}

// --- the buffer -------------------------------------------------------------

export interface SheetRow {
  /** The key of the row on screen. It stays when the row gets its stored id. */
  uid: string;
  /** The stored id, or null for a new row. */
  id: string | null;
  /** The stored values. Empty for a new row. */
  base: Readonly<Record<string, unknown>>;
  /** The values that the member set. A key is here only when it differs from `base`. */
  edits: Readonly<Record<string, unknown>>;
  /** A new row that the door saved. It waits for the fresh read, which brings its id. */
  saved?: boolean;
}

export interface SheetBuffer {
  rows: readonly SheetRow[];
  /** The counter of the next new uid. */
  next: number;
}

export const EMPTY_BUFFER: SheetBuffer = { rows: [], next: 1 };

const storedUid = (id: string): string => `s:${id}`;

/** A buffer of the stored rows, with no edit. */
export function fromStored(rows: readonly Row[]): SheetBuffer {
  return syncStored(EMPTY_BUFFER, rows);
}

/**
 * Join fresh stored rows to the buffer. An edit stays, unless the stored value now equals it. A
 * stored row that the buffer lacks joins at the end. A row with an id that is no longer stored
 * leaves, unless it holds edits. A saved new row leaves, because the stored row replaces it.
 */
export function syncStored(buf: SheetBuffer, stored: readonly Row[]): SheetBuffer {
  const byId = new Map<string, Row>();
  for (const r of stored) if (typeof r.id === "string") byId.set(r.id, r);
  const seen = new Set<string>();
  const rows: SheetRow[] = [];
  for (const row of buf.rows) {
    if (row.id === null) {
      if (!row.saved) rows.push(row);
      continue;
    }
    const s = byId.get(row.id);
    if (!s) {
      if (Object.keys(row.edits).length > 0) rows.push(row);
      continue;
    }
    seen.add(row.id);
    const { id: _id, ...base } = s;
    void _id;
    const edits: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(row.edits)) if (!same(v, base[k])) edits[k] = v;
    rows.push({ ...row, base, edits });
  }
  for (const [id, s] of byId) {
    if (seen.has(id)) continue;
    const { id: _id, ...base } = s;
    void _id;
    rows.push({ uid: storedUid(id), id, base, edits: {} });
  }
  return { rows, next: buf.next };
}

/** A blank new row at the end. */
export function addRow(buf: SheetBuffer): SheetBuffer {
  const row: SheetRow = { uid: `n:${buf.next}`, id: null, base: {}, edits: {} };
  return { rows: [...buf.rows, row], next: buf.next + 1 };
}

/** The value that a cell shows: the edit, else the stored value. */
export function valueOf(row: SheetRow, key: string): unknown {
  return key in row.edits ? row.edits[key] : row.base[key];
}

/** The state of a row for the status column. */
export function rowState(row: SheetRow): "new" | "changed" | "saved" | "" {
  if (row.id === null) return row.saved ? "saved" : Object.keys(row.edits).length > 0 ? "new" : "";
  return Object.keys(row.edits).length > 0 ? "changed" : "";
}

/** Does the row go into a save? A blank new row does not. */
function sent(row: SheetRow): boolean {
  if (row.saved) return false;
  return row.id === null
    ? Object.values(row.edits).some((v) => v !== null && v !== undefined)
    : Object.keys(row.edits).length > 0;
}

/** The number of lines that a save would send: new and changed rows together. */
export function saveCount(buf: SheetBuffer): number {
  return buf.rows.filter(sent).length;
}

export const isDirty = (buf: SheetBuffer): boolean => saveCount(buf) > 0;

export type CellCheck = { ok: true; value: unknown } | { ok: false; message: string };

/**
 * Check one draft with `checkValue`, as the Table does. A blank draft clears the field, unless the
 * field is required. This is a field check only: no rule is judged here.
 */
export function cellValue(field: FieldSpec, draft: Draft): CellCheck {
  const raw = draftToValue(field, draft);
  if (raw === undefined) {
    return field.required ? { ok: false, message: "This field is required." } : { ok: true, value: null };
  }
  const checked = checkValue(field, raw);
  return checked.ok ? { ok: true, value: checked.value } : { ok: false, message: checked.message };
}

/** Set one value of a row. A value equal to the stored value drops the edit. */
export function withValue(buf: SheetBuffer, uid: string, key: string, value: unknown): SheetBuffer {
  return {
    ...buf,
    rows: buf.rows.map((row) => {
      if (row.uid !== uid) return row;
      const edits = { ...row.edits };
      if (row.id !== null && same(value, row.base[key])) delete edits[key];
      else if (row.id === null && (value === null || value === undefined)) delete edits[key];
      else edits[key] = value;
      return { ...row, edits };
    }),
  };
}

/** Commit the draft of one cell into the buffer. A bad value gives a sentence and leaves the buffer. */
export function setCell(
  buf: SheetBuffer, uid: string, field: FieldSpec, draft: Draft,
): { buf: SheetBuffer; error: string | null } {
  const row = buf.rows.find((r) => r.uid === uid);
  if (!row) return { buf, error: null };
  const checked = cellValue(field, draft);
  if (!checked.ok) return { buf, error: `${labelOf(field)}: ${checked.message}` };
  return { buf: withValue(buf, uid, field.key, checked.value), error: null };
}

// --- the body of a save -----------------------------------------------------

export interface SaveBody {
  args: { id: string; data: Record<string, unknown[]> };
  /** The uid of each line of the body, in the order that was sent. "Line N" is `order[N - 1]`. */
  order: string[];
}

export type BuildResult = { ok: true; body: SaveBody } | { ok: false; message: string };

/**
 * The body of the one save. A cap break is refused here, BEFORE any call. The lines are in the order
 * of the buffer, so a refusal names the same row that the member sees, whatever the screen sort.
 */
export function buildSave(
  buf: SheetBuffer, opts: { recordId: string; linesKey: string; canWrite: boolean },
): BuildResult {
  if (!opts.canWrite) return { ok: false, message: SHEET_READ_ONLY };
  const rows = buf.rows.filter(sent);
  if (rows.length === 0) return { ok: false, message: NOTHING_TO_SAVE };
  if (rows.length > MAX_SHEET_LINES) {
    return { ok: false, message: `A save holds at most ${MAX_SHEET_LINES} lines. This sheet has ${rows.length} new and changed lines.` };
  }
  const lines = rows.map((row) => {
    if (row.id === null) {
      const data: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(row.edits)) if (v !== null && v !== undefined) data[k] = v;
      return { data };
    }
    return { id: row.id, data: { ...row.edits } };
  });
  return { ok: true, body: { args: { id: opts.recordId, data: { [opts.linesKey]: lines } }, order: rows.map((r) => r.uid) } };
}

// --- the refusal ------------------------------------------------------------

export interface SheetRefusal {
  /** One alert over the sheet, or null. */
  alert: string | null;
  /** The sentence of a line, by the uid of its row. */
  rows: Record<string, string>;
}

const LINE_PLACE = /^Line (\d+) of `[^`]+`: ([\s\S]+)$/;
const RULE_PREFIX = /^Rule `[^`]+` of object `([^`]+)`: ([\s\S]+)$/;

/**
 * The sentence of a rule of the PARENT, with the place and the rule prefix stripped, or null when the
 * sentence is not one. The door words it "Line 1 of `lines`: Rule `r` of object `parent`: <message>".
 * A sentence with no place also reads "Rule `r` of object `parent`: <message>".
 */
export function parentRuleMessage(message: string | null | undefined, parent: string): string | null {
  if (typeof message !== "string") return null;
  const place = LINE_PLACE.exec(message);
  const rule = RULE_PREFIX.exec(place ? place[2] : message);
  return rule && rule[1] === parent ? rule[2] : null;
}

/** Read the refusal of a save. `order` is the order of the body that was sent. */
export function readSheetRefusal(
  res: { message?: unknown } | null | undefined, parent: string, order: readonly string[],
): SheetRefusal {
  const message = typeof res?.message === "string" && res.message.trim() ? res.message : ACTION_FAILED;
  const rule = parentRuleMessage(message, parent);
  if (rule !== null) return { alert: rule, rows: {} };
  const place = LINE_PLACE.exec(message);
  if (place) {
    const uid = order[Number(place[1]) - 1];
    if (uid !== undefined) {
      const rest = RULE_PREFIX.exec(place[2]);
      return { alert: null, rows: { [uid]: rest ? rest[2] : place[2] } };
    }
  }
  return { alert: message, rows: {} };
}

// --- the save ---------------------------------------------------------------

export type SaveOutcome =
  | { kind: "blocked"; message: string }
  | { kind: "saved"; buf: SheetBuffer; answer: Record<string, unknown> }
  | { kind: "refused"; refusal: SheetRefusal };

/** The buffer after a save that worked: each edit is now stored, and a new row waits for its id. */
export function settleSaved(buf: SheetBuffer): SheetBuffer {
  const rows: SheetRow[] = [];
  for (const row of buf.rows) {
    if (row.id === null) {
      if (sent(row)) rows.push({ ...row, saved: true });
      continue;
    }
    rows.push({ ...row, base: { ...row.base, ...row.edits }, edits: {} });
  }
  return { rows, next: buf.next };
}

/**
 * Save the sheet with ONE `door.run`. A refusal returns the sentences and leaves the buffer to the
 * caller, which keeps every edit. An accepted save drops the cached reads of the door.
 */
export async function saveSheet(
  door: StudioDataDoor,
  buf: SheetBuffer,
  opts: { parent: string; recordId: string; linesKey: string; canWrite: boolean },
): Promise<SaveOutcome> {
  const built = buildSave(buf, opts);
  if (!built.ok) return { kind: "blocked", message: built.message };
  try {
    const res = await door.run(`object:${opts.parent}.update`, built.body.args);
    if (res && res.ok === true) {
      invalidate(doorPrefix(door));
      const data = isObject(res.data) ? res.data : {};
      const record = isObject(data.record) && isObject(data.record.data) ? data.record.data : {};
      return { kind: "saved", buf: settleSaved(buf), answer: record };
    }
    return { kind: "refused", refusal: readSheetRefusal(res, opts.parent, built.body.order) };
  } catch {
    return { kind: "refused", refusal: { alert: ACTION_FAILED, rows: {} } };
  }
}
