/**
 * The chart kinds — a SHORT spec in, a full ECharts option out (WAC-10f).
 *
 * The AI writes only the data (`type`, `title`, labels, values). This file
 * adds everything else: the grid, the type, the colour, the labels. That is
 * where the tokens go: a 12-point line is ~54 tokens of spec against ~362 of
 * ECharts option and ~1665 of SVG (measured 2026-10-11).
 *
 * `check` is the ONE validator of a spec, for the web chat and for WhatsApp.
 * A refusal says what to fix, in words a model can act on.
 *
 * Every size is in units of a 1080-pixel-wide drawing. `scale` maps them to
 * the real width, so the same chart fits a phone image and a chat bubble.
 */
import { alpha, theme, tone } from "./theme.mjs";

export class ChartSpecError extends Error {}

/** The chart kinds, and the data each one takes. The AI reads this list. */
export const KINDS = {
  bar: 'labels, values (or series), tones, unit, stacked',
  line: 'labels, values (or series: [{name, values}]), unit',
  area: 'labels, series: [{name, values}] (stacked)',
  donut: 'labels, values, tones, center',
  progress: 'labels, values (done), totals',
  scatter: 'groups: [{name, points: [[x, y, size?]]}], x_name, y_name',
  heatmap: 'x: [col], y: [row], values: [[n per col] per row]',
  radar: 'axes, series: [{name, values}], max',
  box: 'groups: [{name, values: [n]}]',
  waterfall: 'steps: [{label, value} | {label, total: true}]',
  funnel: 'labels, values',
  calendar: 'days: [["2026-10-01", n]]',
  gantt: 'rows: [{label, start, end, progress, status}], today',
};

const LIMITS = { labels: 31, series: 6, points: 400, rows: 15, cells: 400, days: 400, text: 60, title: 90 };

// ── The checks ──────────────────────────────────────────────────────────────

function fail(msg) { throw new ChartSpecError(msg); }

function text(v, what, max = LIMITS.text, required = true) {
  if (v === undefined || v === null || v === "") {
    if (required) fail(`"${what}" is required`);
    return "";
  }
  if (typeof v !== "string" && typeof v !== "number") fail(`"${what}" must be text`);
  const s = String(v).replace(/\s+/g, " ").trim();
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

function num(v, what) {
  const n = typeof v === "string" && v.trim() !== "" ? Number(v) : v;
  if (typeof n !== "number" || !Number.isFinite(n)) fail(`"${what}" must be a number`);
  return /** @type {number} */ (n);
}

function list(v, what, { min = 1, max = LIMITS.labels } = {}) {
  if (!Array.isArray(v) || v.length < min || v.length > max) {
    fail(`"${what}" must be a list of ${min} to ${max}`);
  }
  return /** @type {any[]} */ (v);
}

function nums(v, what, n) {
  const out = list(v, what, { max: LIMITS.points }).map((x, i) => num(x, `${what}[${i}]`));
  if (n !== undefined && out.length !== n) fail(`"${what}" must have one value per label (${n})`);
  return out;
}

function labels(spec, key = "labels") {
  return list(spec[key], key).map((l, i) => text(l, `${key}[${i}]`, 24));
}

/** `values` or `series`, as a list of named series of the labels' length. */
function series(spec, n, { min = 1 } = {}) {
  if (spec.series !== undefined) {
    const ss = list(spec.series, "series", { min, max: LIMITS.series });
    return ss.map((s, i) => {
      if (!s || typeof s !== "object") fail(`"series[${i}]" must be {name, values}`);
      return { name: text(s.name ?? `Series ${i + 1}`, `series[${i}].name`, 24), values: nums(s.values, `series[${i}].values`, n) };
    });
  }
  if (min > 1) fail(`"series" must hold ${min} or more series`);
  return [{ name: text(spec.name ?? spec.title ?? "", "name", 24, false), values: nums(spec.values, "values", n) }];
}

const DATE = /^\d{4}-\d{2}-\d{2}$/;
function date(v, what) {
  if (typeof v !== "string" || !DATE.test(v) || Number.isNaN(Date.parse(v))) fail(`"${what}" must be a date like 2026-10-11`);
  return Date.parse(/** @type {string} */ (v));
}

// ── The look ────────────────────────────────────────────────────────────────

/** @typedef {{ mode?: "dark" | "light", scale?: number, footer?: boolean }} Opts */

const DAY = 86400000;

/** Short numbers: 1.2k, 34k, 1.5M. */
export function fmt(v) {
  const a = Math.abs(v);
  if (a >= 1e6) return `${(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(a >= 1e4 ? 0 : 1)}k`;
  return Number.isInteger(v) ? `${v}` : `${+v.toFixed(2)}`;
}

function ctx(spec, opts) {
  const t = theme(opts.mode);
  const k = opts.scale ?? 1;
  const u = (/** @type {number} */ n) => Math.round(n * k * 10) / 10;
  const unit = text(spec.unit ?? "", "unit", 8, false);
  const label = (v) => `${fmt(v)}${unit}`;
  const axisText = { color: t.muted, fontFamily: t.font, fontSize: u(24) };
  const hasSub = Boolean(spec.subtitle);
  const top = hasSub ? 190 : 150;
  return { t, u, unit, label, axisText, top, k };
}

function base(spec, opts, c) {
  return {
    backgroundColor: c.t.card,
    color: c.t.series,
    animation: Boolean(opts.animate),
    textStyle: { fontFamily: c.t.font, color: c.t.fg },
    title: {
      text: text(spec.title, "title", LIMITS.title), subtext: text(spec.subtitle ?? "", "subtitle", 120, false),
      left: c.u(56), top: c.u(48), itemGap: c.u(14),
      textStyle: { fontFamily: c.t.font, fontSize: c.u(40), fontWeight: 600, color: c.t.fg },
      subtextStyle: { fontFamily: c.t.font, fontSize: c.u(25), color: c.t.muted },
    },
    grid: { left: c.u(56), right: c.u(56), top: c.u(c.top), bottom: c.u(72), containLabel: true },
    graphic: opts.footer === false ? [] : [{
      type: "text", right: c.u(40), bottom: c.u(26),
      style: { text: "Metorite", fill: c.t.faint, fontWeight: 500, fontSize: c.u(22), fontFamily: c.t.font },
    }],
  };
}

const valueAxis = (c, extra = {}) => ({
  type: "value", splitNumber: 4,
  axisLine: { show: false }, axisTick: { show: false },
  splitLine: { lineStyle: { color: c.t.grid, width: c.u(2) } },
  axisLabel: { ...c.axisText, margin: c.u(20), formatter: fmt },
  ...extra,
});

const categoryAxis = (c, data, extra = {}) => ({
  type: "category", data,
  axisLine: { show: false }, axisTick: { show: false },
  axisLabel: { ...c.axisText, margin: c.u(22), hideOverlap: true },
  ...extra,
});

const legend = (c, extra = {}) => ({
  bottom: c.u(28), left: c.u(56), icon: "roundRect", itemWidth: c.u(22), itemHeight: c.u(22), itemGap: c.u(32),
  textStyle: { color: c.t.muted, fontFamily: c.t.font, fontSize: c.u(24) },
  ...extra,
});

const valueLabel = (c, formatter, extra = {}) => ({
  show: true, color: c.t.fg, fontSize: c.u(26), fontWeight: 600, fontFamily: c.t.font, formatter, ...extra,
});

function tones(c, spec, n) {
  if (spec.tones === undefined) return null;
  return list(spec.tones, "tones", { max: n }).map((w) => tone(c.t, w));
}

// ── The kinds ───────────────────────────────────────────────────────────────

const BUILD = {
  bar(spec, opts, c) {
    const ls = labels(spec);
    const ss = series(spec, ls.length);
    const tn = ss.length === 1 ? tones(c, spec, ls.length) : null;
    const long = ls.some((l) => l.length > 10) || ls.length > 8;
    const o = base(spec, opts, c);
    const cat = categoryAxis(c, ls, long ? { inverse: true, axisLabel: { ...c.axisText, color: c.t.fg, margin: c.u(20) } } : {});
    const val = valueAxis(c, long ? { splitLine: { show: false }, axisLabel: { show: false } } : {});
    Object.assign(o, long ? { yAxis: cat, xAxis: val } : { xAxis: cat, yAxis: val });
    const stacked = Boolean(spec.stacked) && ss.length > 1;
    o.series = ss.map((s, i) => ({
      type: "bar", name: s.name, stack: stacked ? "all" : undefined,
      data: s.values.map((v, j) => ({ value: v, itemStyle: { color: tn?.[j] ?? c.t.series[i] } })),
      barMaxWidth: c.u(ss.length > 1 && !stacked ? 44 : 96), barGap: "20%",
      itemStyle: { borderRadius: long ? [0, c.u(10), c.u(10), 0] : [c.u(12), c.u(12), c.u(4), c.u(4)] },
      label: ss.length === 1 || (stacked && i === ss.length - 1)
        ? valueLabel(c, ({ value }) => c.label(value), { position: long ? "right" : "top", distance: c.u(10) })
        : undefined,
    }));
    if (ss.length > 1) { o.legend = legend(c); o.grid.bottom = c.u(110); }
    if (long) o.grid.right = c.u(110);
    return { o, h: long ? Math.max(560, 220 + ls.length * 64 + (ss.length > 1 ? 50 : 0)) : 760 };
  },

  line(spec, opts, c) {
    const ls = labels(spec);
    const ss = series(spec, ls.length);
    const o = base(spec, opts, c);
    o.xAxis = categoryAxis(c, ls, { boundaryGap: false });
    o.yAxis = valueAxis(c, { scale: Boolean(spec.from_min) });
    const one = ss.length === 1;
    o.series = ss.map((s, i) => ({
      type: "line", name: s.name, data: s.values, smooth: 0.35, symbol: "none",
      lineStyle: { width: c.u(5), cap: "round" },
      areaStyle: one ? { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [
        { offset: 0, color: alpha(c.t.series[i], 0.35) }, { offset: 1, color: alpha(c.t.series[i], 0) }] } } : undefined,
      endLabel: valueLabel(c, ({ value }) => c.label(value), { distance: c.u(12) }),
    }));
    o.grid.right = c.u(130);
    if (!one) { o.legend = legend(c); o.grid.bottom = c.u(110); }
    return { o, h: 760 };
  },

  area(spec, opts, c) {
    const ls = labels(spec);
    const ss = series(spec, ls.length, { min: 2 });
    const tn = spec.tones !== undefined ? list(spec.tones, "tones", { max: ss.length }).map((w) => tone(c.t, w)) : [];
    const o = base(spec, opts, c);
    o.xAxis = categoryAxis(c, ls, { boundaryGap: false });
    o.yAxis = valueAxis(c);
    o.series = ss.map((s, i) => ({
      type: "line", name: s.name, data: s.values, smooth: 0.4, symbol: "none", stack: "all",
      lineStyle: { width: 0 }, areaStyle: { color: alpha(tn[i] ?? c.t.series[i], 0.85) },
      itemStyle: { color: tn[i] ?? c.t.series[i] },
    }));
    o.legend = legend(c); o.grid.bottom = c.u(110);
    return { o, h: 760 };
  },

  donut(spec, opts, c) {
    const ls = labels(spec);
    const vs = nums(spec.values, "values", ls.length);
    if (ls.length > 8) fail('a donut takes at most 8 slices. Group the rest as "Other"');
    if (vs.some((v) => v < 0)) fail("a donut takes no negative value");
    const total = vs.reduce((a, v) => a + v, 0);
    if (total <= 0) fail("a donut needs a total above zero");
    const tn = tones(c, spec, ls.length);
    const o = base(spec, opts, c);
    const W = 1080 * c.k, H = 760 * c.k;
    o.series = [{
      type: "pie", radius: ["50%", "74%"], center: ["31%", "60%"], padAngle: 2.5,
      itemStyle: { borderRadius: c.u(10) }, label: { show: false },
      data: ls.map((name, i) => ({ name, value: vs[i], itemStyle: tn?.[i] ? { color: tn[i] } : undefined })),
    }];
    const center = text(spec.center ?? "total", "center", 16, false);
    o.graphic.push(
      { type: "text", x: W * 0.31, y: H * 0.6 - c.u(46),
        style: { text: fmt(total), align: "center", fill: c.t.fg, fontWeight: 600, fontSize: c.u(64), fontFamily: c.t.font } },
      { type: "text", x: W * 0.31, y: H * 0.6 + c.u(26),
        style: { text: center, align: "center", fill: c.t.muted, fontSize: c.u(26), fontFamily: c.t.font } },
    );
    o.legend = {
      orient: "vertical", right: c.u(64), top: "middle", icon: "circle", itemWidth: c.u(20), itemHeight: c.u(20), itemGap: c.u(30),
      textStyle: { color: c.t.fg, fontFamily: c.t.font, fontSize: c.u(27), rich: {
        n: { color: c.t.fg, fontSize: c.u(27), width: c.u(230), fontFamily: c.t.font },
        p: { color: c.t.muted, fontSize: c.u(27), fontFamily: c.t.font, align: "right", width: c.u(90) } } },
      formatter: (name) => {
        const v = vs[ls.indexOf(name)];
        return `{n|${name}}{p|${Math.round((v / total) * 100)}%}`;
      },
    };
    return { o, h: 760 };
  },

  progress(spec, opts, c) {
    const ls = labels(spec);
    const done = nums(spec.values, "values", ls.length);
    const totals = spec.totals === undefined ? ls.map(() => 100) : nums(spec.totals, "totals", ls.length);
    if (totals.some((t) => t <= 0)) fail('"totals" must be above zero');
    const o = base(spec, opts, c);
    const pct = done.map((d, i) => Math.max(0, Math.min(100, (d / totals[i]) * 100)));
    const caption = (i) => (spec.totals === undefined ? `${Math.round(pct[i])}%` : `${fmt(done[i])} of ${fmt(totals[i])}`);
    if (ls.length <= 4) {
      // Activity rings: the first row is the outer ring.
      o.angleAxis = { max: 100, show: false, startAngle: 90 };
      o.radiusAxis = { type: "category", data: ls, show: false, inverse: true };
      o.polar = { center: ["30%", "58%"], radius: ["26%", "82%"] };
      const bw = c.u(ls.length <= 3 ? 30 : 24);
      o.series = [
        { type: "bar", coordinateSystem: "polar", barWidth: bw, roundCap: true, silent: true,
          data: ls.map((_l, i) => ({ value: 100, itemStyle: { color: alpha(c.t.series[i], 0.18) } })) },
        { type: "bar", coordinateSystem: "polar", barWidth: bw, roundCap: true, barGap: "-100%",
          data: ls.map((_l, i) => ({ value: pct[i], itemStyle: { color: c.t.series[i] } })) },
      ];
      const H = 720, step = 380 / ls.length;
      o.graphic.push(...ls.map((l, i) => ({
        type: "group", left: "60%", top: c.u(220 + i * step + 20),
        children: [
          { type: "circle", shape: { cx: 0, cy: c.u(22), r: c.u(12) }, style: { fill: c.t.series[i] } },
          { type: "text", left: c.u(32), top: 0, style: { text: l, fill: c.t.muted, fontSize: c.u(26), fontFamily: c.t.font } },
          { type: "text", left: c.u(32), top: c.u(36), style: { text: caption(i), fill: c.t.fg, fontWeight: 600, fontSize: c.u(36), fontFamily: c.t.font } },
        ],
      })));
      return { o, h: H };
    }
    // More rows: one track per row, the value at its end.
    o.yAxis = categoryAxis(c, ls, { inverse: true, axisLabel: { ...c.axisText, color: c.t.fg } });
    o.xAxis = { type: "value", max: 100, show: false };
    o.grid.right = c.u(170);
    o.series = [
      { type: "bar", data: ls.map(() => 100), barWidth: c.u(26), silent: true, itemStyle: { color: c.t.grid, borderRadius: c.u(13) } },
      { type: "bar", data: pct.map((p, i) => ({ value: p, itemStyle: { color: c.t.series[0] } })), barWidth: c.u(26), barGap: "-100%",
        itemStyle: { borderRadius: c.u(13) } },
    ];
    o.graphic.push(...ls.map((_l, i) => ({ type: "text", right: c.u(56), top: c.u(c.top + 22 + i * 64),
      style: { text: caption(i), fill: c.t.fg, fontWeight: 600, fontSize: c.u(25), fontFamily: c.t.font, align: "right" } })));
    return { o, h: Math.max(480, c.top + 90 + ls.length * 64) };
  },

  scatter(spec, opts, c) {
    const groups = list(spec.groups, "groups", { max: LIMITS.series }).map((g, i) => {
      if (!g || typeof g !== "object") fail(`"groups[${i}]" must be {name, points}`);
      const pts = list(g.points, `groups[${i}].points`, { max: LIMITS.points }).map((p, j) => {
        const q = list(p, `groups[${i}].points[${j}]`, { min: 2, max: 3 });
        return q.map((v, k) => num(v, `groups[${i}].points[${j}][${k}]`));
      });
      return { name: text(g.name ?? `Group ${i + 1}`, `groups[${i}].name`, 24), points: pts };
    });
    const sizes = groups.flatMap((g) => g.points.map((p) => p[2] ?? 0));
    const maxSize = Math.max(1, ...sizes);
    const o = base(spec, opts, c);
    const name = (v, what) => text(v ?? "", what, 30, false);
    o.xAxis = valueAxis(c, { scale: true, name: name(spec.x_name, "x_name"), nameLocation: "middle", nameGap: c.u(56),
      nameTextStyle: { ...c.axisText }, splitLine: { show: false } });
    o.yAxis = valueAxis(c, { scale: true, name: name(spec.y_name, "y_name"), nameTextStyle: { ...c.axisText, align: "left" } });
    o.grid.bottom = c.u(110); o.grid.top = c.u(c.top + 60);
    o.series = groups.map((g) => ({
      type: "scatter", name: g.name, data: g.points,
      symbolSize: (p) => c.u(12 + ((p[2] ?? 0) / maxSize) * 20),
      itemStyle: { opacity: 0.85, borderColor: c.t.card, borderWidth: c.u(2) },
    }));
    if (groups.length > 1) o.legend = legend(c, { top: c.u(c.top - 40), bottom: undefined, right: c.u(56), left: undefined });
    return { o, h: 760 };
  },

  heatmap(spec, opts, c) {
    const xs = list(spec.x, "x", { max: 31 }).map((l, i) => text(l, `x[${i}]`, 10));
    const ys = list(spec.y, "y", { max: 14 }).map((l, i) => text(l, `y[${i}]`, 14));
    const rows = list(spec.values, "values", { min: ys.length, max: ys.length });
    const data = [];
    rows.forEach((r, y) => nums(r, `values[${y}]`, xs.length).forEach((v, x) => data.push([x, y, v])));
    const max = Math.max(1, ...data.map((d) => d[2]));
    const o = base(spec, opts, c);
    o.xAxis = categoryAxis(c, xs, { axisLabel: { ...c.axisText, fontSize: c.u(22), interval: xs.length > 12 ? 2 : 0 } });
    o.yAxis = categoryAxis(c, ys, { inverse: true });
    o.visualMap = { show: false, min: 0, max, inRange: { color: [c.t.grid, alpha(c.t.series[0], 0.55), c.t.series[0]] } };
    o.series = [{ type: "heatmap", data, itemStyle: { borderColor: c.t.card, borderWidth: c.u(5), borderRadius: c.u(8) } }];
    return { o, h: Math.max(520, c.top + 120 + ys.length * 64) };
  },

  radar(spec, opts, c) {
    const axes = list(spec.axes, "axes", { min: 3, max: 10 }).map((a, i) => text(a, `axes[${i}]`, 16));
    const ss = list(spec.series, "series", { max: 4 }).map((s, i) => ({
      name: text(s?.name ?? `Series ${i + 1}`, `series[${i}].name`, 24), values: nums(s?.values, `series[${i}].values`, axes.length) }));
    const max = spec.max !== undefined ? num(spec.max, "max") : Math.max(...ss.flatMap((s) => s.values));
    const o = base(spec, opts, c);
    o.radar = {
      center: ["50%", "58%"], radius: "58%", splitNumber: 4, shape: "polygon",
      indicator: axes.map((name) => ({ name, max })),
      axisName: { color: c.t.muted, fontSize: c.u(25), fontFamily: c.t.font },
      splitLine: { lineStyle: { color: c.t.grid, width: c.u(2) } }, splitArea: { show: false },
      axisLine: { lineStyle: { color: c.t.grid, width: c.u(2) } },
    };
    o.series = [{ type: "radar", symbol: "none", data: ss.map((s, i) => ({
      name: s.name, value: s.values, lineStyle: { width: c.u(5), color: c.t.series[i] }, itemStyle: { color: c.t.series[i] },
      areaStyle: { color: alpha(c.t.series[i], 0.22) } })) }];
    if (ss.length > 1) o.legend = legend(c);
    return { o, h: 860 };
  },

  box(spec, opts, c) {
    const groups = list(spec.groups, "groups", { max: 8 }).map((g, i) => ({
      name: text(g?.name ?? `Group ${i + 1}`, `groups[${i}].name`, 16),
      values: list(g?.values, `groups[${i}].values`, { min: 3, max: LIMITS.points }).map((v, j) => num(v, `groups[${i}].values[${j}]`)),
    }));
    const o = base(spec, opts, c);
    o.xAxis = categoryAxis(c, groups.map((g) => g.name));
    o.yAxis = valueAxis(c, { scale: true });
    o.series = [{ type: "boxplot", boxWidth: [c.u(40), c.u(90)],
      data: groups.map((g, i) => ({ value: quartiles(g.values), itemStyle: {
        color: alpha(c.t.series[i], 0.3), borderColor: c.t.series[i], borderWidth: c.u(4) } })) }];
    return { o, h: 760 };
  },

  waterfall(spec, opts, c) {
    const steps = list(spec.steps, "steps", { min: 2, max: 16 }).map((s, i) => ({
      label: text(s?.label, `steps[${i}].label`, 16), total: Boolean(s?.total),
      value: s?.total ? 0 : num(s?.value, `steps[${i}].value`) }));
    let run = 0;
    const helper = [], up = [], down = [], total = [];
    steps.forEach((st, i) => {
      if (st.total) { helper.push(0); total.push(run); up.push("-"); down.push("-"); return; }
      if (i === 0) { run = st.value; helper.push(0); total.push(st.value); up.push("-"); down.push("-"); return; }
      if (st.value >= 0) { helper.push(run); up.push(st.value); down.push("-"); run += st.value; }
      else { run += st.value; helper.push(run); down.push(-st.value); up.push("-"); }
      total.push("-");
    });
    const o = base(spec, opts, c);
    o.xAxis = categoryAxis(c, steps.map((s) => s.label));
    o.yAxis = valueAxis(c);
    const lab = (pre) => valueLabel(c, ({ value }) => (value === "-" ? "" : `${pre}${c.label(value)}`), { position: "top", fontSize: c.u(24) });
    const bar = { type: "bar", stack: "w", barMaxWidth: c.u(96) };
    o.series = [
      { ...bar, data: helper, itemStyle: { color: "transparent" }, silent: true },
      { ...bar, data: total, itemStyle: { color: c.t.status.blue, borderRadius: c.u(8) }, label: lab("") },
      { ...bar, data: up, itemStyle: { color: c.t.status.green, borderRadius: c.u(8) }, label: lab("+") },
      { ...bar, data: down, itemStyle: { color: c.t.status.red, borderRadius: c.u(8) }, label: lab("−") },
    ];
    return { o, h: 760 };
  },

  funnel(spec, opts, c) {
    const ls = labels(spec);
    const vs = nums(spec.values, "values", ls.length);
    if (ls.length > 8) fail("a funnel takes at most 8 stages");
    const o = base(spec, opts, c);
    const first = vs[0] || 1;
    o.series = [{
      type: "funnel", sort: "none", left: c.u(56), right: c.u(320), top: c.u(c.top), bottom: c.u(60),
      minSize: "18%", gap: c.u(6), itemStyle: { borderWidth: 0, borderRadius: c.u(8) },
      label: { show: true, position: "right", color: c.t.fg, fontFamily: c.t.font, fontSize: c.u(26),
        formatter: ({ name, value }) => `{n|${name}}\n{v|${c.label(value)} · ${Math.round((value / first) * 100)}%}`,
        rich: { n: { color: c.t.muted, fontSize: c.u(24), fontFamily: c.t.font, lineHeight: c.u(34) },
                v: { color: c.t.fg, fontSize: c.u(28), fontWeight: 600, fontFamily: c.t.font } } },
      labelLine: { show: false },
      data: ls.map((name, i) => ({ name, value: vs[i], itemStyle: { color: c.t.series[i % c.t.series.length] } })),
    }];
    return { o, h: Math.max(620, c.top + 110 + ls.length * 90) };
  },

  calendar(spec, opts, c) {
    const days = list(spec.days, "days", { max: LIMITS.days }).map((d, i) => {
      const q = list(d, `days[${i}]`, { min: 2, max: 2 });
      date(q[0], `days[${i}][0]`);
      return [q[0], num(q[1], `days[${i}][1]`)];
    });
    const sorted = days.map((d) => d[0]).sort();
    const max = Math.max(1, ...days.map((d) => d[1]));
    const o = base(spec, opts, c);
    o.calendar = { range: [sorted[0], sorted[sorted.length - 1]], top: c.u(c.top + 10), left: c.u(110), right: c.u(56),
      cellSize: ["auto", c.u(44)],
      itemStyle: { color: c.t.grid, borderColor: c.t.card, borderWidth: c.u(6), borderRadius: c.u(6) },
      splitLine: { show: false }, yearLabel: { show: false },
      dayLabel: { color: c.t.muted, fontSize: c.u(20), fontFamily: c.t.font, firstDay: 1, nameMap: ["", "Mon", "", "Wed", "", "Fri", ""] },
      monthLabel: { color: c.t.muted, fontSize: c.u(22), fontFamily: c.t.font } };
    o.visualMap = { show: false, min: 0, max, inRange: { color: [alpha(c.t.status.green, 0.22), c.t.status.green] } };
    o.series = [{ type: "heatmap", coordinateSystem: "calendar", data: days.filter((d) => d[1] > 0),
      itemStyle: { borderColor: c.t.card, borderWidth: c.u(6), borderRadius: c.u(6) } }];
    return { o, h: c.top + 450 };
  },

  gantt(spec, opts, c) {
    const rows = list(spec.rows, "rows", { max: LIMITS.rows }).map((r, i) => {
      const start = date(r?.start, `rows[${i}].start`);
      const end = r?.end !== undefined ? date(r.end, `rows[${i}].end`) : start;
      if (end < start) fail(`"rows[${i}].end" must be on or after its start`);
      const progress = r?.progress === undefined ? 0 : Math.max(0, Math.min(100, num(r.progress, `rows[${i}].progress`)));
      return { label: text(r?.label, `rows[${i}].label`, 22), start, end: end + DAY, progress,
        colour: tone(c.t, r?.status) ?? c.t.series[0] };
    });
    const today = spec.today !== undefined ? date(spec.today, "today") : null;
    const min = Math.min(...rows.map((r) => r.start)) - 2 * DAY;
    const max = Math.max(...rows.map((r) => r.end)) + 2 * DAY;
    const span = (max - min) / DAY;
    const tick = span > 120 ? 30 : span > 49 ? 14 : 7;
    const o = base(spec, opts, c);
    o.grid.top = c.u(c.top + 30); o.grid.bottom = c.u(70);
    const M = "JanFebMarAprMayJunJulAugSepOctNovDec";
    o.xAxis = { type: "value", min, max, position: "top", interval: tick * DAY,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { ...c.axisText, fontSize: c.u(22), margin: c.u(16), hideOverlap: true,
        formatter: (v) => { const d = new Date(v); return `${d.getUTCDate()} ${M.slice(d.getUTCMonth() * 3, d.getUTCMonth() * 3 + 3)}`; } },
      splitLine: { show: true, lineStyle: { color: c.t.grid, width: c.u(2) } } };
    o.yAxis = categoryAxis(c, rows.map((r) => r.label), { inverse: true, axisLabel: { ...c.axisText, color: c.t.fg, fontSize: c.u(25), margin: c.u(24) } });
    o.series = [{
      type: "custom",
      data: rows.map((r, i) => [i, r.start, r.end, r.progress]),
      renderItem: (_p, api) => {
        const r = rows[api.value(0)];
        const a = api.coord([api.value(1), api.value(0)]), z = api.coord([api.value(2), api.value(0)]);
        const h = c.u(34), w = Math.max(z[0] - a[0], c.u(12)), x = a[0], top = a[1] - h / 2;
        return { type: "group", children: [
          { type: "rect", shape: { x, y: top, width: w, height: h, r: c.u(10) }, style: { fill: alpha(r.colour, 0.28) } },
          { type: "rect", shape: { x, y: top, width: (w * api.value(3)) / 100, height: h, r: c.u(10) }, style: { fill: r.colour } },
        ] };
      },
    }];
    if (today !== null) {
      o.series.push({ type: "line", data: [], markLine: { symbol: "none", silent: true,
        label: { show: true, formatter: "Today", color: c.t.status.red, fontSize: c.u(22), fontWeight: 600, fontFamily: c.t.font, position: "end", distance: c.u(10) },
        lineStyle: { color: c.t.status.red, width: c.u(3), type: "solid" }, data: [{ xAxis: today }] } });
    }
    return { o, h: c.top + 130 + rows.length * 74 };
  },
};

function quartiles(xs) {
  const v = [...xs].sort((a, b) => a - b);
  const q = (p) => { const i = (v.length - 1) * p, lo = Math.floor(i); return v[lo] + (v[Math.ceil(i)] - v[lo]) * (i - lo); };
  return [v[0], q(0.25), q(0.5), q(0.75), v[v.length - 1]];
}

/**
 * A spec as a full ECharts option, and the drawing's size.
 * @param {any} spec  the AI's short spec, with a `type`
 * @param {Opts & { width?: number, animate?: boolean }} [opts]
 * @returns {{ option: any, width: number, height: number }}
 */
export function chartOption(spec, opts = {}) {
  if (!spec || typeof spec !== "object" || Array.isArray(spec)) fail("a chart is an object with a \"type\"");
  const kind = typeof spec.type === "string" ? spec.type.trim().toLowerCase() : "";
  const build = /** @type {Record<string, Function>} */ (BUILD)[kind];
  if (!build) fail(`"type" must be one of: ${Object.keys(KINDS).join(", ")}`);
  const width = opts.width ?? 1080;
  const scale = opts.scale ?? width / 1080;
  const c = ctx(spec, { ...opts, scale });
  const { o, h } = build(spec, { ...opts, scale }, c);
  return { option: o, width, height: Math.round(h * scale) };
}
