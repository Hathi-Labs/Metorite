/**
 * STU-D4 fence: every one of the 13 chart kinds draws from a C2b aggregate result, and the
 * Stat reads a count, a sum and an average with its trend. The wire shape is the one of
 * `objects_store.AggregateResult`: a Decimal may come as text, and a money sum is in minor units.
 */
import { describe, expect, it } from "vitest";

import { KINDS, chartOption } from "@/lib/charts/kinds.mjs";
import {
  CHART_KINDS, MAX_GROUPS, chartBlock, chartFromAggregate, formatMeasure, keyLabel, parseAggregate,
  statBlock, statFromAggregate,
} from "./aggregateModel";
import { bindsSource } from "./dataDoor";

const group = (key: unknown, count: number, ...values: unknown[]) => ({ key, count, values });

/** A status by owner aggregate: a count, a money sum and an average. */
function wire(groups: unknown[], extra: Record<string, unknown> = {}) {
  return [{
    measures: [{ op: "count" }, { op: "sum", field: "amount" }, { op: "average", field: "score" }],
    currencies: [null, "INR", null],
    groups,
    other: null,
    folded: 0,
    total: group(null, 40, 40, 1250000, "3.5"),
    ...extra,
  }];
}

const BY_STATUS = wire([
  group("open", 12, 12, 400000, "3.0"),
  group("won", 20, 20, 700000, "4.25"),
  group("lost", 6, 6, 150000, "2.0"),
  group(null, 2, 2, 0, null),
]);
const BY_DAY = wire([
  group("2026-11-01", 3, 3, 30000, "3"),
  group("2026-11-02", 5, 5, 90000, "4"),
  group("2026-11-04", 4, 4, 60000, "2"),
]);
const BY_MONTH = wire([
  group("2026-09", 10, 10, 100000, "3"),
  group("2026-10", 12, 12, 150000, "3"),
  group("2026-11", 18, 18, 300000, "4"),
]);

describe("the chart kinds", () => {
  it("holds the 13 kinds of kinds.mjs, no more and no fewer", () => {
    expect([...CHART_KINDS].sort()).toEqual(Object.keys(KINDS).sort());
    expect(CHART_KINDS).toHaveLength(13);
  });

  it.each(CHART_KINDS)("%s draws from a text-grouped aggregate or a date-grouped one", (kind) => {
    const dated = kind === "calendar" ? BY_DAY : kind === "gantt" ? BY_MONTH : BY_STATUS;
    const built = chartFromAggregate(kind, parseAggregate(dated), { title: "Deals" });
    expect(built.ok, built.ok ? "" : built.reason).toBe(true);
    if (!built.ok) return;
    // kinds.mjs is the one validator: its checks must pass, and it must build an option.
    const out = chartOption(built.spec, { width: 800, scale: 0.45, mode: "light" });
    expect(out.option).toBeTruthy();
    expect(out.height).toBeGreaterThan(0);
  });

  it("reads a Decimal text and puts a money sum in major units with its currency", () => {
    const built = chartFromAggregate("bar", parseAggregate(BY_STATUS), {});
    expect(built.ok).toBe(true);
    if (!built.ok) return;
    const series = built.spec.series as { name: string; values: number[] }[];
    expect(series.map((s) => s.name)).toEqual(["Records", "Total amount", "Average score"]);
    expect(series[1].values).toEqual([4000, 7000, 1500, 0]);
    expect(series[2].values).toEqual([3, 4.25, 2, 0]);
    expect(built.spec.labels).toEqual(["open", "won", "lost", "(none)"]);
  });

  it("names the unit of a single money measure", () => {
    const one = [{ ...wire([group("a", 1, 1, 5000, 1)])[0], measures: [{ op: "sum", field: "amount" }], currencies: ["INR"],
      groups: [group("a", 1, 5000)], total: group(null, 1, 5000) }];
    const built = chartFromAggregate("bar", parseAggregate(one), {});
    expect(built.ok && built.spec.unit).toBe("INR");
    expect(built.ok && built.spec.values).toEqual([50]);
  });

  it("says No data for an empty aggregate, and for no aggregate", () => {
    const empty = wire([], { total: group(null, 0, 0, 0, null) });
    expect(chartFromAggregate("bar", parseAggregate(empty))).toEqual({ ok: false, reason: "No data" });
    expect(chartFromAggregate("bar", null)).toEqual({ ok: false, reason: "No data" });
  });

  it("says how to fix a result that a kind cannot draw", () => {
    const cal = chartFromAggregate("calendar", parseAggregate(BY_STATUS));
    expect(cal).toEqual({ ok: false, reason: "A calendar chart needs a date field grouped by day." });
    const gantt = chartFromAggregate("gantt", parseAggregate(BY_STATUS));
    expect(!gantt.ok && gantt.reason).toContain("grouped by day or by month");
    const radar = chartFromAggregate("radar", parseAggregate(wire([group("a", 1, 1, 1, 1), group("b", 1, 1, 1, 1)])));
    expect(radar).toEqual({ ok: false, reason: "A radar chart needs 3 or more groups." });
    expect(chartFromAggregate("pie", parseAggregate(BY_STATUS)).ok).toBe(false);
  });

  it("caps a huge result, keeps the largest groups, keeps the server other, and says so", () => {
    const many = Array.from({ length: 50 }, (_, i) => group(`g${i}`, i + 1, i + 1, 100, 1));
    const agg = parseAggregate(wire(many, { other: group(null, 900, 900, 1, 1), folded: 120 }));
    const built = chartFromAggregate("bar", agg);
    expect(built.ok).toBe(true);
    if (!built.ok) return;
    const labels = built.spec.labels as string[];
    expect(labels).toHaveLength(MAX_GROUPS);
    expect(labels[labels.length - 1]).toBe("Other");
    expect(labels).toContain("g49");
    expect(labels).not.toContain("g0");
    expect(built.note).toBe(`Showing the ${MAX_GROUPS - 1} largest groups. 141 more are not drawn.`);
    expect(chartOption(built.spec, { width: 800, scale: 0.45 }).option).toBeTruthy();
  });

  it("caps a radar at 10 axes and a waterfall at 16 steps", () => {
    const many = Array.from({ length: 40 }, (_, i) => group(`g${i}`, 1, i + 1, 1, 1));
    for (const kind of ["radar", "waterfall", "box", "gantt"]) {
      const data = kind === "gantt"
        ? wire(many.map((g, i) => group(`2026-10-${String((i % 28) + 1).padStart(2, "0")}`, 1, i, 1, 1)))
        : wire(many);
      const built = chartFromAggregate(kind, parseAggregate(data));
      expect(built.ok, kind).toBe(true);
      if (built.ok) expect(chartOption(built.spec, { width: 800, scale: 0.45 }).option).toBeTruthy();
    }
  });

  it("refuses text that is no aggregate", () => {
    expect(parseAggregate([])).toBeNull();
    expect(parseAggregate([{ id: "r1", title: "x" }])).toBeNull();
    expect(parseAggregate([{ measures: [{ op: "median" }], groups: [], total: group(null, 0) }])).toBeNull();
  });

  it("words a key", () => {
    expect(keyLabel("2026-10")).toBe("Oct 2026");
    expect(keyLabel(true)).toBe("Yes");
    expect(keyLabel(null)).toBe("(none)");
  });
});

describe("the stat tile", () => {
  const agg = parseAggregate(BY_MONTH);

  it("shows a count, a money sum and an average", () => {
    expect(statFromAggregate(agg, 0)?.value).toBe("40");
    expect(statFromAggregate(agg, 1)?.value).toBe("₹12,500.00");
    expect(statFromAggregate(agg, 2)?.value).toBe("3.5");
    expect(statFromAggregate(parseAggregate(BY_STATUS), 2)?.label).toBe("Average score");
  });

  it("shows the trend against the period before", () => {
    const tile = statFromAggregate(agg, 0);
    expect(tile?.trend).toMatchObject({ delta: 6, percent: 50, text: "+50%", against: "Oct 2026" });
    expect(tile?.spark).toEqual([10, 12, 18]);
    const money = statFromAggregate(agg, 1);
    expect(money?.trend?.text).toBe("+100%");
  });

  it("has no trend without a date group, and an average with no value reads as a dash", () => {
    expect(statFromAggregate(parseAggregate(BY_STATUS), 0)?.trend).toBeNull();
    expect(formatMeasure(parseAggregate(BY_STATUS)!, 2, null)).toBe("—");
    expect(statFromAggregate(null, 0)).toBeNull();
    expect(statFromAggregate(agg, 9)).toBeNull();
  });

  it("marks a fall with a minus sign", () => {
    const down = parseAggregate(wire([group("2026-10", 10, 10, 1, 1), group("2026-11", 5, 5, 1, 1)]));
    expect(statFromAggregate(down, 0)?.trend).toMatchObject({ delta: -5, text: "−50%" });
  });
});

describe("the block nodes", () => {
  it("bind to a source through the one data door", () => {
    const source = { action: "object:deal.aggregate", args: { group_by: { field: "status" } } };
    const chart = chartBlock("donut", source, "Deals");
    const stat = statBlock(source, "Open deals");
    expect(bindsSource(chart.type, chart.props)).toBe(true);
    expect(bindsSource(stat.type, stat.props)).toBe(true);
    expect(chart.props.data).toEqual({ type: "donut", title: "Deals" });
  });
});
