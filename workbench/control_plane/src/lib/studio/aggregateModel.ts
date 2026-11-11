/**
 * The pure half of the Chart and Stat blocks (`ai_studio.md` §5.1, slice STU-D4).
 *
 * The gateway aggregate of C2b (`objects_store.aggregate_records`) groups the records of one
 * object by one hot field and takes a count, a sum or an average of each group. This file
 * reads that result and turns it into the short spec of `lib/charts/kinds.mjs` (13 kinds)
 * or into the tile of a Stat. It draws nothing and adds no chart code: `kinds.mjs` stays the
 * one validator, and `LiveChart` stays the one renderer.
 *
 * The door answers an aggregate as ONE row, the result. `parseAggregate` reads it with
 * care, because the wire may carry a Decimal as text. A result it cannot read is `null`.
 * No React import: a server route may reach `lib/studio`. Fence: `aggregateModel.test.ts`.
 */
import { minorDigits } from "./fieldCheck";
import type { Row } from "./dataDoor";

/** The most groups a chart draws. `kinds.mjs` takes 31 labels, and the server `other` is one. */
export const MAX_GROUPS = 30;

/** The 13 kinds of `lib/charts/kinds.mjs`. `StudioChart.test.ts` holds this list to `KINDS`. */
export const CHART_KINDS = [
  "bar", "line", "area", "donut", "progress", "scatter", "heatmap", "radar", "box",
  "waterfall", "funnel", "calendar", "gantt",
] as const;
export type ChartKind = (typeof CHART_KINDS)[number];

export interface AggMeasure {
  op: "count" | "sum" | "average";
  /** The key of the field, for a sum or an average. */
  field: string | null;
  label: string;
}

export interface AggGroup {
  /** The key as the wire sent it. `null` is the records with no value. */
  key: unknown;
  count: number;
  /** One number for each measure. `null` is an average with no value. */
  values: (number | null)[];
}

export interface AggData {
  measures: AggMeasure[];
  /** For each measure: the currency of a money measure, or `null`. */
  currencies: (string | null)[];
  groups: AggGroup[];
  other: AggGroup | null;
  folded: number;
  total: AggGroup;
}

const isObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

function numeric(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "string" ? Number(v) : v;
  return typeof n === "number" && Number.isFinite(n) ? n : null;
}

function parseGroup(raw: unknown, n: number): AggGroup | null {
  if (!isObject(raw) || !Array.isArray(raw.values)) return null;
  const count = numeric(raw.count);
  if (count === null) return null;
  const values = Array.from({ length: n }, (_, j) => numeric((raw.values as unknown[])[j]));
  return { key: raw.key ?? null, count, values };
}

function labelOfMeasure(op: AggMeasure["op"], field: string | null, given: unknown): string {
  if (typeof given === "string" && given.trim()) return given.trim();
  if (op === "count" || !field) return "Records";
  const words = field.replace(/_/g, " ");
  return `${op === "sum" ? "Total" : "Average"} ${words}`;
}

/**
 * Read the result of an aggregate from the rows of a door read. The door gives one row.
 * Returns `null` when the row is no aggregate, so a caller can fall back to a plain read.
 */
export function parseAggregate(rows: readonly Row[] | null | undefined): AggData | null {
  const raw = rows?.[0];
  if (!isObject(raw) || !Array.isArray(raw.measures) || !Array.isArray(raw.groups) || !isObject(raw.total)) return null;
  const measures: AggMeasure[] = [];
  for (const m of raw.measures) {
    if (!isObject(m) || (m.op !== "count" && m.op !== "sum" && m.op !== "average")) return null;
    const f = m.field;
    const field = typeof f === "string" ? f : isObject(f) && typeof f.key === "string" ? f.key : null;
    measures.push({ op: m.op, field, label: labelOfMeasure(m.op, field, m.label ?? (isObject(f) ? f.label : null)) });
  }
  if (measures.length === 0) return null;
  const n = measures.length;
  const total = parseGroup(raw.total, n);
  if (!total) return null;
  const groups: AggGroup[] = [];
  for (const g of raw.groups) {
    const p = parseGroup(g, n);
    if (p) groups.push(p);
  }
  const currencies = measures.map((_, j) => {
    const c = Array.isArray(raw.currencies) ? raw.currencies[j] : null;
    return typeof c === "string" && /^[A-Z]{3}$/.test(c) ? c : null;
  });
  return {
    measures, currencies, groups, total,
    other: raw.other ? parseGroup(raw.other, n) : null,
    folded: numeric(raw.folded) ?? 0,
  };
}

// --- the words ---------------------------------------------------------------

const DAY = /^\d{4}-\d{2}-\d{2}$/;
const MONTH = /^\d{4}-\d{2}$/;
const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

/** The key of a group as words: a month as "Oct 2026", a bool as Yes or No. */
export function keyLabel(key: unknown): string {
  if (key === null || key === undefined || key === "") return "(none)";
  if (typeof key === "boolean") return key ? "Yes" : "No";
  const s = String(key);
  if (MONTH.test(s)) {
    const m = Number(s.slice(5, 7));
    if (m >= 1 && m <= 12) return `${MONTHS[m - 1]} ${s.slice(0, 4)}`;
  }
  return s;
}

/** The value of measure `j` in the unit a reader sees: a money minor unit becomes a major unit. */
function scaled(agg: AggData, j: number, v: number | null): number | null {
  if (v === null) return null;
  const cur = agg.currencies[j];
  return cur ? v / 10 ** minorDigits(cur) : v;
}

const LOCALE = "en-IN";

/** One value of measure `j` as words: a count whole, an amount in its currency. */
export function formatMeasure(agg: AggData, j: number, v: number | null): string {
  const x = scaled(agg, j, v);
  if (x === null) return "—";
  const cur = agg.currencies[j];
  if (cur) {
    try {
      return new Intl.NumberFormat(LOCALE, { style: "currency", currency: cur }).format(x);
    } catch {
      return `${cur} ${x.toFixed(2)}`;
    }
  }
  return new Intl.NumberFormat(LOCALE, { maximumFractionDigits: agg.measures[j].op === "count" ? 0 : 2 }).format(x);
}

// --- the Chart ---------------------------------------------------------------

export type ChartResult =
  | { ok: true; spec: Record<string, unknown>; note: string | null }
  | { ok: false; reason: string };

const fail = (reason: string): ChartResult => ({ ok: false, reason });

export interface ChartOpts {
  title?: string;
  subtitle?: string;
}

/** The point cap per kind, below `MAX_GROUPS`. */
const CAP: Partial<Record<ChartKind, number>> = {
  radar: 10, box: 8, waterfall: 15, gantt: 15, heatmap: 30, funnel: 30,
};

/**
 * The groups to draw: at most `cap`, the ones with the largest first measure, in their own
 * order. The server `other` group follows when it exists. `note` says what is left out.
 */
function pick(agg: AggData, cap: number, withOther: boolean): { groups: AggGroup[]; note: string | null } {
  let groups = agg.groups;
  let left = 0;
  const room = withOther && agg.other ? cap - 1 : cap;
  if (groups.length > room) {
    const rank = [...groups.keys()].sort((a, b) => Math.abs(groups[b].values[0] ?? 0) - Math.abs(groups[a].values[0] ?? 0));
    const keep = new Set(rank.slice(0, room));
    left = groups.length - room;
    groups = groups.filter((_, i) => keep.has(i));
  }
  const out = withOther && agg.other ? [...groups, agg.other] : groups;
  const folded = agg.folded + left;
  const note = folded > 0 ? `Showing the ${groups.length} largest groups. ${folded} more are not drawn.` : null;
  return { groups: out, note };
}

const nameOf = (g: AggGroup, isOther: boolean): string => (isOther ? "Other" : keyLabel(g.key));

const val = (agg: AggData, g: AggGroup, j: number): number => scaled(agg, j, g.values[j]) ?? 0;

/** Round away float noise from a scale by a minor unit. */
const tidy = (n: number): number => Math.round(n * 1e6) / 1e6;

const unitOf = (agg: AggData, j: number): string | undefined => agg.currencies[j] ?? undefined;

/**
 * Build the spec of one chart kind from an aggregate. A result that cannot draw, as a
 * calendar of a group that is no date, answers `ok: false` with the words to fix it.
 */
export function chartFromAggregate(kind: string, agg: AggData | null, opts: ChartOpts = {}): ChartResult {
  if (!(CHART_KINDS as readonly string[]).includes(kind)) return fail(`A chart kind is one of: ${CHART_KINDS.join(", ")}.`);
  if (!agg || (agg.groups.length === 0 && !agg.other)) return fail("No data");
  const k = kind as ChartKind;
  const { groups, note } = pick(agg, Math.min(MAX_GROUPS, CAP[k] ?? MAX_GROUPS), k !== "calendar" && k !== "gantt");
  const isOther = (g: AggGroup) => g === agg.other;
  const labels = groups.map((g) => nameOf(g, isOther(g)));
  const col = (j: number) => groups.map((g) => tidy(val(agg, g, j)));
  const many = agg.measures.length;
  const head: Record<string, unknown> = { type: k, title: opts.title, subtitle: opts.subtitle };
  const done = (spec: Record<string, unknown>): ChartResult => ({ ok: true, spec: { ...head, ...spec }, note });
  const series = (max: number) => agg.measures.slice(0, max).map((m, j) => ({ name: m.label, values: col(j) }));
  const unit = many === 1 ? unitOf(agg, 0) : undefined;

  switch (k) {
    case "bar":
    case "line":
      return done(many > 1 ? { labels, series: series(6) } : { labels, values: col(0), name: agg.measures[0].label, unit });
    case "area":
      return done({ labels, series: series(6), unit });
    case "donut":
    case "funnel":
      return done({ labels, values: col(0), unit });
    case "progress": {
      const done0 = col(0);
      const totals = many > 1 ? col(1) : done0.map(() => Math.max(1, ...done0.map(Math.abs)));
      return done({ labels, values: done0, totals: totals.map((t) => (t > 0 ? t : 1)), unit });
    }
    case "radar":
      if (groups.length < 3) return fail("A radar chart needs 3 or more groups.");
      return done({ axes: labels, series: series(4) });
    case "box": {
      if (groups.length < 3) return fail("A box chart needs 3 or more groups.");
      return done({
        groups: agg.measures.slice(0, 8).map((m, j) => ({ name: m.label, values: col(j) })),
      });
    }
    case "scatter": {
      const xj = many > 1 ? 0 : -1;
      const yj = many > 1 ? 1 : 0;
      const points = groups.map((g) => [
        xj === -1 ? g.count : tidy(val(agg, g, xj)), tidy(val(agg, g, yj)), g.count,
      ]);
      return done({
        groups: [{ name: agg.measures[yj].label, points }],
        x_name: xj === -1 ? "Records" : agg.measures[xj].label,
        y_name: agg.measures[yj].label,
      });
    }
    case "heatmap":
      return done({
        x: labels, y: agg.measures.slice(0, 14).map((m) => m.label),
        values: agg.measures.slice(0, 14).map((_, j) => col(j)),
      });
    case "waterfall": {
      if (groups.length < 1) return fail("No data");
      const first = col(0);
      return done({ steps: [...labels.map((label, i) => ({ label, value: first[i] })), { label: "Total", total: true }] });
    }
    case "calendar": {
      const days = groups.filter((g) => DAY.test(String(g.key)));
      if (days.length === 0) return fail("A calendar chart needs a date field grouped by day.");
      return done({ days: days.map((g) => [String(g.key), Math.max(0, tidy(val(agg, g, 0)))]) });
    }
    case "gantt": {
      const dated = groups.filter((g) => DAY.test(String(g.key)) || MONTH.test(String(g.key)));
      if (dated.length === 0) return fail("A gantt chart needs a date field grouped by day or by month.");
      const top = Math.max(1e-9, ...dated.map((g) => Math.abs(val(agg, g, 0))));
      return done({
        rows: dated.map((g) => {
          const key = String(g.key);
          const start = DAY.test(key) ? key : `${key}-01`;
          const end = DAY.test(key) ? key : lastDay(key);
          return { label: keyLabel(key), start, end, progress: Math.round((Math.abs(val(agg, g, 0)) / top) * 100) };
        }),
      });
    }
  }
}

/** The last day of "YYYY-MM", as "YYYY-MM-DD", in UTC. */
function lastDay(month: string): string {
  const y = Number(month.slice(0, 4));
  const m = Number(month.slice(5, 7));
  return new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10);
}

// --- the Stat ----------------------------------------------------------------

export interface StatTile {
  label: string;
  /** The value, in words. */
  value: string;
  /** The change from the one period before, or null with no date group. */
  trend: { delta: number; percent: number | null; text: string; against: string } | null;
  /** The value of each period, for the small line. Empty with no trend. */
  spark: number[];
}

/** The groups that are periods (a day or a month), oldest first. */
function periods(agg: AggData): AggGroup[] {
  return agg.groups.filter((g) => typeof g.key === "string" && (DAY.test(g.key) || MONTH.test(g.key)))
    .sort((a, b) => String(a.key).localeCompare(String(b.key)));
}

/**
 * The tile of measure `j`: its value over every record, and its trend. The trend is the last
 * period against the one before it, and it exists only when the aggregate groups by day or month.
 */
export function statFromAggregate(agg: AggData | null, j = 0): StatTile | null {
  if (!agg || j < 0 || j >= agg.measures.length) return null;
  const label = agg.measures[j].label;
  const value = formatMeasure(agg, j, agg.total.values[j]);
  const ps = periods(agg);
  if (ps.length < 2) return { label, value, trend: null, spark: [] };
  const spark = ps.slice(-12).map((g) => scaled(agg, j, g.values[j]) ?? 0);
  const last = spark[spark.length - 1];
  const prev = spark[spark.length - 2];
  const delta = tidy(last - prev);
  const percent = prev !== 0 ? Math.round((delta / Math.abs(prev)) * 1000) / 10 : null;
  const against = keyLabel(ps[ps.length - 2].key);
  const sign = delta > 0 ? "+" : delta < 0 ? "−" : "";
  const text = percent !== null ? `${sign}${Math.abs(percent)}%` : `${sign}${Math.abs(delta)}`;
  return { label, value, trend: { delta, percent, text, against }, spark };
}

// --- the nodes the manifest mapper builds ----------------------------------

export interface AggregateSource {
  action: string;
  args?: Record<string, unknown>;
}

/** The node of a Chart block: the `chart` template, bound to an aggregate source. */
export function chartBlock(
  kind: ChartKind, source: AggregateSource, title?: string,
): { type: "template"; props: Record<string, unknown> } {
  return { type: "template", props: { name: "chart", source, data: { type: kind, title } } };
}

/** The node of a Stat block: the `sparkTrend` template, bound to an aggregate source. */
export function statBlock(
  source: AggregateSource, title?: string, measure = 0, good: "up" | "down" = "up",
): { type: "template"; props: Record<string, unknown> } {
  return { type: "template", props: { name: "sparkTrend", source, data: { label: title, measure, good } } };
}
