/**
 * The pure half of the Board block (`ai_studio.md` §14 D3, slice STU-D3).
 *
 * A board groups the records of a source by one select field. One lane
 * holds one option, in the option order. A record with no value, or a value
 * that is not an option, goes to a last lane, "No value". This file reads the
 * block's props, builds the lanes and runs the keyboard steps of a move. It
 * has no React import. The words of a card come from `displayValue`
 * (`blocks.ts`), so no second formatter exists. The move itself is in
 * `boardMove.ts`. Fence: `boardModel.test.ts`.
 */
import { labelOf, parseFields } from "./blocks";
import type { FieldSpec } from "./fieldCheck";

/** A lane shows this many cards unless the block says otherwise. */
export const DEFAULT_MAX_CARDS = 50;
/** A card shows at most this many fields under its title. */
export const MAX_CARD_FIELDS = 3;
/** The title of the lane for a record that has no option. */
export const NO_VALUE_TITLE = "No value";

const isObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

export interface BoardConfig {
  fields: FieldSpec[];
  /** The select field that makes the lanes. */
  group: FieldSpec;
  titleField: FieldSpec | null;
  cardFields: FieldSpec[];
  /** The action a drop runs, or null for a board that only reads. */
  update: string | null;
  maxCards: number;
}

export type ParsedBoard = { ok: true; config: BoardConfig } | { ok: false; reason: string };

/** Read the props of a board node. A model writes them, so each part is checked. */
export function parseBoard(data: unknown): ParsedBoard {
  const d = isObject(data) ? data : {};
  const fields = parseFields(d.fields);
  const groupKey = typeof d.groupBy === "string" ? d.groupBy : "";
  const group = fields.find((f) => f.key === groupKey);
  if (!group) return { ok: false, reason: "This board needs a field to group by." };
  if (group.type !== "select" || group.many === true) {
    return { ok: false, reason: `This board groups by a select field. "${labelOf(group)}" is not one.` };
  }
  const byKey = (k: unknown) => (typeof k === "string" ? fields.find((f) => f.key === k) : undefined);
  const titleField = byKey(d.titleField)
    ?? fields.find((f) => f.key === "title")
    ?? fields.find((f) => f.type === "text" && f.key !== group.key)
    ?? null;
  const cardFields: FieldSpec[] = [];
  for (const k of Array.isArray(d.cardFields) ? d.cardFields : []) {
    const f = byKey(k);
    if (f && f.key !== group.key && f.key !== titleField?.key && !cardFields.includes(f)) cardFields.push(f);
  }
  const max = typeof d.maxCards === "number" && Number.isInteger(d.maxCards) && d.maxCards >= 1
    ? d.maxCards : DEFAULT_MAX_CARDS;
  const update = typeof d.update === "string" && d.update.trim() ? d.update.trim() : null;
  return {
    ok: true,
    config: { fields, group, titleField, cardFields: cardFields.slice(0, MAX_CARD_FIELDS), update, maxCards: max },
  };
}

/** The node that draws a Board: the `board` template on the D1 bind path. */
export function boardBlock(
  data: {
    fields: readonly FieldSpec[];
    groupBy: string;
    titleField?: string;
    cardFields?: readonly string[];
    update?: string;
    maxCards?: number;
    title?: string;
  },
  source: { action: string; args?: Record<string, unknown> },
): { type: "template"; props: Record<string, unknown> } {
  return { type: "template", props: { name: "board", source, data } };
}

/** The id of the lane of an option. `null` is the "No value" lane. */
export const laneId = (value: string | null): string => (value === null ? "none" : "opt:" + value);

export interface Lane {
  id: string;
  /** The option, or `null` for the "No value" lane. */
  value: string | null;
  title: string;
  /** The cards on screen, at most `maxCards` unless the lane is expanded. */
  rows: Record<string, unknown>[];
  /** The true count of the lane. */
  total: number;
  /** The cards that "Show more" would add. */
  hidden: number;
}

/** The id of a record, or null when it has none (it cannot be moved). */
export function rowId(row: Record<string, unknown>): string | null {
  const id = row.id;
  return typeof id === "string" || typeof id === "number" ? String(id) : null;
}

/** The records of a row list: a null or a non-object entry is not a record. */
export function recordsOf(rows: readonly unknown[]): Record<string, unknown>[] {
  return rows.filter((r): r is Record<string, unknown> => !!r && typeof r === "object");
}

/** The option that a record sits under now, or null for "No value". */
export function optionOf(
  config: BoardConfig,
  row: Record<string, unknown>,
  placed: Readonly<Record<string, string | null>> = {},
): string | null {
  const id = rowId(row);
  const v = id !== null && id in placed ? placed[id] : row[config.group.key];
  return typeof v === "string" && (config.group.options ?? []).includes(v) ? v : null;
}

/**
 * The lanes of a board. `placed` holds the cards that moved and wait for the
 * door: such a card shows in its new lane, at the end. `expanded` holds the
 * lanes that show every card.
 */
export function buildLanes(
  config: BoardConfig,
  rows: readonly Record<string, unknown>[],
  opts: { placed?: Readonly<Record<string, string | null>>; expanded?: ReadonlySet<string> } = {},
): Lane[] {
  // One lane for each option: a repeated option would give two lanes the same key and cards.
  const options = [...new Set(config.group.options ?? [])];
  const buckets = new Map<string | null, Record<string, unknown>[]>(options.map((o) => [o, []]));
  const moved: Record<string, unknown>[] = [];
  const put = (row: Record<string, unknown>) => {
    const key = optionOf(config, row, opts.placed);
    const list = buckets.get(key) ?? [];
    buckets.set(key, list);
    list.push(row);
  };
  const placed = opts.placed ?? {};
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const id = rowId(row);
    if (id !== null && id in placed) moved.push(row);
    else put(row);
  }
  for (const row of moved) put(row);
  const keys: (string | null)[] = [...options];
  if ((buckets.get(null) ?? []).length > 0) keys.push(null);
  return keys.map((value) => {
    const all = buckets.get(value) ?? [];
    const id = laneId(value);
    const shown = opts.expanded?.has(id) ? all : all.slice(0, config.maxCards);
    return {
      id, value, title: value === null ? NO_VALUE_TITLE : value,
      rows: shown, total: all.length, hidden: all.length - shown.length,
    };
  });
}

// --- the keyboard path ---------------------------------------------------------

/** A card held up by the keyboard, and the lane it is over. */
export interface Hold {
  id: string;
  from: string;
  over: string;
}

export interface KeyStep {
  hold: Hold | null;
  /** Set when the key dropped the card: move `id` to the lane `to`. */
  drop?: { id: string; to: string };
  /** Set when the key put the card back. */
  cancelled?: boolean;
  /** False: the key is none of the board's, so the browser keeps it. */
  handled: boolean;
}

/**
 * One key on a focused card. Space picks the card up. The arrows choose a
 * lane, with no wrap. Space drops it. Escape cancels. `laneIds` is the lane
 * order, and `card.lane` is the lane the card is in.
 */
export function keyStep(
  hold: Hold | null,
  key: string,
  card: { id: string; lane: string },
  laneIds: readonly string[],
): KeyStep {
  if (key === " " || key === "Spacebar") {
    if (!hold) return { hold: { id: card.id, from: card.lane, over: card.lane }, handled: true };
    return { hold: null, drop: { id: hold.id, to: hold.over }, handled: true };
  }
  if (!hold) return { hold, handled: false };
  if (key === "Escape") return { hold: null, cancelled: true, handled: true };
  if (key === "ArrowLeft" || key === "ArrowRight") {
    const at = laneIds.indexOf(hold.over);
    const to = Math.min(laneIds.length - 1, Math.max(0, at + (key === "ArrowRight" ? 1 : -1)));
    return { hold: { ...hold, over: laneIds[to] ?? hold.over }, handled: true };
  }
  return { hold, handled: false };
}
