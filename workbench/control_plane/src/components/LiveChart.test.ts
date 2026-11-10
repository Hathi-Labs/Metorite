/**
 * The web chat's live chart (WS-47 WAC-10g).
 *
 * R7 fences named here:
 *   • `wac10g-one-chart`: the chat's `chart` template and its `barChart`
 *     template both draw through `lib/charts/kinds.mjs`, the engine that
 *     draws the WhatsApp images. No second bar chart.
 *   • `wac10g-live`: a live chart has a tooltip, no "Metorite" footer, and
 *     the page's own font. A bad spec shows its reason, never a blank card.
 *   • `wac10g-every-kind-loads`: `lib/charts/echarts.ts` registers every
 *     series and component that a kind uses, so no kind draws blank in the
 *     browser while it draws fine on the server.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import LiveChart, { WEB_MAX_SCALE, buildChart, chartScrollProps, webScale, withHues } from "./LiveChart";
import { barChartSpec, TEMPLATE_REGISTRY } from "./genUITemplates";
import { KINDS, chartOption } from "@/lib/charts/kinds.mjs";

const SRC = join(__dirname, "..");
type Spec = Record<string, unknown>;

describe("wac10g-one-chart", () => {
  it("the chat offers the chart template, and barChart draws through the engine", () => {
    expect(Object.keys(TEMPLATE_REGISTRY)).toContain("chart");
    const tpl = readFileSync(join(SRC, "components", "genUITemplates.tsx"), "utf8");
    // Both chart templates draw through `ChartCard`, which draws the one
    // engine: in a chat card under the card's header (follow-up of #945).
    expect(tpl).toMatch(/barChart: \(data\) => <ChartCard spec=\{barChartSpec\(data\)\} \/>/);
    expect(tpl).toMatch(/chart: \(data\) => <ChartCard spec=\{data\} \/>/);
    const card = tpl.slice(tpl.indexOf("function ChartCard("), tpl.indexOf("// ── Registry"));
    expect(card.match(/<LiveChart /g)?.length).toBe(2);
    expect(tpl).not.toMatch(/function BarChart\(/);
  });
  it("maps the barChart data and its tones onto a bar spec", () => {
    const spec = barChartSpec({ title: "Hours", unit: "h", bars: [
      { label: "A", value: 3, tone: "danger" }, { label: "B", value: "2" }] });
    expect(spec).toEqual({ type: "bar", title: "Hours", unit: "h", labels: ["A", "B"], values: [3, 2], tones: ["red", "blue"] });
    expect(() => chartOption(spec)).not.toThrow();
  });
});

describe("wac10g-live", () => {
  const spec = { type: "line", title: "Closed", labels: ["W1", "W2"], values: [1, 2] };
  it("has a tooltip, no footer and the page's font", () => {
    const built = buildChart(spec, 600, "light", "Geist-from-next");
    if ("error" in built) throw new Error(built.error);
    const o = built.option as Record<string, any>;
    expect(o.tooltip).toBeTruthy();
    expect(o.graphic.some((g: any) => g.style?.text === "Metorite")).toBe(false);
    expect(o.title.textStyle.fontFamily).toBe("Geist-from-next");
    expect(built.height).toBeLessThan(760);
  });
  it("a bad spec reads as its reason", () => {
    const built = buildChart({ type: "bar", title: "t", labels: ["a", "b"], values: [1] }, 600, "dark");
    expect(built).toEqual({ error: expect.stringMatching(/one value per label/) });
  });
  it("a status word becomes statusAccent's hue before the engine sees it", () => {
    const spec = withHues({ type: "bar", tones: ["On hold", "Done", "red"], rows: [{ label: "x", status: "In progress" }] });
    expect(spec.tones).toEqual(["amber", "green", "red"]);
    expect((spec.rows as { status: string }[])[0].status).toBe("blue");
  });
  it("a chart with no title still draws, for the barChart template", () => {
    expect("error" in buildChart({ type: "bar", labels: ["a"], values: [1] }, 600, "dark")).toBe(false);
  });
});

describe("wac10g-review", () => {
  const src = readFileSync(join(SRC, "components", "LiveChart.tsx"), "utf8");
  it("keys the drawn chart by the spec's content, so a streamed token never redraws it", () => {
    expect(src).toMatch(/const specKey = JSON\.stringify\(spec\)/);
    // `maxHeight` is a number, so it keys by value too (report fix round 2).
    // `numbering` is a string, the member's number system (§13.10).
    expect(src).toMatch(/\[specKey, width, mode, maxHeight, numbering\]/);
    expect(src).not.toMatch(/\[spec, /);
  });
  it("sets no px font size: the type scale follows the member's density", () => {
    expect(src).not.toMatch(/fontSize:\s*\d/);
  });
  it("a chart with no title is shorter by its header, and no more", () => {
    for (const type of ["gantt", "progress", "donut", "bar"]) {
      const spec: Record<string, unknown> = type === "gantt"
        ? { type, rows: [{ label: "a", start: "2026-10-01", end: "2026-10-05" }] }
        : type === "progress"
          ? { type, labels: ["a", "b", "c", "d", "e", "f"], values: [1, 2, 3, 4, 5, 6] }
          : { type, labels: ["a", "b"], values: [1, 2] };
      const titled = chartOption({ ...spec, title: "T" }).height;
      const untitled = chartOption(spec).height;
      expect(titled - untitled, type).toBe(150 - 56);
    }
  });
});

describe("wac10g-every-kind-loads", () => {
  // The series type each kind draws, and the components it needs.
  const SERIES: Record<string, string> = {
    bar: "BarChart", line: "LineChart", area: "LineChart", donut: "PieChart", progress: "BarChart",
    scatter: "ScatterChart", heatmap: "HeatmapChart", radar: "RadarChart", box: "BoxplotChart",
    waterfall: "BarChart", funnel: "FunnelChart", calendar: "HeatmapChart", gantt: "CustomChart",
  };
  const reg = readFileSync(join(SRC, "lib", "charts", "echarts.ts"), "utf8");
  it("names a series for every kind", () => {
    expect(Object.keys(SERIES).sort()).toEqual(Object.keys(KINDS).sort());
  });
  for (const [kind, series] of Object.entries(SERIES)) {
    it(`${kind}: its series is registered`, () => expect(reg).toContain(series));
  }
  for (const comp of ["CalendarComponent", "PolarComponent", "RadarComponent", "VisualMapComponent",
    "MarkLineComponent", "GraphicComponent", "TooltipComponent", "LegendComponent", "SVGRenderer"]) {
    it(`registers ${comp}`, () => expect(reg).toMatch(new RegExp(`echarts\\.use\\(\\[[\\s\\S]*${comp}`)));
  }
});

// ── The report's visual review, 2026-10-16: a chart in a wide chat column ──
//
// The engine sizes its type for a 1080-pixel image, and the web chat scaled
// that drawing with the column. At 768 pixels the axis labels drew near 17 px
// and the title near 28 px, twice the card's text. The web scale is capped,
// so a wide chart is a wider drawing at the same type size, and a card caps
// the height. No CSS transform draws a chart: ECharts lays it out at the box.
describe("a chart in a wide box keeps the chat's type size", () => {
  const bar = { type: "bar", title: "Revenue", labels: ["Jul", "Aug"], values: [1, 2] };
  const fontOf = (width: number) => {
    const built = buildChart(bar, width, "dark");
    if ("error" in built) throw new Error(built.error);
    type Axis = { axisLabel?: { fontSize?: number } };
    const o = built.option as { title: { textStyle: { fontSize: number } }; xAxis: Axis; yAxis: Axis };
    return { title: o.title.textStyle.fontSize, axis: o.xAxis.axisLabel?.fontSize ?? o.yAxis.axisLabel?.fontSize, height: built.height };
  };

  it("caps the scale, and a narrow box still scales down", () => {
    expect(webScale(1600)).toBe(WEB_MAX_SCALE);
    expect(webScale(768)).toBe(WEB_MAX_SCALE);
    expect(webScale(367)).toBeCloseTo(367 / 1080);
    expect(WEB_MAX_SCALE).toBeLessThanOrEqual(0.5);
  });

  it("the type is the same at 768 and at 1600, and no bigger than the chat's text", () => {
    const mid = fontOf(768), wide = fontOf(1600);
    expect(mid.title).toBe(wide.title);
    expect(mid.axis).toBe(wide.axis);
    expect(mid.title).toBeLessThanOrEqual(18);
    expect(mid.axis).toBeLessThanOrEqual(12);
    expect(mid.height).toBe(wide.height);
  });

  it("a card caps the height, and the chart is shorter, not cut", () => {
    const built = buildChart(bar, 768, "dark", undefined, 240);
    if ("error" in built) throw new Error(built.error);
    expect(built.height).toBe(240);
  });

  it("a donut's centre stays over its hole in a wide box", () => {
    const built = buildChart({ type: "donut", labels: ["a", "b"], values: [3, 4] }, 1600, "dark");
    if ("error" in built) throw new Error(built.error);
    const texts = (built.option as { graphic: { x?: number; style?: { text?: string } }[] }).graphic.filter((g) => g.style?.text === "7");
    expect(texts[0].x).toBeCloseTo(1600 * 0.31, 0);
  });

  it("the box names its height cap and draws no transform", () => {
    const html = renderToStaticMarkup(createElement(LiveChart, { spec: bar, maxHeight: 240 }));
    expect(html).toContain('data-chart-max-height="240"');
    expect(html).not.toMatch(/transform|scale\(/);
    expect(readFileSync(join(SRC, "components", "LiveChart.tsx"), "utf8")).not.toMatch(/transform\s*[:=]|scale\(/);
  });
});

// ── Review of #1065, P1: a cap must never move a mark off its row ─────────
//
// A kind that lays its plot out inside the box (a grid, a pie, a radar) is
// drawn at the capped height, and each graphic is placed from that height.
// A kind that grows by its rows (progress, gantt, heatmap, funnel, calendar,
// a long bar) places marks at fixed row steps, so it is never capped: it
// keeps its own height, and the box scrolls.
describe("every kind under a card's height cap", () => {
  const UNDER: Record<string, Spec> = {
    bar: { type: "bar", labels: ["A", "B"], values: [3, 1] },
    "bar-long": { type: "bar", labels: Array.from({ length: 12 }, (_, i) => `A long project name ${i}`), values: Array.from({ length: 12 }, (_, i) => i + 1) },
    line: { type: "line", labels: ["W1", "W2", "W3"], values: [1, 4, 2] },
    area: { type: "area", labels: ["W1", "W2"], series: [{ name: "Done", values: [1, 2] }, { name: "To do", values: [4, 3] }] },
    "donut-counts": { type: "donut", title: "Hours", labels: ["Build", "Meet", "Ops"], values: [18, 9, 4] },
    "donut-shares": { type: "donut", labels: ["A", "B"], values: [60, 40], unit: "%" },
    progress: { type: "progress", labels: ["Tasks"], values: [23], totals: [30] },
    "progress-8": { type: "progress", labels: Array.from({ length: 8 }, (_, i) => `Team ${i}`), values: Array.from({ length: 8 }, (_, i) => i + 2), totals: Array.from({ length: 8 }, () => 10) },
    scatter: { type: "scatter", groups: [{ name: "Ops", points: [[1, 2], [3, 5, 4]] }] },
    heatmap: { type: "heatmap", x: ["9a", "10a"], y: ["Mon", "Tue"], values: [[1, 2], [3, 4]] },
    radar: { type: "radar", axes: ["Price", "Speed", "Terms"], series: [{ name: "A", values: [8, 6, 9] }], max: 10 },
    box: { type: "box", groups: [{ name: "Ops", values: [2, 3, 5, 8] }] },
    waterfall: { type: "waterfall", steps: [{ label: "Open", value: 42 }, { label: "Sales", value: 31 }, { label: "Close", total: true }] },
    funnel: { type: "funnel", labels: ["Leads", "Won"], values: [240, 12] },
    calendar: { type: "calendar", days: [["2026-10-01", 3], ["2026-10-02", 0], ["2026-10-05", 7]] },
    gantt: { type: "gantt", rows: [{ label: "Design", start: "2026-09-01", end: "2026-09-20" }] },
    "gantt-15": { type: "gantt", rows: Array.from({ length: 15 }, (_, i) => ({ label: `Step ${i}`, start: "2026-09-01", end: `2026-09-${String(10 + i).padStart(2, "0")}` })) },
  };
  const GROWS = new Set(["bar-long", "progress", "progress-8", "heatmap", "funnel", "calendar", "gantt", "gantt-15"]);
  type G = { top?: unknown; y?: unknown; type?: string; style?: { text?: string } };
  const build = (spec: Spec, cap?: number) => {
    const b = buildChart(spec, 700, "dark", undefined, cap);
    if ("error" in b) throw new Error(b.error);
    return b;
  };

  it("names every kind of the engine", () => {
    const kinds = new Set(Object.values(UNDER).map((s) => String(s.type)));
    expect([...kinds].sort()).toEqual(Object.keys(KINDS).sort());
  });

  for (const [name, spec] of Object.entries(UNDER)) {
    it(`${name}: every mark lies inside the box drawn`, () => {
      const capped = build(spec, 240);
      const full = build(spec);
      if (GROWS.has(name)) {
        // It keeps its own layout and height: no row moves, and the box scrolls.
        expect(capped.fits, name).toBe(false);
        expect(capped.height).toBe(full.height);
        expect(JSON.stringify(capped.option)).toBe(JSON.stringify(full.option));
        return;
      }
      expect(capped.fits, name).toBe(true);
      expect(capped.height).toBeLessThanOrEqual(240);
      const graphics = ((capped.option as { graphic?: G[] }).graphic ?? []);
      for (const g of graphics) {
        const y = typeof g.top === "number" ? g.top : typeof g.y === "number" ? g.y : null;
        if (y !== null) {
          expect(y, `${name} ${g.style?.text}`).toBeGreaterThanOrEqual(0);
          expect(y, `${name} ${g.style?.text}`).toBeLessThanOrEqual(capped.height);
        }
      }
    });
  }

  it("a donut of counts puts its total over the hole of the capped pie", () => {
    const capped = build(UNDER["donut-counts"], 240);
    const total = ((capped.option as { graphic: G[] }).graphic).find((g) => g.style?.text === "31") as { y: number };
    // The pie's centre is 58% down the box DRAWN, and the total sits 46 units
    // above it. Placed from the uncapped height, it sat 35 px low, on the ring.
    expect(total.y).toBeCloseTo(capped.height * 0.58 - 46 * WEB_MAX_SCALE, 0);
  });

  it("a progress of 8 rows and a gantt of 15 keep each caption beside its bar", () => {
    for (const name of ["progress-8", "gantt-15"]) {
      expect(build(UNDER[name], 240).fits, name).toBe(false);
    }
  });

  it("the box scrolls a chart that keeps its own height, and never cuts it", () => {
    const html = renderToStaticMarkup(createElement(LiveChart, { spec: UNDER["gantt-15"], maxHeight: 240 }));
    expect(html).toMatch(/max-height:240px;overflow-y:auto/);
  });
});

// ── Re-review of #1065, P3: a chart box that scrolls is reachable by keys ──
// Safari does not put a scrolling box in the Tab order by itself, and a
// macOS or iOS scrollbar hides until it scrolls. So a growing chart's box
// takes the MarkdownBody table's pattern, and says that it scrolls.
describe("a chart box that scrolls", () => {
  it("is a labelled region in the Tab order, only when it scrolls", () => {
    expect(chartScrollProps(false, 240, "Plan")).toEqual({ tabIndex: 0, role: "region", "aria-label": "Plan. Scroll for more rows." });
    expect(chartScrollProps(false, 240, "")).toEqual({ tabIndex: 0, role: "region", "aria-label": "Chart. Scroll for more rows." });
    // A chart that fits, or one with no cap, does not scroll: no Tab stop.
    expect(chartScrollProps(true, 240, "Plan")).toBeNull();
    expect(chartScrollProps(false, undefined, "Plan")).toBeNull();
    expect(chartScrollProps(null, 240, "Plan")).toBeNull();
  });

  it("LiveChart spreads them on its box, and draws the hint under it", () => {
    const src = readFileSync(join(SRC, "components", "LiveChart.tsx"), "utf8");
    expect(src).toContain("{...(scrollProps ?? {})}");
    expect(src).toContain("data-chart-scroll-hint");
  });
});
