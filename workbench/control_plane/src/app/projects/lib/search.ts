/**
 * Projects · the search palette's logic (WS-27r).
 *
 * A palette is a keyboard instrument, and every one of its rules is the kind
 * that is wrong-but-plausible: which keystroke reaches the list rather than the
 * input, what a stale response does when it arrives after a newer one, and what
 * "no results" means while a request is still in flight.
 *
 * Kept out of the component so those can be asserted rather than clicked.
 */

import type { ParentFact } from "@/lib/taskCard";

/** One hit, exactly as `GET /projects/search` returns it. */
export interface Hit {
  id: string;
  title: string;
  task_number?: number | null;
  project_id: string;
  project_name?: string | null;
  status_name?: string | null;
  category?: string | null;
  due_at?: string | null;
  completed_at?: string | null;
  rank: number;
  parent_task_id?: string | null;
  /** D-PM-38 — search always shows subtasks, so each hit names its parent. */
  parent?: ParentFact | null;
}

/** Mirrors the gateway's `MIN_QUERY`. Below it the endpoint answers empty. */
export const MIN_QUERY = 2;

/** How long to wait after the last keystroke before asking. */
export const DEBOUNCE_MS = 180;

export type PaletteState =
  | { kind: "idle" }
  | { kind: "typing" }
  | { kind: "searching" }
  | { kind: "results"; hits: Hit[]; truncated: boolean }
  | { kind: "empty" }
  | { kind: "error"; message: string };

/**
 * What the palette should show, given what it knows.
 *
 * **"No results" is a claim, and it may only be made once.** While a request
 * is in flight the palette says nothing rather than "no results found" —
 * flashing an empty state between every keystroke and its answer is how a
 * palette comes to look broken on a slow connection, and it is the single most
 * common bug in hand-rolled search UIs.
 */
export function paletteState(input: {
  query: string;
  loading: boolean;
  hits: Hit[] | null;
  truncated: boolean;
  error: string | null;
}): PaletteState {
  if (input.error) return { kind: "error", message: input.error };
  if (input.query.trim().length < MIN_QUERY) return { kind: "idle" };
  if (input.loading) {
    // A previous answer stays on screen while the next one loads, so the list
    // does not blank and re-fill under the cursor on every keystroke.
    return input.hits && input.hits.length > 0
      ? { kind: "results", hits: input.hits, truncated: input.truncated }
      : { kind: "searching" };
  }
  if (input.hits === null) return { kind: "typing" };
  if (input.hits.length === 0) return { kind: "empty" };
  return { kind: "results", hits: input.hits, truncated: input.truncated };
}

/**
 * Is this response still the one we want?
 *
 * **The out-of-order trap.** Typing "par" then "parser" issues two requests,
 * and there is no rule saying the first finishes first — a slow "par" landing
 * after a fast "parser" replaces the right answers with stale ones, and the
 * user sees the list change without touching the keyboard. Comparing the
 * response's own echoed query against what is in the box now is the cheapest
 * correct fix, and it needs no request ids because the server echoes `query`.
 */
export function isCurrent(responseQuery: string, liveQuery: string): boolean {
  return responseQuery.trim() === liveQuery.trim();
}

/**
 * Where the selection moves.
 *
 * **Wraps at both ends**, because a palette is used without looking at it: Down
 * from the last row goes to the first, and Up from the first goes to the last,
 * which is how every other palette behaves and therefore what the hands expect.
 * Clamped to a valid index whenever the list shrinks under the cursor — the
 * results change on every keystroke, and an index left pointing past the end is
 * an Enter that opens nothing.
 */
export function moveSelection(
  current: number,
  delta: number,
  length: number,
): number {
  if (length <= 0) return 0;
  const from = Math.min(Math.max(current, 0), length - 1);
  return (((from + delta) % length) + length) % length;
}

/** The keys the palette consumes, and what they mean. */
export type PaletteAction = "up" | "down" | "open" | "close" | null;

/**
 * Which palette action a keystroke is, if any.
 *
 * **Arrow keys must not reach the input.** Left to the browser they move the
 * text caret to the start or end of the query, so the selection appears to move
 * while the cursor jumps — two effects from one key.
 *
 * A key with a modifier held is NOT an action: `Cmd+Left` is "go to line start"
 * and stealing it breaks text editing inside the very box the palette is built
 * around.
 */
export function paletteKey(event: {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
}): PaletteAction {
  if (event.metaKey || event.ctrlKey || event.altKey) return null;
  if (event.key === "ArrowDown") return "down";
  if (event.key === "ArrowUp") return "up";
  if (event.key === "Enter") return "open";
  if (event.key === "Escape") return "close";
  return null;
}

/** Does this keystroke open the palette? ⌘K or Ctrl-K, from anywhere. */
export function isOpenShortcut(event: {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
}): boolean {
  return (event.key === "k" || event.key === "K") &&
    Boolean(event.metaKey || event.ctrlKey);
}

/**
 * The parts of a title around every match, for highlighting.
 *
 * Case-insensitive to match the query's own ILIKE, and the needle is escaped
 * before it becomes a regex — a user searching for `a+b` or `(draft)` would
 * otherwise blow up the palette with a syntax error, which is the browser-side
 * twin of the LIKE-metacharacter defect this ticket fixed on the server.
 */
export function highlight(
  text: string,
  query: string,
): { text: string; match: boolean }[] {
  const needle = query.trim();
  if (!needle) return [{ text, match: false }];
  const pattern = new RegExp(
    `(${needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`,
    "ig",
  );
  return text
    .split(pattern)
    .filter((part) => part !== "")
    .map((part) => ({
      text: part,
      match: part.toLowerCase() === needle.toLowerCase(),
    }));
}

/** "Ops · #42", the one line that says where a hit lives. */
export function hitContext(hit: Hit): string {
  const parts = [hit.project_name, hit.task_number ? `#${hit.task_number}` : null]
    .filter(Boolean);
  return parts.join(" · ");
}
