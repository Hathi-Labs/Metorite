/**
 * The move of a card (`ai_studio.md` §14 D3, slice STU-D3).
 *
 * A plain store with no React import: the Board view reads it with
 * `useSyncExternalStore`, and `StudioBoard.test.ts` drives it with a fake
 * door. A drop moves the card at once. Exactly one `door.run(update, {id,
 * data: {[groupBy]: option}})` follows. A card with a call in flight is
 * locked, so a second drop on it does nothing. A refusal puts the card back
 * (the card returns to its last accepted lane, or leaves `placed` when it had none) and
 * writes the message of the door into `alert`.
 */
import { invalidate } from "@/lib/dataCache";
import type { Hold } from "./boardModel";
import { doorPrefix, type StudioDataDoor } from "./dataDoor";
import { readRefusal, statusLines } from "./formModel";
import { ACTION_FAILED } from "./TypedActionButton";

export interface MoverState {
  /** The cards that moved and wait for the door, with the option they moved to. */
  placed: Readonly<Record<string, string | null>>;
  /** The cards with a call in flight. */
  pending: ReadonlySet<string>;
  /** The refusal to show in a `role="alert"` line, or null. */
  alert: string | null;
  /** The sentence for the `aria-live` line. */
  live: string;
  /** The card that a pointer drag carries, or null. */
  dragId: string | null;
  /** The lane under the dragged card (it draws the drop ring), or null. */
  over: string | null;
  /** The card that the keyboard holds up, or null. */
  hold: Hold | null;
  /** The card that should take focus now (a keyboard move, a drop or a refusal), or null. */
  focusId: string | null;
}

export interface Mover {
  getState(): MoverState;
  subscribe(fn: () => void): () => void;
  /** Move card `id` from option `from` to option `to` (`null` is "No value"). */
  move(id: string, from: string | null, to: string | null, toTitle: string, opts?: { keyboard?: boolean }): Promise<void>;
  /** Say a sentence in the `aria-live` line. The store holds the only live text. */
  say(live: string): void;
  /** The view focused the card that `focusId` named. */
  clearFocus(): void;
  /** A drag of card `id` starts. */
  dragStart(id: string): void;
  /** The dragged card is over `lane`. Returns false when no drag runs. */
  dragOver(lane: string): boolean;
  /**
   * The dragged card left a lane. `outside` is true when the pointer went to
   * an element outside the lane. A leave with no related target (the window)
   * keeps the ring until the drag ends.
   */
  dragLeave(outside: boolean): void;
  /** The drag ended, dropped or not. The ring clears. */
  dragEnd(): void;
  /** A drop. Clears the drag, and returns the id that was dragged (or null). */
  drop(): string | null;
  /** The keyboard holds a card up, or lets go with null. */
  setHold(hold: Hold | null): void;
  /** Focus left the board: a held card drops its hold. */
  blur(): void;
  /** Drop the moves that the door has answered. The view calls it when rows arrive. */
  settle(): void;
}

/** The words of a refusal: the rule is named, and a plain message stays as the door wrote it. */
export function refusalText(res: { message?: unknown; data?: unknown } | null | undefined): string {
  const refusal = readRefusal(res?.data);
  const own = typeof res?.message === "string" && res.message.trim() ? res.message : null;
  const message = (refusal.rule ? refusal.message ?? own : own) ?? ACTION_FAILED;
  return statusLines([], { message, fieldErrors: refusal.fieldErrors, rule: refusal.rule }).join(" ");
}

export function createMover(door: StudioDataDoor, update: string, groupBy: string): Mover {
  let state: MoverState = { placed: {}, pending: new Set(), alert: null, live: "", dragId: null, over: null, hold: null, focusId: null };
  const listeners = new Set<() => void>();
  // The cards that the door accepted. They stay in `placed` until fresh rows arrive.
  const answered = new Set<string>();
  const set = (next: Partial<MoverState>) => {
    state = { ...state, ...next };
    listeners.forEach((fn) => fn());
  };
  const without = (id: string) => {
    const { [id]: _gone, ...rest } = state.placed;
    void _gone;
    return rest;
  };
  return {
    getState: () => state,
    subscribe(fn) {
      listeners.add(fn);
      return () => { listeners.delete(fn); };
    },
    dragStart: (id) => set({ dragId: id }),
    dragOver(lane) {
      if (state.dragId === null) return false;
      if (state.over !== lane) set({ over: lane });
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
    setHold: (hold) => set({ hold }),
    say: (live) => set({ live }),
    clearFocus() {
      if (state.focusId !== null) set({ focusId: null });
    },
    blur() {
      if (state.hold !== null) set({ hold: null, live: "Move cancelled" });
    },
    async move(id, from, to, toTitle, opts) {
      if (state.pending.has(id)) return;
      if (from === to) return;
      // The lane that the last accepted move gave the card, if any. A refusal returns to it.
      const had = id in state.placed;
      const prior = state.placed[id];
      const back = () => {
        if (!had) return without(id);
        return { ...state.placed, [id]: prior };
      };
      // The card leaves one lane list and joins another, so its node mounts anew. The keyboard keeps its place.
      const focus = opts?.keyboard ? id : null;
      set({
        placed: { ...state.placed, [id]: to },
        ...(focus !== null ? { focusId: focus } : {}),
        pending: new Set(state.pending).add(id),
        alert: null,
        live: `Moving to ${toTitle}`,
      });
      const done = (next: Partial<MoverState>) => {
        const pending = new Set(state.pending);
        pending.delete(id);
        set({ pending, ...next });
      };
      try {
        const res = await door.run(update, { id, data: { [groupBy]: to } });
        if (res && res.ok === true) {
          answered.add(id);
          invalidate(doorPrefix(door));
          done({ live: `Moved to ${toTitle}` });
          return;
        }
        const text = refusalText(res);
        done({ placed: back(), alert: text, live: `Move refused: ${text}`, ...(focus !== null ? { focusId: focus } : {}) });
      } catch {
        done({ placed: back(), alert: ACTION_FAILED, live: `Move refused: ${ACTION_FAILED}`, ...(focus !== null ? { focusId: focus } : {}) });
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
