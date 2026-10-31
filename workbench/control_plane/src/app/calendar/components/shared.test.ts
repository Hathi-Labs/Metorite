import { describe, expect, it } from "vitest";
import type { MyTask } from "@/app/tasks/lib/types";
import type { Block } from "@/app/tasks/lib/scheduling";
import type { DayTemplate } from "@/app/tasks/lib/api";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  blockLayout,
  blockLines,
  elapsedPct,
  fmtClockShort,
  fmtRange,
  layoutBlocks,
  leftText,
  plannerCandidates,
  reservedWindowsForDay,
} from "./shared";

const item = (id: string): MyTask =>
  ({ id, title: id, disposition: "NEXT" }) as MyTask;
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

/**
 * D-PM-38 (Subtasks S4) — the rail's candidate rule against the gateway's,
 * through ONE table. `tests/fixtures/subtask_planner_parity.json` is also read
 * by `tests/live/live_ws39_subtask_planner.py`, which runs
 * `planning.LENS_SOURCE.candidates` on Postgres.
 */
interface ParityTask {
  key: string;
  parent?: string;
  mine?: boolean;
  state?: "open" | "done" | "backlog" | "trash" | "archived";
  stated?: "INBOX" | "SOMEDAY" | "WAITING";
  scheduled?: boolean;
}
const PLANNER = JSON.parse(
  readFileSync(
    fileURLToPath(
      new URL("../../../../../../tests/fixtures/subtask_planner_parity.json", import.meta.url),
    ),
    "utf8",
  ),
) as { cases: { name: string; tasks: ParityTask[]; candidates: string[] }[] };

/** One fixture task as the store holds it. The disposition is the effective
 *  one the gateway sends (`effective_disposition`): TRASH for my trash, DONE
 *  for a closed lane, then what I stated, then SOMEDAY for a backlog lane,
 *  NEXT for my open work and WAITING for somebody else's. */
const effective = (t: ParityTask, mine: boolean, state: string): MyTask["disposition"] => {
  if (state === "trash") return "TRASH";
  if (state === "done") return "DONE";
  if (t.stated) return t.stated;
  if (state === "backlog") return "SOMEDAY";
  return mine ? "NEXT" : "WAITING";
};
const storeTask = (t: ParityTask): MyTask => {
  const mine = t.mine ?? true;
  const state = t.state ?? "open";
  return {
    id: t.key,
    title: t.key,
    source: "LOCAL",
    isMine: mine,
    parentItemId: t.parent,
    disposition: effective(t, mine, state),
    archivedAt: state === "archived" ? "2026-09-20T09:00:00Z" : undefined,
    scheduledStart: t.scheduled ? "2026-09-28T09:00:00Z" : undefined,
    scheduledEnd: t.scheduled ? "2026-09-28T10:00:00Z" : undefined,
    createdAt: "2026-09-20T09:00:00Z",
    updatedAt: "2026-09-20T09:00:00Z",
  } as MyTask;
};

describe("plannerCandidates — the rail and the planner pack the same tasks", () => {
  it("reads a non-empty table", () => {
    expect(PLANNER.cases.length).toBeGreaterThanOrEqual(8);
  });

  it.each(PLANNER.cases)("$name", ({ tasks, candidates }) => {
    const got = plannerCandidates(tasks.map(storeTask)).map((t) => t.id);
    expect([...got].sort()).toEqual([...candidates].sort());
  });
});

describe("blockLines — a one-hour step block fits (review of #492)", () => {
  it("under 90 minutes a step block puts its crumb on the clock line and drops the ribbon", () => {
    expect(blockLines(60, true)).toEqual({ crumb: "inline", outcome: false });
    expect(blockLines(30, true)).toEqual({ crumb: "inline", outcome: false });
  });
  it("from 90 minutes a step block gives the crumb its own line and keeps the ribbon", () => {
    expect(blockLines(90, true)).toEqual({ crumb: "own", outcome: true });
  });
  it("a plain block keeps the ribbon from 45 minutes, as before", () => {
    expect(blockLines(44, false)).toEqual({ crumb: null, outcome: false });
    expect(blockLines(60, false)).toEqual({ crumb: null, outcome: true });
  });
});

describe("blockLayout — what a card draws, by length and real width", () => {
  it("draws one row under 45 minutes, and two rows from 45", () => {
    expect(blockLayout(30, 400, false).rows).toBe("one");
    expect(blockLayout(44, 400, false).rows).toBe("one");
    expect(blockLayout(45, 400, false).rows).toBe("two");
    expect(blockLayout(89, 400, false).rows).toBe("two");
    expect(blockLayout(90, 400, false).rows).toBe("three");
  });

  it("keeps the full range on a wide one-row card", () => {
    expect(blockLayout(30, 400, false)).toMatchObject({
      rangeOnTitle: "full",
      focus: false,
      chips: false,
    });
  });

  it("shortens the range, then drops it, as the card narrows", () => {
    expect(blockLayout(30, 200, false).rangeOnTitle).toBe("compact");
    expect(blockLayout(30, 110, false).rangeOnTitle).toBe(false);
    expect(blockLayout(30, 76, false).rangeOnTitle).toBe(false);
  });

  it("drops the Focus label, then the range, then the chips, then the icon", () => {
    const now = { isNow: true };
    expect(blockLayout(30, 520, false, now)).toMatchObject({
      focus: "label",
      rangeOnTitle: "full",
      chips: true,
    });
    // 300 px: the label goes first, and the range after it.
    expect(blockLayout(30, 300, false, now)).toMatchObject({
      focus: "icon",
      rangeOnTitle: false,
      chips: true,
    });
    // 160 px: the chip goes, the icon stays.
    expect(blockLayout(30, 160, false, now)).toMatchObject({
      focus: "icon",
      chips: false,
    });
    // A week card of 92 px or a lane of 76 px: the title keeps the room.
    expect(blockLayout(30, 92, false, now)).toMatchObject({
      focus: false,
      rangeOnTitle: false,
      chips: false,
    });
    expect(blockLayout(30, 76, false, now).focus).toBe(false);
  });

  it("drops the Overlaps chip on a narrow overlapping pair", () => {
    const o = { overlaps: true };
    expect(blockLayout(30, 400, false, o).chips).toBe(true);
    // 163 px, a day view of two lanes at 390 px: the title is not cut.
    expect(blockLayout(30, 163, false, o).chips).toBe(false);
  });

  it("keeps a title room of 48 px or 40% of the card", () => {
    const widths = [76, 92, 120, 163, 200, 300, 480, 900];
    for (const w of widths) {
      for (const opts of [{}, { isNow: true }, { overlaps: true }]) {
        const l = blockLayout(30, w, false, opts);
        const used =
          34 +
          (l.focus === "label" ? 62 : l.focus === "icon" ? 26 : 0) +
          (l.rangeOnTitle === "full" ? 102 : l.rangeOnTitle === "compact" ? 68 : 0) +
          (l.chips
            ? ("isNow" in opts ? 94 : 0) + ("overlaps" in opts ? 68 : 0)
            : 0);
        expect(w - used).toBeGreaterThanOrEqual(Math.min(48, w - 34));
        if (used > 34) expect(w - used).toBeGreaterThanOrEqual(Math.max(48, w * 0.4));
      }
    }
  });

  it("keeps the outcome from 45 minutes on a plain block, as blockLines does", () => {
    expect(blockLayout(45, 400, false).outcome).toBe("inline");
    expect(blockLayout(60, 400, false).outcome).toBe("inline");
    expect(blockLayout(60, 150, false).outcome).toBe(false); // too narrow
    expect(blockLayout(90, 400, false).outcome).toBe("own");
    // A step under 90 minutes gives way, as in blockLines.
    expect(blockLayout(60, 400, true).outcome).toBe(false);
  });

  it("keeps the crumb of a step on the meta row under 90 minutes", () => {
    expect(blockLayout(45, 400, true).crumb).toBe("inline");
    expect(blockLayout(60, 400, true).crumb).toBe("inline");
    expect(blockLayout(90, 400, true).crumb).toBe("own");
    expect(blockLayout(60, 400, false).crumb).toBe(null);
  });

  it("drops the check circle from a card under 90 px, and gives the room to the title", () => {
    expect(blockLayout(30, 92, false).check).toBe(true);
    expect(blockLayout(30, 89, false).check).toBe(false);
    expect(blockLayout(60, 44, false).check).toBe(false);
  });

  it("sets the meta row by width", () => {
    expect(blockLayout(60, 300, false)).toMatchObject({
      metaRange: "full",
      duration: true,
      energy: true,
    });
    expect(blockLayout(60, 120, false)).toMatchObject({
      metaRange: "compact",
      duration: false,
      energy: false,
    });
  });

  it("keeps a Focus button on a two-row card by the room it has", () => {
    expect(blockLayout(60, 300, false, { isNow: true }).focus).toBe("label");
    expect(blockLayout(60, 130, false, { isNow: true }).focus).toBe("icon");
    expect(blockLayout(60, 80, false, { isNow: true }).focus).toBe(false);
  });

  it("lets the title wrap from 120 minutes on a card that is wide enough", () => {
    expect(blockLayout(119, 400, false).wrapTitle).toBe(false);
    expect(blockLayout(120, 400, false).wrapTitle).toBe(true);
    expect(blockLayout(120, 100, false).wrapTitle).toBe(false);
  });
});

describe("fmtClockShort — the pill of the gutter", () => {
  it("drops the period, so the pill fits the gutter", () => {
    expect(fmtClockShort(new Date(2099, 5, 15, 12, 59), "en-US")).toBe("12:59");
    expect(fmtClockShort(new Date(2099, 5, 15, 4, 39), "en-US")).toBe("4:39");
    expect(fmtClockShort(new Date(2099, 5, 15, 16, 5), "en-GB")).toBe("16:05");
  });
});

describe("fmtRange — the time range of a card", () => {
  const at = (h: number, m: number) => new Date(2099, 5, 15, h, m);
  it("drops the start period when the end shares it", () => {
    expect(fmtRange(at(4, 26), at(5, 26), { locale: "en-US" })).toBe(
      "4:26 – 5:26 AM",
    );
  });
  it("keeps both periods across noon", () => {
    expect(fmtRange(at(11, 30), at(12, 30), { locale: "en-US" })).toBe(
      "11:30 AM – 12:30 PM",
    );
  });
  it("has a compact form for a narrow card", () => {
    expect(fmtRange(at(16, 26), at(16, 56), { compact: true, locale: "en-US" })).toBe(
      "4:26–4:56",
    );
  });
  it("keeps a 24-hour clock without a period", () => {
    expect(fmtRange(at(16, 26), at(17, 26), { locale: "en-GB" })).toBe(
      "16:26 – 17:26",
    );
  });
});

describe("elapsedPct and leftText — the Now card", () => {
  const start = new Date(2099, 5, 15, 4, 26);
  const end = new Date(2099, 5, 15, 4, 56);
  it("is the percent of the block that has passed", () => {
    expect(elapsedPct(start, end, new Date(2099, 5, 15, 4, 41))).toBe(50);
    expect(elapsedPct(start, end, new Date(2099, 5, 15, 4, 26))).toBe(0);
  });
  it("clamps before the start and after the end", () => {
    expect(elapsedPct(start, end, new Date(2099, 5, 15, 3, 0))).toBe(0);
    expect(elapsedPct(start, end, new Date(2099, 5, 15, 9, 0))).toBe(100);
  });
  it("is 0 for a block with no length", () => {
    expect(elapsedPct(start, start, start)).toBe(0);
  });
  it("names the minutes left, rounded up", () => {
    expect(leftText(end, new Date(2099, 5, 15, 4, 44))).toBe("12m left");
    expect(leftText(end, new Date(2099, 5, 15, 4, 43, 30))).toBe("13m left");
    expect(leftText(end, new Date(2099, 5, 15, 3, 51))).toBe("1h 5m left");
    expect(leftText(end, end)).toBe("<1m left");
  });
});
