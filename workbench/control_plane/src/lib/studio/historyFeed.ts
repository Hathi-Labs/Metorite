/**
 * The model of the Activity tab of a record (STU-C4b, `ai_studio.md` §14, rows C4b and D22a).
 * Pure: no React, no I/O.
 *
 * It turns the answer of the door op `object:<name>.history` (C4a-2) into display lines. The wire
 * shape is `{history: [{id, op, actor, via_kind, via_id, changes, at}], next}`. `changes` is
 * `{key: {old?, new}}`. The door lists the changes OLDEST FIRST, and pages by `id > after`, so
 * `next` is the cursor of the next page, or null on the last page.
 *
 * **The order.** The view keeps the order of the door: oldest first, and "Load more" brings the
 * later changes. The C4b row says newest first, but the door cannot page from the end, so a
 * newest-first list would need each page to be read first. A later slice can add a direction to
 * the door. Until then the section says "Oldest first".
 *
 * **The keys.** A line is drawn for each key that is IN `changes`, and for no other. The door drops
 * a key that C8 hides, or that the viewer may not read. This model never fills a key from the
 * record, so a hidden field shows no line, not even a blank one.
 *
 * **A boundary change has no `old`.** The first value of a record, and the first change after a
 * viewer's scope start, carry only `new`. The line then shows the new value and no "from".
 *
 * **The words of a value** come from `displayValue` (`blocks.ts`), the one formatter that the Table
 * and the Detail block use. So money, dates and selects read as they read there. A value is never
 * drawn as raw JSON. A long value is cut at `MAX_VALUE_CHARS`.
 *
 * Fence: `historyFeed.test.ts`.
 */
import { displayValue } from "./blocks";
import type { FieldSpec } from "./fieldCheck";

/** The longest value that a line shows, in characters. A longer value ends with an ellipsis. */
export const MAX_VALUE_CHARS = 160;

/** The word of an empty value. */
export const EMPTY_VALUE = "empty";

/** The words of an op, as a verb in the past tense. */
export const OP_WORDS: Readonly<Record<string, string>> = {
  create: "Created",
  update: "Changed",
  delete: "Deleted",
};

/** The words of the writer when the door gives no address. */
export const VIA_WORDS: Readonly<Record<string, string>> = {
  workflow_run: "a workflow run",
  agent: "an agent",
};

export interface HistoryLine {
  key: string;
  label: string;
  /** The words of the old value, or null when the change has no `old` key. */
  old: string | null;
  /** The words of the new value. */
  next: string;
}

export interface HistoryEntry {
  id: number;
  op: string;
  /** "Created", "Changed" or "Deleted". Another op shows its own word. */
  opWord: string;
  /** Who wrote: an address, or "a workflow run", or "an agent", or "Someone". */
  who: string;
  /** The `at` of the door, for the `time` element. */
  at: string;
  /** The words of the time, in the local time of the viewer. */
  atText: string;
  lines: HistoryLine[];
}

export interface HistoryPage {
  entries: HistoryEntry[];
  /** The cursor of the next page (`after`), or null on the last page. */
  next: number | null;
}

const isObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

/** Cut a value to `MAX_VALUE_CHARS`, with an ellipsis. */
export function clip(text: string): string {
  return text.length > MAX_VALUE_CHARS ? `${text.slice(0, MAX_VALUE_CHARS - 1).trimEnd()}…` : text;
}

/**
 * The words of one value of one field. `displayValue` is the formatter. A key with no field (a
 * field removed since) reads as text. A value that no formatter words never shows as JSON:
 * a number or a boolean reads as itself, and any other shape reads as "a value".
 */
export function valueWords(field: FieldSpec | undefined, v: unknown): string {
  if (v === null || v === undefined || v === "") return EMPTY_VALUE;
  const spec: FieldSpec = field ?? { key: "", type: "text" };
  const words = displayValue(spec, v).trim();
  if (words) return clip(words);
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (typeof v === "string") return EMPTY_VALUE;
  return "a value";
}

/** Who wrote: the address when the door gives one, else the `via` of the change, else "Someone". */
export function whoWords(actor: unknown, viaKind: unknown): string {
  if (typeof actor === "string" && actor.trim()) return actor.trim();
  if (typeof viaKind === "string" && viaKind in VIA_WORDS) return VIA_WORDS[viaKind];
  return "Someone";
}

/** The words of a time, in the local time of the viewer. An `at` that is not a time stays as it is. */
export function timeWords(at: string): string {
  return displayValue({ key: "at", type: "datetime" }, at);
}

function labelOf(field: FieldSpec | undefined, key: string): string {
  return field?.label?.trim() || key;
}

/**
 * The lines of one change: one for each key of `changes`, in the order of the fields of the
 * object, then the keys that the object has no field for. A value that is not `{old?, new}` is
 * taken as the new value, so a bad shape never breaks the page.
 */
export function changeLines(changes: unknown, fields: readonly FieldSpec[]): HistoryLine[] {
  if (!isObject(changes)) return [];
  const byKey = new Map(fields.map((f, i) => [f.key, { f, i }] as const));
  const keys = Object.keys(changes).sort((a, b) => {
    const ia = byKey.get(a)?.i ?? Number.MAX_SAFE_INTEGER;
    const ib = byKey.get(b)?.i ?? Number.MAX_SAFE_INTEGER;
    return ia - ib;
  });
  return keys.map((key) => {
    const field = byKey.get(key)?.f;
    const c = changes[key];
    const pair = isObject(c) && ("new" in c || "old" in c) ? c : { new: c };
    return {
      key,
      label: labelOf(field, key),
      old: "old" in pair ? valueWords(field, pair.old) : null,
      next: valueWords(field, pair.new),
    };
  });
}

/**
 * The page of the answer of the door op, or `null` when the answer has no `history` list (so the
 * caller can fail). A row with no numeric `id` is left out.
 */
export function parseHistory(answer: unknown, fields: readonly FieldSpec[]): HistoryPage | null {
  if (!isObject(answer) || !Array.isArray(answer.history)) return null;
  const entries: HistoryEntry[] = [];
  for (const raw of answer.history) {
    if (!isObject(raw) || typeof raw.id !== "number") continue;
    const op = typeof raw.op === "string" ? raw.op : "update";
    const at = typeof raw.at === "string" ? raw.at : "";
    entries.push({
      id: raw.id,
      op,
      opWord: OP_WORDS[op] ?? op,
      who: whoWords(raw.actor, raw.via_kind),
      at,
      atText: at ? timeWords(at) : "",
      lines: changeLines(raw.changes, fields),
    });
  }
  return { entries, next: typeof answer.next === "number" ? answer.next : null };
}

/**
 * The pages together: the entries of each page in order, with the `next` of the last. A page that
 * repeats an id of an earlier page adds nothing, so a refresh cannot show a change twice.
 */
export function joinPages(first: HistoryPage, later: readonly HistoryPage[]): HistoryPage {
  const seen = new Set<number>();
  const entries: HistoryEntry[] = [];
  for (const page of [first, ...later]) {
    for (const e of page.entries) {
      if (!seen.has(e.id)) { seen.add(e.id); entries.push(e); }
    }
  }
  const last = later.length ? later[later.length - 1] : first;
  return { entries, next: last.next };
}

/** The door source of a page of the history of one record. `after` is the cursor, when there is one. */
export function historySource(object: string, recordId: string, after: number | null): {
  action: string; args: { id: string; after?: number };
} {
  return {
    action: `object:${object}.history`,
    args: after === null ? { id: recordId } : { id: recordId, after },
  };
}
