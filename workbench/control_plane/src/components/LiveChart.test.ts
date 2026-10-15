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
import LiveChart, { WEB_MAX_SCALE, buildChart, webScale, withHues } from "./LiveChart";
import { barChartSpec, TEMPLATE_REGISTRY } from "./genUITemplates";
import { KINDS, chartOption } from "@/lib/charts/kinds.mjs";

const SRC = join(__dirname, "..");

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
    expect(src).toMatch(/\[specKey, width, mode, maxHeight\]/);
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
