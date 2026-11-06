import { describe, expect, it } from "vitest";

import type { FocusLive } from "@/app/tasks/lib/focusLive";
import type { MyTask } from "@/app/tasks/lib/types";

import { clockText, idleOffer, ringCaption, ringFill, ringText, upNext } from "./nowCardRules";

/**
 * The Now card's rules (CC-4). Mutations, run by hand on 2026-11-07:
 *  - upNext without the `t.id !== currentId` test: the task in focus names
 *    itself as "Up next" and the first upNext test fails.
 *  - upNext without the `open` filter: a done block is "Up next", the second fails.
 *  - idleOffer ignoring the One Thing: the first idleOffer test fails.
 */
const NOW = Date.parse("2026-11-07T10:00:00.000Z");
const at = (mins: number) => new Date(NOW + mins * 60000).toISOString();
const task = (id: string, startMin: number | null, extra: Partial<MyTask> = {}): MyTask =>
  ({
    id,
    title: `Task ${id}`,
    disposition: "NEXT",
    scheduledStart: startMin === null ? undefined : at(startMin),
    scheduledEnd: startMin === null ? undefined : at(startMin + 30),
    ...extra,
  }) as MyTask;

const live = (p: Partial<FocusLive> = {}): FocusLive => ({
  itemId: "a",
  phase: "work",
  paused: false,
  remainingSec: 1122,
  elapsedSec: 378,
  targetSec: 1500,
  cycle: 2,
  modeLabel: "25 / 5",
  flow: false,
  ...p,
});

describe("the clock", () => {
  it("prints minutes and padded seconds, never below zero", () => {
    expect(clockText(1122)).toBe("18:42");
    expect(clockText(5)).toBe("0:05");
    expect(clockText(-3)).toBe("0:00");
  });
  it("a countdown shows what is left, Flow shows what is spent", () => {
    expect(ringText(live())).toBe("18:42");
    expect(ringText(live({ remainingSec: null, elapsedSec: 90, flow: true }))).toBe("1:30");
  });
  it("fills with the share of the segment spent, between 0 and 1", () => {
    expect(ringFill(live())).toBeCloseTo(0.252);
    expect(ringFill(live({ elapsedSec: 9999 }))).toBe(1);
    expect(ringFill(live({ targetSec: 0 }))).toBe(0);
  });
  it("names the state under the number", () => {
    expect(ringCaption(live())).toBe("of 25:00 · 25 / 5");
    expect(ringCaption(live({ paused: true }))).toBe("paused");
    expect(ringCaption(live({ phase: "break" }))).toBe("break");
    expect(ringCaption(live({ flow: true, remainingSec: null }))).toBe("flow, counting up");
  });
});

describe("up next", () => {
  it("is the next open block, never the task in focus", () => {
    const items = [task("a", -10), task("b", 60), task("c", 30)];
    expect(upNext(items, NOW, "a")?.id).toBe("c");
    expect(upNext([task("c", 30)], NOW, "c")).toBeNull();
  });
  it("skips a done block and a block that has started", () => {
    const items = [task("d", 20, { disposition: "DONE" }), task("p", -5), task("n", 45)];
    expect(upNext(items, NOW, null)?.id).toBe("n");
  });
  it("is null with nothing planned", () => {
    expect(upNext(undefined, NOW, null)).toBeNull();
    expect(upNext([], NOW, null)).toBeNull();
  });
});

describe("the idle offer", () => {
  it("takes the One Thing when it is open on today's list", () => {
    const items = [task("a", 5), task("one", 90)];
    expect(idleOffer(items, NOW, "one")?.id).toBe("one");
  });
  it("ignores a One Thing that is done or not on the list", () => {
    const items = [task("a", 5), task("one", 90, { disposition: "DONE" })];
    expect(idleOffer(items, NOW, "one")?.id).toBe("a");
    expect(idleOffer(items, NOW, "gone")?.id).toBe("a");
  });
  it("prefers the block that runs now, then the next, then any open one", () => {
    expect(idleOffer([task("later", 40), task("run", -10)], NOW, null)?.id).toBe("run");
    expect(idleOffer([task("later", 40), task("past", -90)], NOW, null)?.id).toBe("later");
    expect(idleOffer([task("loose", null)], NOW, null)?.id).toBe("loose");
  });
  it("offers nothing when today holds nothing open", () => {
    expect(idleOffer([], NOW, null)).toBeNull();
    expect(idleOffer([task("d", 5, { disposition: "DONE" })], NOW, null)).toBeNull();
  });
});
