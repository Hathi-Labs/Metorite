/**
 * The move of a node of the Tree block (`ai_studio.md` §14 D18, slice D18-2b).
 *
 * A plain store with no React import, as `boardMove.ts` is. The Tree view reads it with
 * `useSyncExternalStore`, and `treeMove.test.ts` drives it with a fake door.
 *
 * A move first asks the model (`canMove` of `treeModel.ts`). A refusal of the model writes its
 * plain sentence into `alert`, and no call is made. Else the node moves at once on the screen
 * (`placed`), and exactly one `door.run(update, {id, data: {[field]: {id} | null}})` follows.
 * A node with a call in flight is locked, so a second move of it does nothing. A refusal of
 * the door puts the node back, and writes the message of the door into `alert`.
 *
 * `boardMove.ts` stays as it is. This file only reads its `refusalText`.
 */
import { invalidate } from "@/lib/dataCache";
import { refusalText } from "./boardMove";
import { doorPrefix, type StudioDataDoor } from "./dataDoor";
import { canMove, movePatch, moveRefusal, type TreeNodeIn } from "./treeModel";
import { ACTION_FAILED } from "./TypedActionButton";

/** The `over` value of the "Top level" drop target. A record id never holds these characters. */
export const TOP_OVER = "::top";

export interface TreeMoveState {
  /** The nodes that moved and wait for the door, with their new parent (null is the top level). */
  placed: Readonly<Record<string, string | null>>;
  /** The nodes with a call in flight. */
  pending: ReadonlySet<string>;
  /** The refusal to show in a `role="alert"` line, or null. */
  alert: string | null;
  /** The sentence for the `role="status"` line. */
  live: string;
  /** The node that a pointer drag carries, or null. */
  dragId: string | null;
  /** The row (or `TOP_OVER`) under the dragged node. It draws the drop ring. Null when none. */
  over: string | null;
}

export interface TreeMover {
  getState(): TreeMoveState;
  subscribe(fn: () => void): () => void;
  /**
   * Move node `id` under `parent` (null is the top level). `nodes` is the tree as the screen shows
   * it now. `labelOf` names a node for the status line. Resolves when the call is answered.
   */
  move(nodes: ReadonlyArray<TreeNodeIn>, id: string, parent: string | null, labelOf: (id: string) => string): Promise<void>;
  /** A drag of node `id` starts. A node with a call in flight does not drag. */
  dragStart(id: string): boolean;
  /** The dragged node is over `target` (a node id or `TOP_OVER`). Returns false when no drag runs. */
  dragOver(target: string): boolean;
  /** The pointer left a target for an element outside it. */
  dragLeave(outside: boolean): void;
  /** The drag ended, dropped or not. The ring clears. */
  dragEnd(): void;
  /** A drop. Clears the drag, and returns the id that was dragged (or null). */
  drop(): string | null;
  /** Drop the moves that the door has answered. The view calls it when fresh nodes arrive. */
  settle(): void;
}

/** The nodes as the screen shows them: each `placed` node under its new parent. Pure. */
export function placedNodes(
  nodes: ReadonlyArray<TreeNodeIn>, placed: Readonly<Record<string, string | null>>,
): ReadonlyArray<TreeNodeIn> {
  if (Object.keys(placed).length === 0) return nodes;
  return nodes.map((n) => {
    if (!(n.id in placed)) return n;
    const { orphan: _o, cycle: _c, ...rest } = n;
    void _o; void _c;
    return { ...rest, parentId: placed[n.id] };
  });
}

/** The sentence of a move that was accepted. */
export const movedLine = (label: string, parent: string | null): string =>
  parent === null ? `Moved ${label} to the top level.` : `Moved ${label} under ${parent}.`;

export function createTreeMover(door: StudioDataDoor, update: string, field: string): TreeMover {
  let state: TreeMoveState = { placed: {}, pending: new Set(), alert: null, live: "", dragId: null, over: null };
  const listeners = new Set<() => void>();
  // The nodes that the door accepted. They stay in `placed` until fresh nodes arrive.
  const answered = new Set<string>();
  const set = (next: Partial<TreeMoveState>) => {
    state = { ...state, ...next };
    listeners.forEach((fn) => fn());
  };
  return {
    getState: () => state,
    subscribe(fn) {
      listeners.add(fn);
      return () => { listeners.delete(fn); };
    },
    dragStart(id) {
      if (state.pending.has(id)) return false;
      set({ dragId: id });
      return true;
    },
    dragOver(target) {
      if (state.dragId === null) return false;
      if (state.over !== target) set({ over: target });
      return true;
    },
    dragLeave(outside) {
      if (outside && state.over !== null) set({ over: null });
    },
    dragEnd() {
      if (state.dragId !== null || state.over !== null) set({ dragId: null, over: null });
    },
    drop() {
      const id = state.dragId;
      set({ dragId: null, over: null });
      return id;
    },
    async move(nodes, id, parent, labelOf) {
      if (state.pending.has(id)) return;
      const check = canMove(nodes, id, parent);
      if (!check.ok) {
        const text = moveRefusal(check.reason);
        set({ alert: text, live: "" });
        return;
      }
      // The parent that the last accepted move gave the node, if any. A refusal returns to it.
      const had = id in state.placed;
      const prior = state.placed[id];
      const back = () => {
        if (!had) {
          const { [id]: _gone, ...rest } = state.placed;
          void _gone;
          return rest;
        }
        return { ...state.placed, [id]: prior };
      };
      const label = labelOf(id);
      set({
        placed: { ...state.placed, [id]: parent },
        pending: new Set(state.pending).add(id),
        alert: null,
        live: `Moving ${label}…`,
      });
      const done = (next: Partial<TreeMoveState>) => {
        const pending = new Set(state.pending);
        pending.delete(id);
        set({ pending, ...next });
      };
      try {
        const res = await door.run(update, movePatch(field, id, parent));
        if (res && res.ok === true) {
          answered.add(id);
          invalidate(doorPrefix(door));
          done({ live: movedLine(label, parent === null ? null : labelOf(parent)) });
          return;
        }
        // A restored parent is an accepted one: the next fresh nodes must drop it.
        if (had) answered.add(id);
        done({ placed: back(), alert: refusalText(res), live: "" });
      } catch {
        if (had) answered.add(id);
        done({ placed: back(), alert: ACTION_FAILED, live: "" });
      }
    },
    settle() {
      const keep = ([id]: [string, unknown]) => !answered.has(id) || state.pending.has(id);
      const placed = Object.fromEntries(Object.entries(state.placed).filter(keep));
      answered.clear();
      if (Object.keys(placed).length !== Object.keys(state.placed).length) set({ placed });
    },
  };
}
