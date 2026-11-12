/**
 * The Now card's rules, all pure (My Day command centre, CC-4).
 *
 * The card has two states. A session runs: the ring, the clock and the three
 * acts. No session runs: one offer to start focus on the first task of today.
 * "Up next" reads the same calendar window as the Today card. Fence:
 * `nowCardRules.test.ts`.
 */
import type { MyTask } from "@/app/tasks/lib/types";
import type { FocusLive } from "@/app/tasks/lib/focusLive";

/** mm:ss for the ring. */
export function clockText(totalSec: number): string {
  const s = Math.max(0, Math.floor(totalSec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Ring fill, 0 to 1. A Flow session counts up against its planned length. */
export function ringFill(live: Pick<FocusLive, "elapsedSec" | "targetSec">): number {
  if (live.targetSec <= 0) return 0;
  return Math.min(1, Math.max(0, live.elapsedSec / live.targetSec));
}

/** The big number: time left in a countdown, time spent in Flow. */
export function ringText(live: Pick<FocusLive, "remainingSec" | "elapsedSec">): string {
  return clockText(live.remainingSec ?? live.elapsedSec);
}

/** The small line under the number, such as "of 25:00 - 2 of 2" or "paused". */
export function ringCaption(live: FocusLive): string {
  if (live.phase === "done") return "done";
  if (live.paused) return "paused";
  if (live.phase === "break") return "break";
  if (live.flow) return "flow, counting up";
  return `of ${clockText(live.targetSec)} · ${live.modeLabel}`;
}

const open = (t: MyTask) => t.disposition !== "DONE" && !t.completedAt;

const startMs = (t: MyTask) => (t.scheduledStart ? Date.parse(t.scheduledStart) : NaN);
const endMs = (t: MyTask) => (t.scheduledEnd ? Date.parse(t.scheduledEnd) : NaN);

/**
 * The block after now: the open item that starts next, never the task in
 * focus. Null when nothing else is planned today.
 */
export function upNext(
  items: readonly MyTask[] | undefined,
  nowMs: number,
  currentId: string | null,
): MyTask | null {
  const later = (items ?? [])
    .filter((t) => open(t) && t.id !== currentId && startMs(t) > nowMs)
    .sort((a, b) => startMs(a) - startMs(b));
  return later[0] ?? null;
}

/**
 * The task the idle card offers: the One Thing when it is open and on the
 * list, else the block that runs now, else the next block, else the first
 * open item. Null when today holds nothing open.
 */
export function idleOffer(
  items: readonly MyTask[] | undefined,
  nowMs: number,
  oneThingId: string | null,
): MyTask | null {
  const list = (items ?? []).filter(open);
  if (list.length === 0) return null;
  const one = oneThingId ? list.find((t) => t.id === oneThingId) : undefined;
  if (one) return one;
  const byStart = [...list].sort((a, b) => (startMs(a) || 0) - (startMs(b) || 0));
  const running = byStart.find((t) => startMs(t) <= nowMs && endMs(t) > nowMs);
  if (running) return running;
  return byStart.find((t) => startMs(t) > nowMs) ?? byStart[0];
}
