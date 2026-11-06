// The running focus session, as the rest of the app may read it (CC-4).
//
// The timer lives in `FocusRoom` (component state: segment, pause, cycles).
// My Day's Now card must show the same clock and drive the same Pause, Done
// and +10 min, without a second timer. So the room PUBLISHES a snapshot each
// second and registers its three commands here, and the card reads and calls
// them. One session, one clock, two views. Memory only.

import { useSyncExternalStore } from "react";

export interface FocusLive {
  itemId: string;
  /** "done" once the task is completed inside the room. */
  phase: "work" | "break" | "done";
  paused: boolean;
  /** Seconds left in a counting-down segment. null for Flow, which counts up. */
  remainingSec: number | null;
  /** Seconds since the segment began. */
  elapsedSec: number;
  /** The segment's length in seconds, with any +10 min added. */
  targetSec: number;
  /** 1-based pomodoro number. */
  cycle: number;
  /** The mode's label, such as "25 / 5". */
  modeLabel: string;
  flow: boolean;
}

export interface FocusCommands {
  togglePause(): void;
  /** Complete the task and close the session. */
  complete(): void;
  extend10(): void;
}

let live: FocusLive | null = null;
let commands: FocusCommands | null = null;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

export function publishFocus(next: FocusLive, cmds: FocusCommands): void {
  commands = cmds;
  const same =
    live !== null &&
    live.itemId === next.itemId &&
    live.phase === next.phase &&
    live.paused === next.paused &&
    live.remainingSec === next.remainingSec &&
    live.elapsedSec === next.elapsedSec &&
    live.targetSec === next.targetSec &&
    live.cycle === next.cycle &&
    live.modeLabel === next.modeLabel;
  if (same) return;
  live = next;
  emit();
}

export function clearFocusLive(itemId: string): void {
  if (live?.itemId !== itemId) return;
  live = null;
  commands = null;
  emit();
}

export function focusCommands(): FocusCommands | null {
  return commands;
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

/** The live session, or null. The server render and the first paint read null. */
export function useFocusLive(): FocusLive | null {
  return useSyncExternalStore(
    subscribe,
    () => live,
    () => null,
  );
}
