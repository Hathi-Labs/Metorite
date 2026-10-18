/**
 * The Metorite chart language (WS-47 WAC-10f).
 *
 * R7 fences named here:
 *   • `wac10f-one-palette`: every chart colour is a token of `themes.ts`, or a
 *     `--cat-n` hue lifted for a chart. A drift fails here, not on a phone.
 *   • `wac10f-every-kind-draws`: each kind in `KINDS` builds and draws a PNG.
 *   • `wac10f-spec-refusals`: a bad spec is refused with what to fix.
 */
import { describe, expect, it } from "vitest";
import { THEME } from "@/lib/theme/themes";
import { CAT_HUES, SERIES_SLOTS, hsl, theme, tone } from "./theme.mjs";
import { ChartSpecError, KINDS, chartOption, fmt, isShare } from "./kinds.mjs";
import { classify, handle, renderPng } from "./render.mjs";

const PNG = [0x89, 0x50, 0x4e, 0x47];

/** `hsl(215 85% 61%)` as [215, 85, 61]. */
function parts(token: string): number[] {
  const m = token.match(/hsl\(([\d.]+) ([\d.]+)% ([\d.]+)%\)/);
  if (!m) throw new Error(`not an hsl token: ${token}`);
  return m.slice(1).map(Number);
}
const hex = (token: string) => {
  const [h, s, l] = parts(token);
  return hsl(h, s, l);
};

describe("wac10f-one-palette", () => {
  for (const mode of ["dark", "light"] as const) {
    const c = THEME.colors[mode] as Record<string, string>;
    const t = theme(mode);
    it(`${mode}: the surface and text are the app's tokens`, () => {
      expect(t.card).toBe(hex(c.card));
      expect(t.bg).toBe(hex(c.background));
      expect(t.fg).toBe(hex(c.foreground));
      expect(t.muted).toBe(hex(c.mutedForeground));
    });
    it(`${mode}: each status hue is the app's status token`, () => {
      expect(t.status.green).toBe(hex(c.success));
      expect(t.status.amber).toBe(hex(c.warning));
      expect(t.status.red).toBe(hex(c.destructive));
      expect(t.status.blue).toBe(hex(c.info));
      expect(t.status.violet).toBe(hex(c.violet));
      expect(t.status.gray).toBe(hex(c.mutedForeground));
    });
  }
  it("each series colour is a --cat-n hue", () => {
    const dark = THEME.colors.dark as Record<string, string>;
    for (const slot of SERIES_SLOTS) {
      expect((CAT_HUES as Record<number, number>)[slot]).toBe(parts(dark[`cat-${slot}`])[0]);
    }
  });
  it("a tone names one of statusAccent's six hues, and a status WORD names none", () => {
    // A status word is mapped by statusAccent (web) or whatsapp_cards.hue
    // (WhatsApp) before it reaches a chart: this file holds no second list.
    const t = theme("dark");
    expect(tone(t, "amber")).toBe(t.status.amber);
    expect(tone(t, " Violet ")).toBe(t.status.violet);
    for (const word of ["On hold", "overdue", "In progress", "#ff00ff", "purple"]) expect(tone(t, word)).toBeNull();
  });
});

const SPECS: Record<string, Record<string, unknown>> = {
  bar: { title: "Overdue", labels: ["A", "B"], values: [3, 1], tones: ["red", "amber"] },
  line: { title: "Closed", labels: ["W1", "W2", "W3"], values: [1, 4, 2], unit: "%" },
  area: { title: "Work", labels: ["W1", "W2"], series: [{ name: "Done", values: [1, 2] }, { name: "To do", values: [4, 3] }] },
  donut: { title: "Hours", labels: ["Build", "Meet"], values: [18, 9] },
  progress: { title: "Week", labels: ["Tasks"], values: [23], totals: [30] },
  scatter: { title: "Est vs actual", groups: [{ name: "Ops", points: [[1, 2], [3, 5, 4]] }] },
  heatmap: { title: "Hours", x: ["9a", "10a"], y: ["Mon", "Tue"], values: [[1, 2], [3, 4]] },
  radar: { title: "Vendors", axes: ["Price", "Speed", "Terms"], series: [{ name: "A", values: [8, 6, 9] }], max: 10 },
  box: { title: "Lead time", groups: [{ name: "Ops", values: [2, 3, 5, 8] }] },
  waterfall: { title: "Cash", steps: [{ label: "Open", value: 42 }, { label: "Sales", value: 31 }, { label: "Close", total: true }] },
  funnel: { title: "Pipeline", labels: ["Leads", "Won"], values: [240, 12] },
  calendar: { title: "Streak", days: [["2026-10-01", 3], ["2026-10-02", 0], ["2026-10-05", 7]] },
  gantt: { title: "Plan", today: "2026-10-11", rows: [{ label: "Design", start: "2026-09-01", end: "2026-09-20", progress: 100, status: "green" }] },
};

describe("wac10f-every-kind-draws", () => {
  it("covers every kind the AI is told about", () => {
    expect(Object.keys(SPECS).sort()).toEqual(Object.keys(KINDS).sort());
  });
  for (const [type, spec] of Object.entries(SPECS)) {
    it(`${type} draws a PNG`, () => {
      const png: Buffer = renderPng({ type, ...spec });
      expect([...png.subarray(0, 4)]).toEqual(PNG);
    });
  }
  it("scales every size with the drawing's width", () => {
    const wide = chartOption({ type: "bar", ...SPECS.bar }, { width: 1080 });
    const narrow = chartOption({ type: "bar", ...SPECS.bar }, { width: 540 });
    expect(narrow.height).toBe(Math.round(wide.height / 2));
    expect(narrow.option.title.textStyle.fontSize).toBe(wide.option.title.textStyle.fontSize / 2);
  });
  it("a long label turns the bars sideways", () => {
    const { option } = chartOption({ type: "bar", title: "t", labels: ["A very long project name", "B"], values: [1, 2] });
    expect(option.yAxis.type).toBe("category");
  });
  it("labels a stacked column with its total", () => {
    const { option } = chartOption({ type: "bar", title: "t", labels: ["Q1"], stacked: true,
      series: [{ name: "a", values: [10] }, { name: "b", values: [1] }] });
    const top = option.series[1].label;
    expect(top.formatter({ value: 1, dataIndex: 0 })).toBe("11");
  });
  it("draws a waterfall step as a bar from the old total to the new, below zero too", () => {
    const { option } = chartOption({ type: "waterfall", title: "t", steps: [
      { label: "Open", value: 10 }, { label: "Loss", value: -30 }, { label: "Close", total: true }] });
    expect(option.series[0].data).toEqual([[0, 0, 10], [1, -20, 10], [2, -20, 0]]);
  });
  it("a funnel's share is of its first stage, and a sliver reads <1%", () => {
    const { option } = chartOption({ type: "funnel", title: "t", labels: ["Leads", "Won"], values: [10000, 12] });
    const f = option.series[0].label.formatter;
    expect(f({ name: "Won", value: 12 })).toContain("<1%");
    expect(f({ name: "Leads", value: 10000 })).toContain("100%");
  });
  it("writes short numbers, in lakh and crore by default", () => {
    expect([fmt(999), fmt(1200), fmt(34000), fmt(1250000), fmt(2.345), fmt(-3)])
      .toEqual(["999", "1.2K", "34K", "12.5\u00a0L", "2.35", "\u22123"]);
    expect([fmt(1200, "international"), fmt(34000, "international"), fmt(1500000, "international")])
      .toEqual(["1.2K", "34K", "1.5M"]);
  });
});

describe("wac10f-spec-refusals", () => {
  const refused: [string, Record<string, unknown>, RegExp][] = [
    ["no type", { title: "t" }, /"type" must be one of/],
    ["an unknown type", { type: "pie3d", title: "t" }, /"type" must be one of/],
    ["values that miss a label", { type: "bar", title: "t", labels: ["a", "b"], values: [1] }, /one value per label/],
    ["text for a number", { type: "line", title: "t", labels: ["a"], values: ["lots"] }, /must be a number/],
    ["too many labels", { type: "bar", title: "t", labels: Array(40).fill("a"), values: Array(40).fill(1) }, /1 to 31/],
    ["a donut of nothing", { type: "donut", title: "t", labels: ["a"], values: [0] }, /total above zero/],
    ["a stacked area of one", { type: "area", title: "t", labels: ["a"], series: [{ name: "x", values: [1] }] }, /2 to 6/],
    ["a bad date", { type: "gantt", title: "t", rows: [{ label: "x", start: "soon" }] }, /date like/],
    ["a calendar a century long", { type: "calendar", title: "t", days: [["1926-01-01", 1], ["2026-01-01", 2]] }, /spans at most 400 days/],
    ["a kind from the prototype", { type: "constructor", title: "t" }, /"type" must be one of/],
    ["a stacked area below zero", { type: "area", title: "t", labels: ["a"], series: [{ name: "x", values: [-1] }, { name: "y", values: [1] }] }, /no negative value/],
    ["a date that rolls over", { type: "calendar", title: "t", days: [["2026-02-30", 1]] }, /date like/],
    ["a funnel below zero", { type: "funnel", title: "t", labels: ["a", "b"], values: [5, -1] }, /no negative value/],
    ["an end before its start", { type: "gantt", title: "t", rows: [{ label: "x", start: "2026-10-10", end: "2026-10-01" }] }, /on or after/],
  ];
  for (const [what, spec, says] of refused) {
    it(`refuses ${what}`, () => {
      expect(() => chartOption(spec)).toThrow(ChartSpecError);
      expect(() => chartOption(spec)).toThrow(says);
    });
  }
  it("the child-process door answers each chart, and a bad one with its reason", () => {
    const out = handle(JSON.stringify({ charts: [{ type: "bar", ...SPECS.bar }, { type: "bar", title: "t" }] }));
    expect(out.images[0].png.length).toBeGreaterThan(1000);
    expect(out.images[1].error).toMatch(/"labels" must be a list/);
    expect(handle("not json").error).toMatch(/not JSON/);
  });
  it("a fault inside the engine is a fault, never a spec error for the model", () => {
    expect(classify(new TypeError("boom"))).toEqual({ fault: "TypeError" });
    expect(classify(new ChartSpecError("fix this"))).toEqual({ error: "fix this" });
  });
  it("a live tooltip draws text, never HTML", () => {
    for (const type of Object.keys(SPECS)) {
      const { option } = chartOption({ type, ...SPECS[type] }, { interactive: true });
      expect(option.tooltip.renderMode).toBe("richText");
    }
    const { option } = chartOption({ type: "gantt", ...SPECS.gantt, rows: [{ label: "<img src=x onerror=alert(1)>", start: "2026-09-01" }] }, { interactive: true });
    const text = option.series[0].tooltip.formatter({ value: [0] });
    expect(text).not.toContain("<br");
    expect(text.startsWith("<img")).toBe(true); // drawn as text by richText, not parsed
  });
  it("hostile text stays text", () => {
    const png: Buffer = renderPng({ type: "bar", title: "<script>alert(1)</script>", labels: ["</text><x>"], values: [1] });
    expect([...png.subarray(0, 4)]).toEqual(PNG);
  });
});

// The report's visual review, 2026-10-16: a split in percent drew "100 total"
// in the hole of its donut. A share has no centre. Any other donut shows its
// real total, with its unit.
describe("the donut's centre", () => {
  const centre = (spec: Record<string, unknown>) =>
    (chartOption({ type: "donut", title: "Mix", labels: ["a", "b"], ...spec }).option.graphic as { style?: { text?: string } }[])
      .map((g) => g.style?.text)
      .filter((t) => t !== "Metorite");

  it("draws no total for shares in percent", () => {
    expect(isShare("%")).toBe(true);
    expect(isShare(" % ")).toBe(true);
    expect(centre({ values: [52, 48], unit: "%" })).toEqual([]);
  });

  it("draws the real total with its unit for anything else", () => {
    expect(isShare("h")).toBe(false);
    expect(isShare(undefined)).toBe(false);
    expect(centre({ values: [18, 9], unit: "h" })).toEqual(["27h", "total"]);
    expect(centre({ values: [18, 9] })).toEqual(["27", "total"]);
    expect(centre({ values: [60, 40], center: "hours" })).toEqual(["100", "hours"]);
  });
});

// Owner, 2026-10-19: lakh and crore by default, million and billion by
// choice (whatsapp_assistant_channel.md §13.10). Every place a kind writes a
// number goes through the chart's one formatter, so no "M" survives.
describe("numbering: every number a chart writes follows it", () => {
  const BIG = 13500000; // 1.35 Cr, or 13.5M
  const texts = (o: unknown): string[] => {
    const out: string[] = [];
    const walk = (v: unknown) => {
      if (typeof v === "string") out.push(v);
      else if (Array.isArray(v)) v.forEach(walk);
      else if (v && typeof v === "object") Object.values(v).forEach(walk);
    };
    walk(o);
    return out;
  };

  it("an axis, a bar label, a line's end label and the tooltip", () => {
    for (const type of ["bar", "line"] as const) {
      const spec = { type, title: "t", labels: ["a", "b"], values: [BIG, 1] };
      const indian = chartOption(spec, { interactive: true }).option;
      const intl = chartOption({ ...spec, numbering: "international" }, { interactive: true }).option;
      const axis = (o: typeof indian) => (type === "bar" ? o.yAxis : o.yAxis).axisLabel.formatter;
      expect(axis(indian)(BIG)).toBe("1.35\u00a0Cr");
      expect(axis(intl)(BIG)).toBe("13.5M");
      const label = (o: typeof indian) => (type === "bar" ? o.series[0].label : o.series[0].endLabel).formatter;
      expect(label(indian)({ value: BIG, dataIndex: 0 })).toBe("1.35\u00a0Cr");
      expect(label(intl)({ value: BIG, dataIndex: 0 })).toBe("13.5M");
      expect(indian.tooltip.valueFormatter(BIG)).toBe("1.35\u00a0Cr");
      expect(intl.tooltip.valueFormatter(BIG)).toBe("13.5M");
    }
  });

  it("a donut's total, a progress caption, a waterfall step and a funnel stage", () => {
    const donut = (n?: string) => texts(chartOption({ type: "donut", title: "t", labels: ["a", "b"], values: [BIG, BIG], numbering: n }).option.graphic);
    expect(donut()).toContain("2.7\u00a0Cr");
    expect(donut("international")).toContain("27M");
    const progress = (n?: string) => texts(chartOption({ type: "progress", title: "t", labels: ["a"], values: [250000], totals: [BIG], numbering: n }).option.graphic);
    expect(progress()).toContain("2.5\u00a0L of 1.35\u00a0Cr");
    expect(progress("international")).toContain("250K of 13.5M");
    const funnel = (n?: string) => chartOption({ type: "funnel", title: "t", labels: ["a"], values: [BIG], numbering: n }).option.series[0].label.formatter({ name: "a", value: BIG });
    expect(funnel()).toContain("1.35\u00a0Cr");
    expect(funnel("international")).toContain("13.5M");
  });

  it("the axes of a scatter and a box plot", () => {
    const scatter = chartOption({ type: "scatter", title: "t", groups: [{ name: "g", points: [[1, BIG]] }] }).option;
    expect(scatter.yAxis.axisLabel.formatter(BIG)).toBe("1.35\u00a0Cr");
    const box = chartOption({ type: "box", title: "t", groups: [{ name: "g", values: [1, 2, BIG] }], numbering: "international" }).option;
    expect(box.yAxis.axisLabel.formatter(BIG)).toBe("13.5M");
  });

  it("the viewer's numbering wins over the spec's, and a bad value is the default", () => {
    const spec = { type: "bar", title: "t", labels: ["a"], values: [BIG], numbering: "indian" };
    expect(chartOption(spec, { numbering: "international" }).option.yAxis.axisLabel.formatter(BIG)).toBe("13.5M");
    expect(chartOption({ ...spec, numbering: "roman" }).option.yAxis.axisLabel.formatter(BIG)).toBe("1.35\u00a0Cr");
  });

  it("a PNG reads the spec's numbering, which the server stamps", () => {
    const one = renderPng({ type: "bar", title: "t", labels: ["a"], values: [BIG], numbering: "international" });
    const two = renderPng({ type: "bar", title: "t", labels: ["a"], values: [BIG] });
    expect([...one.subarray(0, 4)]).toEqual(PNG);
    expect(Buffer.compare(one, two)).not.toBe(0);
  });
});
