/**
 * Projects · what the AI chat is focused on (owner direction 2026-09-23).
 *
 * The owner found a chat bound to one project confusing: a general question
 * had nowhere to go. So the chat ALWAYS reaches everything the member can
 * see, and the focus is a hint the model uses to read "this project" and
 * "here". It is never a boundary. The model resolves any other project or
 * space the member names with its own tools.
 *
 * **The focus follows the tree until the member picks.** Opening the chat
 * shows the node they selected last. Selecting another node moves the focus
 * with it. Once the member chooses in the header's picker, that choice stays,
 * so "Everything you can see" is not undone by the next click in the tree.
 *
 * Pure, so `chatScope.test.ts` pins the rules.
 */

/** The picker value for "every space you can see". */
export const EVERYTHING = "";

export interface ScopeEntry {
  id: string;
  name: string;
  /** 'space' | 'folder' | 'project' | 'subproject'. */
  level: string;
  depth: number;
  archived?: boolean;
}

export interface ScopeOption {
  value: string;
  label: string;
  depth?: number;
}

export function scopeOptions(entries: readonly ScopeEntry[]): ScopeOption[] {
  return [
    { value: EVERYTHING, label: "Everything you can see" },
    ...entries.map((e) => ({ value: e.id, label: e.name, depth: e.depth })),
  ];
}

export interface FocusState {
  focusId: string;
  /** True once the member chose in the picker. */
  pinned: boolean;
}

export type FocusEvent =
  | { type: "tree"; id: string | null }
  | { type: "pick"; id: string };

export function initialFocus(treeId: string | null | undefined): FocusState {
  return { focusId: treeId ?? EVERYTHING, pinned: false };
}

export function nextFocus(state: FocusState, event: FocusEvent): FocusState {
  if (event.type === "pick") return { focusId: event.id, pinned: true };
  if (state.pinned) return state;
  return { focusId: event.id ?? EVERYTHING, pinned: false };
}

/** The focused entry, or null for everything (or a node no longer visible). */
export function focusedEntry(
  state: FocusState,
  entries: readonly ScopeEntry[],
): ScopeEntry | null {
  if (state.focusId === EVERYTHING) return null;
  return entries.find((e) => e.id === state.focusId) ?? null;
}
