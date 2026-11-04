/**
 * Inline edit of a cell in the Table (`ai_studio.md` §14 D2b-1, slice STU-D2b).
 *
 * A plain store with no React import, on the pattern of `boardMove.ts`. The
 * Table reads it with `useSyncExternalStore`, and `inlineEdit.test.ts` drives
 * it with a fake door.
 *
 * A commit shows the new value at once. Exactly one `door.run(update, {id,
 * data: {[key]: value}})` follows, with one key. A cell with a call in flight
 * is locked, so a second commit on it does nothing. A refusal puts the old
 * value back and writes the rule or field sentence of the door into `alert`.
 * An accepted edit drops the cached reads of the door, so the Table reads the
 * stored rows again.
 *
 * Only a scalar field edits here: text, number, select, checkbox, date, url,
 * email and phone. A field with many values, a long text, a money, a person
 * and the rest edit in the Form. A field that a formula or a roll-up fills is
 * never editable (`computedKeys`).
 */
import { invalidate } from "@/lib/dataCache";
import { type Draft, draftToValue, labelOf } from "./blocks";
import { doorPrefix, type StudioDataDoor } from "./dataDoor";
import { checkValue, type FieldSpec } from "./fieldCheck";
import { readRefusal, statusLines } from "./formModel";
import type { FieldType } from "./manifest";
import { ACTION_FAILED } from "./TypedActionButton";

/** The types that edit in the cell. */
export const INLINE_TYPES: ReadonlySet<FieldType> = new Set<FieldType>([
  "text", "number", "select", "checkbox", "date", "url", "email", "phone",
]);

/** The keys that a `formula` or a `rollup` rule fills. The server refuses a write to them. */
export function computedKeys(rules: unknown): ReadonlySet<string> {
  const out = new Set<string>();
  if (!Array.isArray(rules)) return out;
  for (const r of rules) {
    if (!r || typeof r !== "object") continue;
    const rule = r as Record<string, unknown>;
    if ((rule.kind === "formula" || rule.kind === "rollup") && typeof rule.target === "string") out.add(rule.target);
  }
  return out;
}

/** Can this field edit in its cell? */
export function isInlineEditable(f: FieldSpec, computed: ReadonlySet<string>): boolean {
  return INLINE_TYPES.has(f.type) && f.many !== true && !computed.has(f.key);
}

export interface EditState {
  /** The cell in its editor, or null. */
  editing: { id: string; key: string } | null;
  /** The values that the member set and the door has not confirmed. `id` then `key`. */
  shown: Readonly<Record<string, Readonly<Record<string, unknown>>>>;
  /** The cells with a call in flight, as `cellId(id, key)`. */
  pending: ReadonlySet<string>;
  /** The refusal to show in a `role="alert"` line, or null. */
  alert: string | null;
  /** The sentence for the `aria-live` line. */
  live: string;
}

export const cellId = (id: string, key: string): string => `${id}\u0000${key}`;

export interface Editor {
  getState(): EditState;
  subscribe(fn: () => void): () => void;
  /** Can the cells of the field `key` edit? */
  canEdit(key: string): boolean;
  /** Open the editor of a cell. Does nothing for a cell that cannot edit or is in flight. */
  open(id: string, key: string): void;
  /** Close the editor with no change. */
  cancel(): void;
  /**
   * Commit a draft. `current` is the value that the cell shows now. A draft
   * that equals it sends nothing.
   */
  commit(id: string, key: string, draft: Draft, current: unknown): Promise<void>;
  /** Drop the values that the door has accepted. The view calls it when fresh rows arrive. */
  settle(): void;
}

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/** The words of a refusal. A field sentence starts with the label of its field. */
export function editRefusalText(
  fields: readonly FieldSpec[],
  res: { message?: unknown; data?: unknown } | null | undefined,
): string {
  const refusal = readRefusal(res?.data);
  const own = typeof res?.message === "string" && res.message.trim() ? res.message : null;
  const message = (refusal.rule ? refusal.message ?? own : own) ?? ACTION_FAILED;
  const byLabel: Record<string, string> = {};
  for (const [k, v] of Object.entries(refusal.fieldErrors)) {
    const f = fields.find((x) => x.key === k);
    byLabel[f ? labelOf(f) : k] = v;
  }
  return statusLines([], { message, fieldErrors: byLabel, rule: refusal.rule }).join(" ");
}

export function createEditor(
  door: StudioDataDoor,
  update: string,
  fields: readonly FieldSpec[],
  computed: ReadonlySet<string> = new Set(),
): Editor {
  let state: EditState = { editing: null, shown: {}, pending: new Set(), alert: null, live: "" };
  const listeners = new Set<() => void>();
  const answered = new Set<string>();
  const set = (next: Partial<EditState>) => {
    state = { ...state, ...next };
    listeners.forEach((fn) => fn());
  };
  const field = (key: string): FieldSpec | undefined => {
    const f = fields.find((x) => x.key === key);
    return f && isInlineEditable(f, computed) ? f : undefined;
  };
  const withValue = (id: string, key: string, value: unknown) => ({
    ...state.shown, [id]: { ...state.shown[id], [key]: value },
  });
  const withoutValue = (id: string, key: string) => {
    const { [key]: _gone, ...rest } = state.shown[id] ?? {};
    void _gone;
    const { [id]: _row, ...others } = state.shown;
    void _row;
    return Object.keys(rest).length > 0 ? { ...others, [id]: rest } : others;
  };

  return {
    getState: () => state,
    subscribe(fn) {
      listeners.add(fn);
      return () => { listeners.delete(fn); };
    },
    canEdit: (key) => field(key) !== undefined,
    open(id, key) {
      if (!field(key) || state.pending.has(cellId(id, key))) return;
      if (state.editing?.id === id && state.editing.key === key) return;
      set({ editing: { id, key }, alert: null });
    },
    cancel() {
      if (state.editing !== null) set({ editing: null });
    },
    async commit(id, key, draft, current) {
      const f = field(key);
      if (!f) return;
      const cell = cellId(id, key);
      if (state.pending.has(cell)) return;
      const raw = draftToValue(f, draft);
      let value: unknown;
      if (raw === undefined) {
        // A blank control clears the field, unless the field is required.
        if (f.required) {
          set({ alert: `${labelOf(f)}: This field is required.`, live: "Not saved" });
          return;
        }
        value = null;
      } else {
        const checked = checkValue(f, raw);
        if (!checked.ok) {
          set({ alert: `${labelOf(f)}: ${checked.message}`, live: "Not saved" });
          return;
        }
        value = checked.value;
      }
      if (same(value, current)) {
        set({ editing: null, alert: null });
        return;
      }
      const had = key in (state.shown[id] ?? {});
      const prior = state.shown[id]?.[key];
      const back = () => (had ? withValue(id, key, prior) : withoutValue(id, key));
      set({
        shown: withValue(id, key, value),
        pending: new Set(state.pending).add(cell),
        editing: null,
        alert: null,
        live: "Saving",
      });
      const done = (next: Partial<EditState>) => {
        const pending = new Set(state.pending);
        pending.delete(cell);
        set({ pending, ...next });
      };
      try {
        const res = await door.run(update, { id, data: { [key]: value } });
        if (res && res.ok === true) {
          answered.add(cell);
          invalidate(doorPrefix(door));
          done({ live: "Saved" });
          return;
        }
        const text = editRefusalText(fields, res);
        done({ shown: back(), alert: text, live: `Not saved: ${text}` });
      } catch {
        done({ shown: back(), alert: ACTION_FAILED, live: `Not saved: ${ACTION_FAILED}` });
      }
    },
    settle() {
      if (answered.size === 0) return;
      let shown = state.shown;
      for (const cell of answered) {
        if (state.pending.has(cell)) continue;
        const [id, key] = cell.split("\u0000");
        const { [key]: _gone, ...rest } = shown[id] ?? {};
        void _gone;
        const { [id]: _row, ...others } = shown;
        void _row;
        shown = Object.keys(rest).length > 0 ? { ...others, [id]: rest } : others;
        answered.delete(cell);
      }
      if (shown !== state.shown) set({ shown });
    },
  };
}
