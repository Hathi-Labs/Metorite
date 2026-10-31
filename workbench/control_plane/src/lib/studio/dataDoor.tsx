/**
 * The data door of a Studio page (`ai_studio.md` §5.2, slice STU-D1).
 *
 * A generative-UI node may carry `props.source`, a read the page runs
 * through its door. A page supplies the door with `StudioDataContext`. Chat
 * supplies none, so the context holds `null` there. Then `source` is
 * ignored, and the node draws from its own props as it always did.
 *
 * This file holds the seam and the pure helpers. `GenerativeUINode.tsx`
 * holds the one renderer that reads them. Fence: `dataDoor.test.ts`.
 */
import { createContext } from "react";

import { text } from "@/lib/genUiText";

/** One row of a source. The block reads the keys it needs. */
export type Row = Record<string, unknown>;

/** What a page reads: a named action and its arguments. */
export interface StudioSource {
  action: string;
  args?: Record<string, unknown>;
}

/** What a typed action returns. */
export interface ActionResult {
  ok: boolean;
  message?: string;
  data?: unknown;
}

/** The page's side of the seam. */
export interface StudioDataDoor {
  /** The app id plus "draft" or "live". It scopes the cache keys of this door. */
  scope: string;
  read(source: StudioSource): Promise<Row[]>;
  /**
   * Run a typed action. ⚠️ `action` and `args` are written by a model.
   * The server side of the door (B2) must refuse an id that the package did
   * not declare, with 403. The client check is only a courtesy.
   */
  run(action: string, args?: Record<string, unknown>): Promise<ActionResult>;
}

/** JSON with sorted keys at every depth. Null stays. Two equal reads give one string. */
export function stableJson(v: unknown): string {
  if (Array.isArray(v)) return "[" + v.map(stableJson).join(",") + "]";
  if (isObject(v)) {
    return "{" + Object.keys(v).sort()
      .filter((k) => v[k] !== undefined)
      .map((k) => JSON.stringify(k) + ":" + stableJson(v[k])).join(",") + "}";
  }
  return JSON.stringify(v === undefined ? null : v);
}

/** The cache key prefix of a door. `invalidate` with it drops all reads of the door. */
export const doorPrefix = (door: StudioDataDoor): string => "studio:" + door.scope + ":";

/** The cache key of one read through a door. */
export function sourceKey(door: StudioDataDoor, source: StudioSource): string {
  return doorPrefix(door) + source.action + ":" + stableJson(source.args ?? {});
}

/** A page supplies the Provider. Chat supplies none, so the value is `null`. */
export const StudioDataContext = createContext<StudioDataDoor | null>(null);

const isObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

/** Read `props.source`, or `null` when it is not a valid source. */
export function parseSource(raw: unknown): StudioSource | null {
  if (!isObject(raw)) return null;
  const action = typeof raw.action === "string" ? raw.action.trim() : "";
  if (!action) return null;
  return { action, args: isObject(raw.args) ? raw.args : undefined };
}

/** The kinds that bind in STU-D1: a `table` and the `dataGrid` template. */
export function bindsSource(type: string, props: Record<string, unknown>): boolean {
  if (parseSource(props.source) === null) return false;
  if (type === "table") return true;
  return type === "template" && props.name === "dataGrid";
}

/** A button's action: the old sentence, or a typed action. */
export type ParsedAction =
  | { kind: "text"; text: string }
  | {
    kind: "typed";
    id: string;
    args?: Record<string, unknown>;
    message?: string;
    /** What chat sends when no `message` exists: the text of the object, else the id. */
    sentence: string;
  };

/**
 * Read `props.action`. A string stays a string. An object with a string `id`
 * is typed. An object with no `id` reads as text, as it did before: models
 * send objects in string slots (`text()` picks `text ?? label ?? value`).
 */
export function parseAction(raw: unknown): ParsedAction | null {
  if (isObject(raw)) {
    const id = typeof raw.id === "string" ? raw.id.trim() : "";
    if (!id) {
      const sentence = text(raw);
      return sentence ? { kind: "text", text: sentence } : null;
    }
    const message = typeof raw.message === "string" && raw.message.trim() ? raw.message : undefined;
    return {
      kind: "typed",
      id,
      args: isObject(raw.args) ? raw.args : undefined,
      message,
      sentence: message ?? (text(raw) || id),
    };
  }
  const sentence = text(raw);
  return sentence ? { kind: "text", text: sentence } : null;
}

/** Can the button do anything? Chat needs `onAction`. A page door needs nothing else. */
export function actionDisabled(
  action: ParsedAction | null,
  door: StudioDataDoor | null,
  onAction?: (message: string) => void,
): boolean {
  if (!action) return true;
  if (action.kind === "typed" && door) return false;
  return !onAction;
}

/**
 * Press a button in chat. A string goes to `onAction`, as before. A typed
 * action with no door goes to `onAction(message ?? text ?? id)`. With a door,
 * `TypedActionButton` handles the press, so this does nothing.
 */
export function pressAction(
  action: ParsedAction | null,
  door: StudioDataDoor | null,
  onAction?: (message: string) => void,
): void {
  if (!action) return;
  if (action.kind === "text") {
    onAction?.(action.text);
    return;
  }
  // On a page, `TypedActionButton` runs the action and owns its states.
  // This path is for chat: the card reaches the member as a sentence.
  if (door) return;
  onAction?.(action.sentence);
}

/** What a bound block draws. */
export type BoundView = "loading" | "error" | "empty" | "rows";

/** The pure state-to-view choice. Rows already read stay on screen. */
export function boundView(state: {
  data: Row[] | undefined | null;
  loading: boolean;
  error: string | null;
}): BoundView {
  if (Array.isArray(state.data)) return state.data.length > 0 ? "rows" : "empty";
  // A door that answers with a non-array is a failure, not a long wait.
  if (state.error || (!state.loading && state.data !== undefined && state.data !== null)) return "error";
  return "loading";
}

/**
 * Door rows as `dataGrid` data. The template reads `{ id?, cells }` rows
 * and a `columns` list. A door row is keyed by column name, so each cell is
 * the row value under that column. A row that has `cells` already stays as
 * it is. Without `columns`, the keys of the first row name them (`id` aside).
 */
export function gridData(
  data: Record<string, unknown>,
  rows: Row[],
): Record<string, unknown> {
  let columns = Array.isArray(data.columns) ? data.columns.map((c) => text(c)) : [];
  const clean = rows.filter((r) => r && typeof r === "object");
  if (columns.length === 0 && clean.length > 0) {
    columns = Object.keys(clean[0]).filter((k) => k !== "id" && k !== "cells");
  }
  const mapped = clean.map((r) => {
    if (Array.isArray(r.cells)) return r;
    const cell = (c: string) => {
      const v = r[c];
      return v === null || v === undefined || typeof v === "object" ? "" : (v as string | number);
    };
    return { ...(r.id !== undefined ? { id: r.id } : {}), cells: columns.map(cell) };
  });
  // A bound grid holds ids of any kind. The default `openBase` links tasks.
  return { openBase: "", ...data, columns, rows: mapped };
}
