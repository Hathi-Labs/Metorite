/**
 * Draw charts to PNG on the server (WS-47 WAC-10f). Node only.
 *
 * The WhatsApp bot sends a chart as an image. The gateway runs this file as a
 * child process: no port, no token, no network. It reads one JSON object
 * from stdin and writes one to stdout:
 *
 *   in:  {"charts": [<spec>, ...], "mode": "dark"}
 *   out: {"images": [{"png": "<base64>"} | {"error": "<what to fix>"}, ...]}
 *
 * ECharts draws each chart as SVG (its server-side renderer), and resvg turns
 * the SVG into a PNG with Geist, the app's own type. No browser, no canvas.
 * A 1080-pixel chart takes about 100 ms.
 *
 * The web chat imports `kinds.mjs` and draws the SAME option live in the
 * page, so the two surfaces show one chart, not two that look alike.
 */
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import * as echarts from "echarts";
import { Resvg } from "@resvg/resvg-js";
import { ChartSpecError, chartOption } from "./kinds.mjs";

const require = createRequire(import.meta.url);
// `geist/font/sans` is `dist/sans.js`. The TTF files sit beside it.
const GEIST = join(dirname(require.resolve("geist/font/sans")), "fonts", "geist-sans");
const FONTS = ["Regular", "Medium", "SemiBold", "Bold"].map((w) => join(GEIST, `Geist-${w}.ttf`));

/** The most charts one call draws, and the tallest drawing. */
export const MAX_CHARTS = 6;
const MAX_HEIGHT = 2400;

/**
 * One chart as a PNG.
 * @param {any} spec
 * @param {{ mode?: "dark" | "light" }} [opts]
 * @returns {Buffer}
 */
export function renderPng(spec, opts = {}) {
  const { option, width, height } = chartOption(spec, { mode: opts.mode ?? "dark" });
  const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width, height: Math.min(height, MAX_HEIGHT) });
  try {
    chart.setOption(option);
    const svg = chart.renderToSVGString();
    return new Resvg(svg, { font: { fontFiles: FONTS, loadSystemFonts: false, defaultFontFamily: "Geist" } })
      .render().asPng();
  } finally {
    // An undisposed chart keeps a timer, and the process never exits.
    chart.dispose();
  }
}

/** @param {string} raw */
export function handle(raw) {
  let req;
  try {
    req = JSON.parse(raw);
  } catch {
    return { error: "the request is not JSON" };
  }
  const charts = Array.isArray(req?.charts) ? req.charts.slice(0, MAX_CHARTS) : [];
  return {
    images: charts.map((spec) => {
      try {
        return { png: renderPng(spec, { mode: req.mode }).toString("base64") };
      } catch (err) {
        // A spec error says what to fix. Anything else is the engine's own.
        return { error: err instanceof ChartSpecError ? err.message : `the chart could not be drawn (${err?.name ?? "Error"})` };
      }
    }),
  };
}

async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  process.stdout.write(JSON.stringify(handle(Buffer.concat(chunks).toString("utf8"))));
}

// Run as a script (`node render.mjs`), not when a test imports it.
if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1].replace(/\\/g, "/").replace(/^\/?/, "/")}`).href) {
  main().catch(() => process.exit(1));
}
