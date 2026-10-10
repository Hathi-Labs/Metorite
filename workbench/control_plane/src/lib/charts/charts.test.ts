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
import { ChartSpecError, KINDS, chartOption, fmt } from "./kinds.mjs";
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
  it("writes short numbers", () => {
    expect([fmt(999), fmt(1200), fmt(34000), fmt(1500000), fmt(2.345)]).toEqual(["999", "1.2k", "34k", "1.5M", "2.35"]);
  });
});

describe("wac10f-spec-refusals", () => {
  const refused: [string, Record<string, unknown>, RegExp][] = [
    ["no type", { title: "t" }, /"type" must be one of/],
    ["an unknown type", { type: "pie3d", title: "t" }, /"type" must be one of/],
    ["no title", { type: "bar", labels: ["a"], values: [1] }, /"title" is required/],
    ["values that miss a label", { type: "bar", title: "t", labels: ["a", "b"], values: [1] }, /one value per label/],
    ["text for a number", { type: "line", title: "t", labels: ["a"], values: ["lots"] }, /must be a number/],
    ["too many labels", { type: "bar", title: "t", labels: Array(40).fill("a"), values: Array(40).fill(1) }, /1 to 31/],
    ["a donut of nothing", { type: "donut", title: "t", labels: ["a"], values: [0] }, /total above zero/],
    ["a stacked area of one", { type: "area", title: "t", labels: ["a"], series: [{ name: "x", values: [1] }] }, /2 to 6/],
    ["a bad date", { type: "gantt", title: "t", rows: [{ label: "x", start: "soon" }] }, /date like/],
    ["a calendar a century long", { type: "calendar", title: "t", days: [["1926-01-01", 1], ["2026-01-01", 2]] }, /spans at most 400 days/],
    ["a kind from the prototype", { type: "constructor", title: "t" }, /"type" must be one of/],
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
  it("hostile text stays text", () => {
    const png: Buffer = renderPng({ type: "bar", title: "<script>alert(1)</script>", labels: ["</text><x>"], values: [1] });
    expect([...png.subarray(0, 4)]).toEqual(PNG);
  });
});
