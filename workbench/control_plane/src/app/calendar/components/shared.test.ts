import { describe, expect, it } from "vitest";
import type { GtdItem } from "@/app/tasks/lib/types";
import type { Block } from "@/app/tasks/lib/scheduling";
import type { DayTemplate } from "@/app/tasks/lib/api";
import { layoutBlocks, reservedWindowsForDay } from "./shared";

const item = (id: string): GtdItem =>
  ({ id, title: id, disposition: "NEXT" }) as GtdItem;
// Accepts decimal hours (11.5 → 11:30); the Date ctor truncates, so split it.
const hm = (dec: number): [number, number] => {
  const h = Math.floor(dec);
  return [h, Math.round((dec - h) * 60)];
};
const block = (id: string, startH: number, endH: number): Block => {
  const [sh, sm] = hm(startH);
  const [eh, em] = hm(endH);
  return {
    item: item(id),
    start: new Date(2099, 5, 15, sh, sm, 0),
    end: new Date(2099, 5, 15, eh, em, 0),
  };
};

describe("layoutBlocks", () => {
  it("gives non-overlapping blocks a single lane each", () => {
    const out = layoutBlocks([block("a", 9, 10), block("b", 11, 12)]);
    expect(out.every((o) => o.lanes === 1 && o.lane === 0)).toBe(true);
  });

  it("splits two overlapping blocks into two side-by-side lanes", () => {
    const out = layoutBlocks([block("a", 9, 11), block("b", 10, 12)]);
    expect(out.every((o) => o.lanes === 2)).toBe(true);
    expect(out.map((o) => o.lane).sort()).toEqual([0, 1]);
  });

  it("reuses a freed lane after a block ends (transitive overlap group)", () => {
    // a:9–11 overlaps b:10–13; c:11:30–14 overlaps b but not a → 2 lanes total,
    // and c can reuse a's lane since a has ended by 11:30.
    const out = layoutBlocks([
      block("a", 9, 11),
      block("b", 10, 13),
      block("c", 11.5, 14),
    ]);
    const lane = (id: string) => out.find((o) => o.block.item.id === id)!.lane;
    expect(out.every((o) => o.lanes === 2)).toBe(true);
    expect(lane("a")).toBe(0);
    expect(lane("b")).toBe(1);
    expect(lane("c")).toBe(0); // reclaimed a's lane
  });

  it("keeps a stable count independent of input order", () => {
    const a = layoutBlocks([block("a", 9, 11), block("b", 10, 12)]);
    const b = layoutBlocks([block("b", 10, 12), block("a", 9, 11)]);
    expect(a.length).toBe(b.length);
    expect(a.every((o) => o.lanes === 2)).toBe(true);
    expect(b.every((o) => o.lanes === 2)).toBe(true);
  });
});

describe("reservedWindowsForDay", () => {
  const tpl = (o: Partial<DayTemplate>): DayTemplate =>
    ({
      days: [],
      start_hour: 9,
      end_hour: 10,
      kind: "block",
      label: "",
      theme: "",
      ...o,
    }) as DayTemplate;
  const day = new Date(2099, 5, 15);
  const dow = day.getDay();
  const otherDow = (dow + 1) % 7;

  it("emits a Lunch block when a valid lunch window is set", () => {
    const out = reservedWindowsForDay(day, 13, 14, []);
    expect(out).toEqual([
      { startHour: 13, endHour: 14, label: "Lunch", kind: "block" },
    ]);
  });

  it("omits lunch when unset or zero-length", () => {
    expect(reservedWindowsForDay(day, undefined, undefined, [])).toEqual([]);
    expect(reservedWindowsForDay(day, 13, 13, [])).toEqual([]);
  });

  it("includes an every-day template and labels it", () => {
    const out = reservedWindowsForDay(day, undefined, undefined, [
      tpl({ start_hour: 18, end_hour: 19, label: "Gym" }),
    ]);
    expect(out).toEqual([
      { startHour: 18, endHour: 19, label: "Gym", kind: "block" },
    ]);
  });

  it("applies the day-of-week filter", () => {
    const onOther = reservedWindowsForDay(day, undefined, undefined, [
      tpl({ days: [otherDow], label: "X" }),
    ]);
    expect(onOther).toEqual([]);
    const onThis = reservedWindowsForDay(day, undefined, undefined, [
      tpl({ days: [dow], start_hour: 8, end_hour: 9, label: "X" }),
    ]);
    expect(onThis).toHaveLength(1);
  });

  it("keeps focus windows as kind 'focus' (not a hard block)", () => {
    const out = reservedWindowsForDay(day, undefined, undefined, [
      tpl({ kind: "focus", start_hour: 10, end_hour: 12, theme: "deep", label: "" }),
    ]);
    expect(out[0].kind).toBe("focus");
    expect(out[0].label).toBe("deep");
  });
});
