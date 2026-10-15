"use client";

/**
 * genUITemplates — Tier 2 of generative UI: a registry of pre-DESIGNED, polished,
 * animated React components an agent renders by NAME while supplying only data.
 *
 * Why templates: Tier 1 (GenerativeUINode primitives) is safe but generic; Tier 3
 * (SandboxedHtml) is unlimited but inconsistent run-to-run. Templates are the
 * sweet spot — the agent picks `weatherCard` / `statDashboard` / … and passes
 * typed data; the DESIGN (layout, motion, theming) is ours and identical every
 * time, so on-the-fly UI still looks like one coherent product.
 *
 * An agent triggers a template with a generative_ui node:
 *   { "type": "template", "props": { "name": "weatherCard", "data": { ... } } }
 * Unknown names fall back to the neutral card of `GenUIFallback` (same safety
 * posture as Tier 1). The tool refuses an unknown name (`genui_refusal`).
 *
 * SOURCE OF TRUTH: TEMPLATE_CATALOG below lists every template + its data shape.
 * The backend MUST mirror this list, or agents will emit names the renderer
 * drops: the emit_generative_ui docstring names each template, and
 * write_artifact.GENUI_TEMPLATE_SHAPES holds each `data` string, which the
 * tool returns on demand. test_genui_catalog_lockstep.py keeps them in lockstep.
 *
 * All animation is CSS/SVG only (no new bundle deps). Every template is
 * theme-agnostic: it styles via the app's CSS custom-property tokens (full
 * colors — see theme-css-vars memory) so it reads correctly in light and dark.
 */

import { createElement, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";

import GenUIFallback from "@/components/GenUIFallback";
import LiveChart from "@/components/LiveChart";
import { MarkdownBody } from "@/components/MarkdownMessage";
import { saveBlob } from "@/lib/export";
import { GenUiFileContext, reportCardActions } from "@/lib/genUiFiles";
import {
  type Report,
  type ReportVisual,
  REPORT_LIMITS,
  baseName,
  bareChartSpec,
  firstVisualSection,
  kpiGridClass,
  liteMarkdown,
  mapVisual,
  plainSummary,
  reportFrom,
  reportMeta,
} from "@/lib/report";
import { resolveIcon } from "@/lib/icons";
import { hasMoreToTheRight } from "@/lib/scrollCue";
import {
  PLAN_EDIT_COLS,
  type PlanRow,
  afterCount,
  afterLabel,
  afterOptions,
  blankRow,
  isMarked,
  markLine,
  planIncomplete,
  planRowsFrom,
  planSubmit,
  waitsOnName,
  withAfter,
} from "@/app/projects/lib/planCard";
import Badge from "@/components/ui/Badge";
import RecommendedBadge, { RECOMMENDED_RING } from "@/components/RecommendedBadge";
import { pickedFromAnswer } from "@/lib/askAnswers";
import { Checkbox } from "@/components/ui/Checkbox";
import GenUiText from "@/components/GenUiText";
import { clipWords, plainTitle } from "@/lib/fencedText";
import { templateTitle } from "@/lib/chatPlacement";
import { fieldSpec, formatCardDate } from "@/lib/cardFields";
import {
  CollapsibleCardBody,
  CollapsibleCardHeader,
  CollapsibleSection,
  useCardFold,
} from "@/components/ui/Collapsible";
import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { ReportFileButtons } from "@/app/projects/components/ReportFileButtons";
import { taskCell } from "@/app/projects/lib/matrix";
import { reportLink } from "@/app/projects/lib/reportBuilder";
import { PriorityChip } from "@/components/TaskMeta";
import EntityPill from "@/components/ui/EntityPill";
import { ValueElement } from "@/components/CardFieldValue";
import {
  type CellKind,
  type GridColumn,
  type GridLayout,
  cellText,
  gridColumns,
  gridLabels,
  gridLayout,
  isEmptyCell,
  splitMany,
} from "@/lib/dataGridLayout";
import { statusAccent } from "@/lib/statusAccent";
import { priorityChip } from "@/lib/taskCard";
import { TYPE } from "@/lib/typeScale";

type Data = Record<string, unknown>;

/** Small inline Lucide icon for templates (optional `icon` fields). */
function TIcon({ name, size = 16, color }: { name?: unknown; size?: number; color?: string }) {
  if (!name || typeof name !== "string") return null;
  // createElement (not <Icon/>) so the resolved component isn't flagged as
  // declared-during-render (react-hooks/static-components).
  return createElement(resolveIcon(name), {
    size, strokeWidth: 1.75, color: color ?? "currentColor", "aria-hidden": true,
  });
}

const str = (v: unknown, f = ""): string =>
  typeof v === "string" ? v : v == null ? f : String(v);
const num = (v: unknown, f = 0): number => {
  const n = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isFinite(n) ? n : f;
};
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

// ── Catalog (source of truth, also consumed for the fallback listing) ────────

export interface TemplateSpec {
  name: string;
  summary: string;
  /** Human-readable data shape, mirrored into GENUI_TEMPLATE_SHAPES (backend). */
  data: string;
}

export const TEMPLATE_CATALOG: TemplateSpec[] = [
  {
    name: "weatherCard",
    summary: "Current conditions + multi-day forecast, animated sky icon.",
    data: "{ location, tempC|tempF, condition('sunny'|'cloudy'|'rain'|'snow'|'storm'), highC?, lowC?, humidity?, wind?, forecast?:[{day,condition,high,low}] }",
  },
  {
    name: "statDashboard",
    summary: "Row of KPI stat tiles with delta arrows and animated count-up.",
    data: "{ title?, stats:[{ label, value, unit?, delta?:number, icon?(Lucide name e.g. 'trending-up') }] }",
  },
  {
    name: "barChart",
    summary: "Horizontal bar chart with animated grow-in bars.",
    data: "{ title?, unit?, bars:[{ label, value, tone?('primary'|'success'|'warning'|'danger') }] }",
  },
  {
    name: "sparkTrend",
    summary: "Single metric with an inline SVG sparkline trend.",
    data: "{ label, value, unit?, delta?:number, series:number[] }",
  },
  {
    name: "comparison",
    summary: "Side-by-side option comparison with a highlighted winner.",
    data: "{ title?, options:[{ name, recommended?:bool, rows:[{ label, value }] }] }",
  },
  {
    name: "progressTracker",
    summary: "Ordered steps with done/active/pending states and a progress bar.",
    data: "{ title?, steps:[{ label, state('done'|'active'|'pending') }] }",
  },
  {
    name: "recipeCard",
    summary: "Recipe with meta chips, ingredient checklist, numbered steps.",
    data: "{ title, description?, servings?, prepMinutes?, cookMinutes?, calories?, ingredients:[{item,amount?}], steps:[string], tags?:[string], tip? }",
  },
  {
    name: "flightStatus",
    summary: "Flight route card with animated progress, gates and status badge.",
    data: "{ airline?, flightNo, status('scheduled'|'boarding'|'departed'|'in-air'|'landed'|'delayed'|'cancelled'), from:{code,city?,time?,terminal?,gate?}, to:{code,city?,time?,terminal?,gate?}, progressPct?, durationMin?, date?, note? }",
  },
  {
    name: "trainStatus",
    summary: "Train journey card with stops timeline, platforms and delay badge.",
    data: "{ operator?, trainNo?, line?, status('scheduled'|'boarding'|'departed'|'arrived'|'delayed'|'cancelled'), from:{station,time?,platform?}, to:{station,time?,platform?}, stops?:[{station,time?,state?('done'|'active'|'pending')}], delayMin?, note? }",
  },
  {
    name: "formCard",
    summary: "Schema-driven form (text/number/select/slider/toggle/checkbox/date/textarea) that submits values back — pair with hitl.",
    data: "{ title?, description?, submitLabel?, fields:[{ name, label, type('text'|'number'|'select'|'slider'|'toggle'|'checkbox'|'date'|'textarea'), placeholder?, value?, required?, options?:[string], min?, max?, step?, unit?, hint? }] }",
  },
  {
    name: "optionPicker",
    summary: "Rich choice cards (single or multi select) that submit the pick back — pair with hitl.",
    data: "{ title?, description?, multi?:bool, options:[{ id, label, description?, icon?, badge?, recommended?:bool }] }",
  },
  // ── The Projects chat's views (WS-27bm S4) ───────────────────────────────
  {
    name: "timeline",
    summary: "A record's activity feed, newest first: comments, field changes, system notes, who and when.",
    data: "{ title?, taskId?, total?, rows:[{ id?, at, type, actor, body?, field?, before?, after?, via? }] }",
  },
  {
    name: "taskBoard",
    summary: "A kanban board: one column per lane, task cards with assignees and due dates; a card opens the task.",
    data: "{ title?, total?, columns:[{ id?, name, category?, tasks:[{ id, number?, title, assignees?:[string], due?, due_at?, importance?, leveraged?:bool, done?:bool }] }] }",
  },
  {
    name: "dataGrid",
    summary: "A sortable table of rows; a row with an id opens `openBase + id`.",
    data: "{ title?, columns:[string], rows:[{ id?, cells:[string|number] }], openBase? }",
  },
  {
    name: "reportCard",
    summary: "A saved report: headline tiles plus one table per section.",
    data: "{ title, period?, reportId?, stats?:[{label,value,unit?,icon?}], tables?:[{ title, columns:[string], rows:[{cells:[string|number]}] }] }",
  },
  {
    name: "planCard",
    summary: "An editable project plan (title, owner, effort, start, due, after per task, with a priority score; the owner's fit, hours and marks read-only) that submits the edited rows back — pair with hitl.",
    data: "{ title?, description?, submitLabel?, project:{ name, parent?, description? }, tasks:[{ key, title, owner, effort_mins, start?, due, after?:[key], important?:bool, leveraged?:bool, impact?, urgency?, effort?, priority?, fit?, hours?, marks?:[string], warnings?:[string] }], capacity?, warnings?:[string], risks?:[string] }",
  },  {
    name: "chart",
    summary: "A live chart in Metorite's chart language (13 kinds), with a tooltip and a legend. The WhatsApp bot sends the same spec as an image.",
    data: "{ type('bar'|'line'|'area'|'donut'|'progress'|'scatter'|'heatmap'|'radar'|'box'|'waterfall'|'funnel'|'calendar'|'gantt'), title?, subtitle?, unit?, labels?:[string], values?:[number], series?:[{name,values}], stacked?:bool, tones?:[status], totals?:[number], center?, groups?:[{name,points|values}], x_name?, y_name?, x?, y?, axes?, max?, steps?:[{label,value}|{label,total:true}], days?:[[date,n]], rows?:[{label,start,end,progress,status}], today? }",
  },
  // ── A report the agent makes (owner directive, 2026-10-16) ───────────────
  {
    name: "report",
    summary: "A report make_report wrote: KPI tiles, a summary, sections with their charts and tables, the sources and a PDF. Opens in full in the side panel.",
    data: "{ kind?('metorite.report'), path?, pdf?, title, subtitle?, for?, date?, period?, summary?(bold, italic, lists), kpis?:[{label,value,unit?,delta?,good?('up'|'down')}](8), sections:[{ heading?, text?, bullets?:[string], visual?:{ kind('chart'|'stats'|'table'|'board'|'timeline'|'gantt'|'agenda'), title?, ... } }](1-30), sources?:[string] }",
  },
];

// ── Shared bits ──────────────────────────────────────────────────────────────

/** Whether the viewer prefers reduced motion (evaluated once, lazily). */
function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
  );
}

/** Ease-out count-up for numeric values. Respects prefers-reduced-motion by
 *  starting AT the target (no animation) rather than mutating state in-effect. */
function useCountUp(target: number, ms = 700): number {
  // Lazy initial state: reduced-motion users start at the final value, so the
  // effect below never needs to setState synchronously (react-hooks lint).
  const [v, setV] = useState(() => (prefersReducedMotion() ? target : 0));
  useEffect(() => {
    if (prefersReducedMotion()) return;
    let raf = 0;
    let start: number | null = null;
    const step = (t: number) => {
      if (start == null) start = t;
      const p = Math.min((t - start) / ms, 1);
      setV(target * (1 - Math.pow(1 - p, 3)));
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return v;
}

// These are Tier-2 templates: real React components in OUR bundle, rendered
// under `html[data-theme]`, so every semantic token is defined and a literal
// here is simply a colour the theming engine cannot reach.
const TONE_COLOR: Record<string, string> = {
  // A bar with no tone is a CATEGORY, so it takes the first categorical slot.
  // It used to take `--primary`, which is the member's accent: set the accent
  // to red and every plain bar read as a warning (S8 visual review).
  neutral: "var(--cat-1)",
  primary: "var(--primary)",
  success: "var(--success)",
  warning: "var(--warning)",
  danger: "var(--destructive)",
};

/** A bar's fill: its tone when it names one, else the neutral slot. */
export function barColor(tone: unknown): string {
  return TONE_COLOR[str(tone, "neutral")] ?? TONE_COLOR.neutral;
}

/**
 * A change, with its arrow. A rise is green unless `good` says "down": for a
 * cost or a count of defects, a fall is the good news. `label` draws the
 * change as the data wrote it ("12%"), where the bare number would drop its
 * unit.
 */
function DeltaArrow({ delta, good, label }: { delta: number; good?: unknown; label?: string }): React.ReactElement | null {
  if (!delta) return <span style={{ color: "var(--muted-foreground)" }}>—</span>;
  const up = delta > 0;
  const fine = up === (str(good).toLowerCase() !== "down");
  return (
    <span style={{ color: fine ? "var(--success)" : "var(--destructive)", fontVariantNumeric: "tabular-nums" }}>
      <Icon name={up ? "ArrowUp" : "ArrowDown"} size={11} className="inline align-[-1px]" /> {label || Math.abs(delta)}
    </span>
  );
}

// ── Animated weather glyph (pure SVG/CSS) ────────────────────────────────────

/**
 * The one place in this file that is deliberately NOT themed.
 *
 * These are depictions, not chrome: a sun is yellow and a raincloud is
 * blue-grey because that is what those things look like, and routing them
 * through `--warning` / `--muted` would make the weather card recolour when
 * somebody changes theme — which is not a themed illustration, it is a broken
 * one. Semantic tokens describe MEANING (danger, success); nothing here means
 * anything, it just depicts.
 *
 * Collected here rather than inlined so the exception is one visible, argued
 * decision instead of seven literals a reader has to judge one at a time —
 * and so `theme-conformance.test.ts` can allow exactly this constant.
 */
const WEATHER_INK = {
  sun: "#fbbf24",
  cloud: "#94a3b8",
  cloudLight: "#cbd5e1",
  rain: "#38bdf8",
  snow: "#e0f2fe",
} as const;

function WeatherGlyph({ condition, size = 48 }: { condition: string; size?: number }) {
  const c = condition.toLowerCase();
  const sun = (
    <g>
      <circle cx="24" cy="24" r="9" fill={WEATHER_INK.sun}>
        <animate attributeName="r" values="9;9.8;9" dur="3s" repeatCount="indefinite" />
      </circle>
      {Array.from({ length: 8 }).map((_, i) => (
        <line
          key={i}
          x1="24" y1="24" x2="24" y2="6"
          stroke={WEATHER_INK.sun} strokeWidth="2" strokeLinecap="round"
          transform={`rotate(${i * 45} 24 24)`}
        >
          <animate attributeName="opacity" values="0.4;1;0.4" dur="2s"
            begin={`${i * 0.15}s`} repeatCount="indefinite" />
        </line>
      ))}
    </g>
  );
  const cloud = (
    <g>
      <ellipse cx="24" cy="30" rx="14" ry="8" fill={WEATHER_INK.cloud}>
        <animate attributeName="cx" values="22;26;22" dur="4s" repeatCount="indefinite" />
      </ellipse>
      <circle cx="17" cy="26" r="7" fill={WEATHER_INK.cloudLight} />
      <circle cx="30" cy="26" r="8" fill={WEATHER_INK.cloudLight} />
    </g>
  );
  const drops = (
    <g>
      {[14, 24, 34].map((x, i) => (
        <line key={x} x1={x} y1="34" x2={x - 2} y2="42" stroke={WEATHER_INK.rain} strokeWidth="2"
          strokeLinecap="round">
          <animate attributeName="opacity" values="0;1;0" dur="1s"
            begin={`${i * 0.25}s`} repeatCount="indefinite" />
        </line>
      ))}
    </g>
  );
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden>
      {c.includes("sun") || c.includes("clear") ? sun : null}
      {c.includes("cloud") || c.includes("rain") || c.includes("storm") ? cloud : null}
      {c.includes("rain") || c.includes("storm") ? drops : null}
      {c.includes("snow") ? (
        <text x="24" y="40" textAnchor="middle" fontSize="16" fill={WEATHER_INK.snow}>❄</text>
      ) : null}
    </svg>
  );
}

// ── Templates ────────────────────────────────────────────────────────────────

function WeatherCard({ data }: { data: Data }) {
  const location = templateTitle("weatherCard", data);
  // In a chat card the place is the header, so the body does not say it again.
  const fold = useCardFold();
  const temp = data.tempF != null ? num(data.tempF) : num(data.tempC);
  const unit = data.tempF != null ? "°F" : "°C";
  const condition = str(data.condition, "clear");
  const animated = useCountUp(temp);
  const forecast = arr(data.forecast);
  return (
    <div style={{
      borderRadius: 14, padding: 16, border: "1px solid var(--border)",
      background: "linear-gradient(135deg, color-mix(in srgb, var(--primary) 12%, var(--card)), var(--card))",
    }}>
      <TemplateHead icon="cloud-sun" title={location} foldOnly />
      <TemplateBody>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <WeatherGlyph condition={condition} />
        <div style={{ flex: 1 }}>
          {!fold && <div style={{ fontSize: TYPE.sm, color: "var(--muted-foreground)" }}>{location}</div>}
          <div style={{ fontSize: TYPE["4xl"], fontWeight: 600, color: "var(--foreground)", lineHeight: 1.1 }}>
            {Math.round(animated)}{unit}
          </div>
          <div style={{ fontSize: TYPE.xs, color: "var(--muted-foreground)", textTransform: "capitalize" }}>
            {condition}
          </div>
        </div>
        <div style={{ textAlign: "right", fontSize: TYPE.caption, color: "var(--muted-foreground)", lineHeight: 1.6 }}>
          {data.highC != null && <div>H {num(data.highC)}°</div>}
          {data.lowC != null && <div>L {num(data.lowC)}°</div>}
          {data.humidity != null && <div style={INLINE_ICON}><TIcon name="droplets" size={11} /> {num(data.humidity)}%</div>}
          {data.wind != null && <div style={INLINE_ICON}><TIcon name="wind" size={11} /> {str(data.wind)}</div>}
        </div>
      </div>
      {forecast.length > 0 && (
        <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
          {forecast.slice(0, 7).map((d, i) => {
            const day = (d ?? {}) as Data;
            return (
              <div key={i} style={{
                flex: 1, textAlign: "center", padding: "8px 4px", borderRadius: 10,
                background: "var(--secondary)", animation: `ccFadeUp .4s ease ${i * 0.05}s both`,
              }}>
                <div style={{ fontSize: TYPE.micro, color: "var(--muted-foreground)" }}>{str(day.day)}</div>
                <WeatherGlyph condition={str(day.condition, condition)} size={26} />
                <div style={{ fontSize: TYPE.caption, color: "var(--foreground)" }}>
                  {num(day.high)}°<span style={{ color: "var(--muted-foreground)" }}> {num(day.low)}°</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
      </TemplateBody>
      <style>{`@keyframes ccFadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}`}</style>
    </div>
  );
}

/** A small icon in a line of text, on the text's baseline. */
const INLINE_ICON: React.CSSProperties = { display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 3 };

/**
 * The delta a stat tile may draw, or null (WS-27bm S9).
 *
 * A model asked for "Overdue 2" sent `delta: -2`, and the tile drew a red
 * "▼ 2" that no tool ever printed: it copied the value. A delta whose size
 * equals the value, with no `deltaLabel` and no `period` to say what it is a
 * change over, is that copy, so the tile drops it. A real change over a
 * period carries one of the two. Exported for its test.
 */
export function shownDelta(stat: Data): number | null {
  if (stat.delta == null || (typeof stat.delta === "string" && stat.delta.trim() === "")) return null;
  // A report writes its change as text: "-3%", a minus sign, or a down
  // triangle. The sign of that text is the sign of the change. The marks
  // are escapes, so the glyph fence of the chat cards reads no glyph here.
  const delta = typeof stat.delta === "string"
    ? num(stat.delta.trim().replace(/^[\u2212\u25BC]\s*/, "-").replace(/^[\u25B2+]\s*/, ""))
    : num(stat.delta);
  const labelled = str(stat.deltaLabel).trim() !== "" || str(stat.period).trim() !== "";
  if (!labelled && delta !== 0 && Math.abs(delta) === Math.abs(num(stat.value, NaN))) return null;
  return delta;
}

/**
 * The words of a text change without its sign, or "" for a plain number:
 * "+12%" draws as "12%" after its arrow. Exported for its test.
 */
export function deltaWords(stat: Data): string {
  if (typeof stat.delta !== "string") return "";
  const t = stat.delta.trim();
  if (/^[+-]?\d+(?:\.\d+)?$/.test(t)) return "";
  return t.replace(/^[+\-\u2212\u25B2\u25BC\s]+/, "");
}

/**
 * A tile's value as a number to count up to, or null to draw it as text.
 * Only a plain number counts: "42%", "4.2L" and "1,234" keep their words,
 * which the count-up once cut to "42", "4" and "1". Exported for its test.
 */
export function tileNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const t = str(value).trim();
  return /^-?\d+(?:\.\d+)?$/.test(t) ? Number(t) : null;
}

/**
 * One stat tile. `dense` is the report card's form: it sits two or four to a
 * row in a chat column, so it takes no least width, a smaller value and a
 * label cut to one line.
 */
function StatTile({ s, dense = false }: { s: Data; dense?: boolean }) {
  const delta = shownDelta(s);
  const target = tileNumber(s.value);
  const animated = useCountUp(target ?? 0);
  const decimals = target === null ? 0 : (String(target).split(".")[1]?.length ?? 0);
  return (
    <div data-stat-tile={dense ? "dense" : ""} style={{
      flex: 1, minWidth: dense ? 0 : 110, padding: dense ? "6px 8px" : 12, borderRadius: dense ? 8 : 12,
      border: "1px solid var(--border)", background: "var(--card)",
      animation: "ccFadeUp .4s ease both",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 5, fontSize: TYPE.caption, color: "var(--muted-foreground)", minWidth: 0 }}>
        <TIcon name={s.icon} size={13} />
        <span style={dense ? { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } : undefined}
          title={dense ? str(s.label) : undefined}>{str(s.label)}</span>
      </div>
      {/* A value never cuts: a number cut to "4..." is a wrong number. */}
      <div style={{ fontSize: dense ? TYPE.base : TYPE["2xl"], fontWeight: 600, color: "var(--foreground)", fontVariantNumeric: "tabular-nums",
        ...(dense ? { overflowWrap: "anywhere" as const } : {}) }}>
        {target !== null
          ? animated.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
          : str(s.value)}
        {s.unit != null && str(s.unit) !== "" && <span style={{ fontSize: TYPE.sm, color: "var(--muted-foreground)" }}> {str(s.unit)}</span>}
      </div>
      {delta != null && (
        <div style={{ fontSize: TYPE.caption, marginTop: 2 }}><DeltaArrow delta={delta} good={s.good} label={deltaWords(s)} /></div>
      )}
    </div>
  );
}

function StatDashboard({ data }: { data: Data }) {
  const stats = arr(data.stats);
  // The tiles have no frame of their own. In a chat card the card's frame
  // holds the header and the tiles, as the other chat templates draw.
  const fold = useCardFold();
  return (
    <div style={fold ? CARD_BOX : undefined}>
      <TemplateHead icon="layout-dashboard" title={data.title != null ? str(data.title) : null} />
      <TemplateBody>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {stats.map((s, i) => <StatTile key={i} s={(s ?? {}) as Data} />)}
      </div>
      </TemplateBody>
      <style>{`@keyframes ccFadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}`}</style>
    </div>
  );
}

/** The `barChart` template's data as a chart-language spec (WAC-10g), so the
 *  chat has ONE bar chart: the one the WhatsApp bot draws too. */
const BAR_TONES: Record<string, string> = { primary: "blue", success: "green", warning: "amber", danger: "red" };
export function barChartSpec(data: Data): Data {
  const bars = arr(data.bars).map((b) => (b ?? {}) as Data);
  return {
    type: "bar",
    title: data.title,
    unit: data.unit,
    labels: bars.map((b) => str(b.label)),
    values: bars.map((b) => num(b.value)),
    tones: bars.map((b) => BAR_TONES[str(b.tone)] ?? "blue"),
  };
}

function SparkTrend({ data }: { data: Data }) {
  const series = arr(data.series).map((n) => num(n));
  const value = num(data.value);
  const animated = useCountUp(value);
  const w = 120, h = 36;
  const min = Math.min(...series, 0), max = Math.max(...series, 1);
  const range = max - min || 1;
  const pts = series.map((v, i) => {
    const x = series.length > 1 ? (i / (series.length - 1)) * w : 0;
    const y = h - ((v - min) / range) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  // In a chat card the label is the header, so the tile does not say it again.
  const fold = useCardFold();
  const label = templateTitle("sparkTrend", data);
  return (
    <div style={{ padding: 14, borderRadius: 12, border: "1px solid var(--border)", background: "var(--card)" }}>
    <TemplateHead icon="trending-up" title={label || null} foldOnly />
    <TemplateBody>
    <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
      <div style={{ flex: 1 }}>
        {!fold && <div style={{ fontSize: TYPE.caption, color: "var(--muted-foreground)" }}>{label}</div>}
        <div style={{ fontSize: TYPE["2xl"], fontWeight: 600, color: "var(--foreground)", fontVariantNumeric: "tabular-nums" }}>
          {Math.round(animated).toLocaleString()}
          {data.unit != null && <span style={{ fontSize: TYPE.sm, color: "var(--muted-foreground)" }}> {str(data.unit)}</span>}
        </div>
        {data.delta != null && <div style={{ fontSize: TYPE.caption }}><DeltaArrow delta={num(data.delta)} /></div>}
      </div>
      <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
        <polyline points={pts} fill="none" stroke="var(--primary)" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round"
          style={{ strokeDasharray: 400, strokeDashoffset: 400, animation: "ccDraw 1s ease forwards" }} />
      </svg>
      <style>{`@keyframes ccDraw{to{stroke-dashoffset:0}}`}</style>
    </div>
    </TemplateBody>
    </div>
  );
}

function Comparison({ data }: { data: Data }) {
  const options = arr(data.options).map((o) => (o ?? {}) as Data);
  const rowLabels = Array.from(new Set(
    options.flatMap((o) => arr(o.rows).map((r) => str((r as Data).label))),
  ));
  // The title strip is the card's header in a chat card. It draws while the
  // card folds even with no title, so the toggle has its place.
  const fold = useCardFold();
  return (
    <div style={{ borderRadius: 12, border: "1px solid var(--border)", overflow: "hidden" }}>
      {(data.title != null || fold?.toggle) && (
        <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)", background: "var(--card)" }}>
          <TemplateHead icon="scale" title={data.title != null ? str(data.title) : null} gap={0} />
        </div>
      )}
      <TemplateBody>
      <div style={{ display: "grid", gridTemplateColumns: `140px repeat(${options.length}, 1fr)` }}>
        <div style={{ background: "var(--card)" }} />
        {options.map((o, i) => (
          <div key={i} style={{
            padding: "8px 10px", fontSize: TYPE.xs, fontWeight: 600, textAlign: "center",
            color: "var(--foreground)",
            // The highlight family of the Recommended badge, so the column
            // and its mark read as one thing (owner report, 2026-10-09).
            background: o.recommended ? "color-mix(in srgb, var(--warning) 10%, var(--card))" : "var(--card)",
            borderLeft: "1px solid var(--border)",
            display: "flex", flexDirection: "column", alignItems: "center", gap: 4,
          }}>
            <span>{str(o.name)}</span>
            {o.recommended ? <RecommendedBadge /> : null}
          </div>
        ))}
        {rowLabels.map((label, ri) => (
          <div key={label} style={{ display: "contents" }}>
            <div style={{ padding: "8px 10px", fontSize: TYPE.caption, color: "var(--muted-foreground)",
              borderTop: "1px solid var(--border)", background: ri % 2 ? "var(--secondary)" : "transparent" }}>
              {label}
            </div>
            {options.map((o, ci) => {
              const row = arr(o.rows).map((r) => (r ?? {}) as Data).find((r) => str(r.label) === label);
              return (
                <div key={ci} style={{ padding: "8px 10px", fontSize: TYPE.xs, textAlign: "center",
                  color: "var(--foreground)", borderTop: "1px solid var(--border)", borderLeft: "1px solid var(--border)",
                  background: o.recommended ? "color-mix(in srgb, var(--warning) 6%, transparent)" : (ri % 2 ? "var(--secondary)" : "transparent") }}>
                  {row ? str(row.value) : "—"}
                </div>
              );
            })}
          </div>
        ))}
      </div>
      </TemplateBody>
    </div>
  );
}

function ProgressTracker({ data }: { data: Data }) {
  const steps = arr(data.steps).map((s) => (s ?? {}) as Data);
  const done = steps.filter((s) => str(s.state) === "done").length;
  const pct = steps.length ? (done / steps.length) * 100 : 0;
  return (
    <div style={{ padding: 14, borderRadius: 12, border: "1px solid var(--border)", background: "var(--card)" }}>
      <TemplateHead icon="gauge" title={data.title != null ? str(data.title) : null} gap={10} />
      <TemplateBody>
      <div style={{ height: 6, borderRadius: 3, background: "var(--secondary)", overflow: "hidden", marginBottom: 12 }}>
        <div style={{ height: "100%", width: `${pct}%`, background: "var(--success)",
          animation: "ccGrow .7s cubic-bezier(.22,1,.36,1) both" }} />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {steps.map((s, i) => {
          const state = str(s.state, "pending");
          const dot = state === "done" ? "var(--success)" : state === "active" ? "var(--primary)" : "var(--border)";
          return (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ width: 14, height: 14, borderRadius: "50%", background: dot,
                boxShadow: state === "active" ? "0 0 0 4px color-mix(in srgb, var(--primary) 25%, transparent)" : "none",
                animation: state === "active" ? "ccPulse 1.6s ease-in-out infinite" : "none",
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: TYPE.micro, color: "var(--success-foreground)" }}>
                {state === "done" ? <Icon name="Check" size={10} /> : null}
              </span>
              <span style={{ fontSize: TYPE.xs,
                color: state === "pending" ? "var(--muted-foreground)" : "var(--foreground)",
                fontWeight: state === "active" ? 600 : 400 }}>
                {str(s.label)}
              </span>
            </div>
          );
        })}
      </div>
      </TemplateBody>
      <style>{`@keyframes ccGrow{from{width:0}}@keyframes ccPulse{0%,100%{opacity:1}50%{opacity:.55}}`}</style>
    </div>
  );
}

function RecipeCard({ data }: { data: Data }) {
  const ingredients = arr(data.ingredients).map((x) => (x ?? {}) as Data);
  const steps = arr(data.steps).map((s) => str(s));
  const tags = arr(data.tags).map((t) => str(t));
  const meta: Array<[string, string]> = [];
  if (data.servings != null) meta.push(["users", `Serves ${num(data.servings)}`]);
  if (data.prepMinutes != null) meta.push(["timer", `Prep ${num(data.prepMinutes)} min`]);
  if (data.cookMinutes != null) meta.push(["chef-hat", `Cook ${num(data.cookMinutes)} min`]);
  if (data.calories != null) meta.push(["flame", `${num(data.calories)} kcal`]);
  // In a chat card the strip holds only the header, and the description and
  // the facts move into the body, so they fold with it.
  const fold = useCardFold();
  const intro = (
    <>
        {data.description != null && (
          <div style={{ fontSize: TYPE.xs, color: "var(--muted-foreground)", marginTop: 4 }}><GenUiText text={str(data.description)} /></div>
        )}
        {meta.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 8 }}>
            {meta.map(([icon, label]) => (
              <span key={label} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: TYPE.caption,
                color: "var(--muted-foreground)", background: "var(--secondary)", borderRadius: 999, padding: "3px 9px" }}>
                <TIcon name={icon} size={12} /> {label}
              </span>
            ))}
          </div>
        )}
    </>
  );
  const hasIntro = data.description != null || meta.length > 0;
  return (
    <div style={{ borderRadius: 14, border: "1px solid var(--border)", background: "var(--card)", overflow: "hidden" }}>
      <div style={{
        padding: "14px 16px", borderBottom: "1px solid var(--border)",
        background: "linear-gradient(135deg, color-mix(in srgb, var(--accent) 10%, var(--card)), var(--card))",
      }}>
        <TemplateHead icon="utensils" title={str(data.title, "Recipe")} gap={0} />
        {!fold && intro}
      </div>
      <TemplateBody>
      {fold && hasIntro && <div style={{ padding: "10px 16px 0" }}>{intro}</div>}
      <div style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 12 }}>
        {ingredients.length > 0 && (
          <div>
            <div style={{ fontSize: TYPE.caption, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em",
              color: "var(--muted-foreground)", marginBottom: 6 }}>Ingredients</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 4 }}>
              {ingredients.map((ing, i) => (
                <div key={i} style={{ display: "flex", alignItems: "baseline", gap: 6, fontSize: TYPE.xs,
                  color: "var(--foreground)", animation: `ccFadeUp .3s ease ${i * 0.03}s both` }}>
                  <TIcon name="check" size={11} color="var(--success)" />
                  <span>{str(ing.item)}</span>
                  {ing.amount != null && <span style={{ color: "var(--muted-foreground)", fontSize: TYPE.caption }}>{str(ing.amount)}</span>}
                </div>
              ))}
            </div>
          </div>
        )}
        {steps.length > 0 && (
          <div>
            <div style={{ fontSize: TYPE.caption, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em",
              color: "var(--muted-foreground)", marginBottom: 6 }}>Steps</div>
            <ol style={{ display: "flex", flexDirection: "column", gap: 8, margin: 0, padding: 0, listStyle: "none" }}>
              {steps.map((s, i) => (
                <li key={i} style={{ display: "flex", gap: 10, fontSize: TYPE.xs, color: "var(--foreground)",
                  animation: `ccFadeUp .3s ease ${i * 0.05}s both` }}>
                  <span style={{ flexShrink: 0, width: 20, height: 20, borderRadius: "50%", display: "flex",
                    alignItems: "center", justifyContent: "center", fontSize: TYPE.micro, fontWeight: 600,
                    background: "color-mix(in srgb, var(--primary) 15%, transparent)",
                    color: "var(--primary)" }}>{i + 1}</span>
                  <span style={{ lineHeight: 1.55 }}>{s}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
        {data.tip != null && (
          <div style={{ display: "flex", gap: 8, fontSize: TYPE.xs, color: "var(--foreground)", padding: "8px 10px",
            borderRadius: 10, background: "color-mix(in srgb, var(--accent) 10%, transparent)" }}>
            <TIcon name="lightbulb" size={14} color="var(--accent)" />
            <span><GenUiText text={str(data.tip)} /></span>
          </div>
        )}
        {tags.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {tags.map((t) => (
              <span key={t} style={{ fontSize: TYPE.micro, color: "var(--muted-foreground)",
                border: "1px solid var(--border)", borderRadius: 999, padding: "2px 8px" }}>{t}</span>
            ))}
          </div>
        )}
      </div>
      </TemplateBody>
      <style>{`@keyframes ccFadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}`}</style>
    </div>
  );
}

const STATUS_TONE: Record<string, { color: string; bg: string }> = {
  scheduled: { color: "var(--muted-foreground)", bg: "var(--secondary)" },
  boarding: { color: "var(--primary)", bg: "color-mix(in srgb, var(--primary) 14%, transparent)" },
  departed: { color: "var(--primary)", bg: "color-mix(in srgb, var(--primary) 14%, transparent)" },
  "in-air": { color: "var(--primary)", bg: "color-mix(in srgb, var(--primary) 14%, transparent)" },
  landed: { color: "var(--success)", bg: "color-mix(in srgb, var(--success) 14%, transparent)" },
  arrived: { color: "var(--success)", bg: "color-mix(in srgb, var(--success) 14%, transparent)" },
  delayed: { color: "var(--warning)", bg: "color-mix(in srgb, var(--warning) 14%, transparent)" },
  cancelled: { color: "var(--destructive)", bg: "color-mix(in srgb, var(--destructive) 14%, transparent)" },
};

function StatusBadge({ status }: { status: string }) {
  const tone = STATUS_TONE[status] ?? STATUS_TONE.scheduled;
  return (
    <span style={{ fontSize: TYPE.caption, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em",
      color: tone.color, background: tone.bg, borderRadius: 999, padding: "3px 10px" }}>
      {status}
    </span>
  );
}

function EndpointBlock({ e, align }: { e: Data; align: "left" | "right" }) {
  return (
    <div style={{ textAlign: align, minWidth: 72 }}>
      <div style={{ fontSize: TYPE.xl, fontWeight: 700, color: "var(--foreground)", letterSpacing: "0.02em" }}>
        {str(e.code ?? e.station, "—")}
      </div>
      {e.city != null && <div style={{ fontSize: TYPE.caption, color: "var(--muted-foreground)" }}>{str(e.city)}</div>}
      {e.time != null && <div style={{ fontSize: TYPE.xs, color: "var(--foreground)", fontVariantNumeric: "tabular-nums" }}>{str(e.time)}</div>}
      <div style={{ fontSize: TYPE.micro, color: "var(--muted-foreground)" }}>
        {e.terminal != null && <span>T{str(e.terminal)} </span>}
        {e.gate != null && <span>Gate {str(e.gate)}</span>}
        {e.platform != null && <span>Platform {str(e.platform)}</span>}
      </div>
    </div>
  );
}

function FlightStatus({ data }: { data: Data }) {
  const from = (data.from ?? {}) as Data;
  const to = (data.to ?? {}) as Data;
  const status = str(data.status, "scheduled");
  const pct = Math.max(0, Math.min(100, num(data.progressPct,
    status === "landed" ? 100 : status === "in-air" ? 50 : 0)));
  return (
    <div style={{ borderRadius: 14, border: "1px solid var(--border)", background: "var(--card)", padding: 16 }}>
      <TemplateHead icon="plane" title={templateTitle("flightStatus", data)} gap={12}
        aside={data.date != null ? str(data.date) : undefined} end={<StatusBadge status={status} />} />
      <TemplateBody>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <EndpointBlock e={from} align="left" />
        <div style={{ flex: 1, position: "relative", height: 24 }}>
          <div style={{ position: "absolute", top: 11, left: 0, right: 0, height: 2, borderRadius: 1,
            background: "var(--secondary)" }} />
          <div style={{ position: "absolute", top: 11, left: 0, width: `${pct}%`, height: 2, borderRadius: 1,
            background: "var(--primary)", transition: "width 0.6s var(--cc-ease, ease)" }} />
          <span style={{ position: "absolute", top: 0, left: `${pct}%`, transform: "translateX(-50%)",
            animation: "ccFadeUp .5s ease both" }}>
            <TIcon name="plane" size={20} color="var(--primary)" />
          </span>
        </div>
        <EndpointBlock e={to} align="right" />
      </div>
      {(data.durationMin != null || data.note != null) && (
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 10, fontSize: TYPE.caption,
          color: "var(--muted-foreground)" }}>
          <span>{data.durationMin != null ? `${Math.floor(num(data.durationMin) / 60)}h ${num(data.durationMin) % 60}m` : ""}</span>
          <span>{data.note != null ? str(data.note) : ""}</span>
        </div>
      )}
      </TemplateBody>
      <style>{`@keyframes ccFadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}`}</style>
    </div>
  );
}

function TrainStatus({ data }: { data: Data }) {
  const from = (data.from ?? {}) as Data;
  const to = (data.to ?? {}) as Data;
  const status = str(data.status, "scheduled");
  const stops = arr(data.stops).map((s) => (s ?? {}) as Data);
  const delay = num(data.delayMin);
  return (
    <div style={{ borderRadius: 14, border: "1px solid var(--border)", background: "var(--card)", padding: 16 }}>
      <TemplateHead icon="train-front" title={templateTitle("trainStatus", data)} gap={12} end={
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {delay > 0 && (
            <span style={{ fontSize: TYPE.caption, fontWeight: 600, color: "var(--warning)",
              background: "color-mix(in srgb, var(--warning) 14%, transparent)",
              borderRadius: 999, padding: "3px 10px" }}>+{delay} min</span>
          )}
          <StatusBadge status={status} />
        </div>
      } />
      <TemplateBody>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12, marginBottom: stops.length ? 12 : 0 }}>
        <EndpointBlock e={from} align="left" />
        <div style={{ flex: 1, borderTop: "2px dashed var(--border)", marginTop: 12 }} />
        <EndpointBlock e={to} align="right" />
      </div>
      {stops.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 0, borderLeft: "2px solid var(--secondary)",
          marginLeft: 6, paddingLeft: 14 }}>
          {stops.map((s, i) => {
            const state = str(s.state, "pending");
            const dot = state === "done" ? "var(--success)"
              : state === "active" ? "var(--primary)" : "var(--border)";
            return (
              <div key={i} style={{ position: "relative", padding: "5px 0", fontSize: TYPE.xs,
                color: state === "pending" ? "var(--muted-foreground)" : "var(--foreground)",
                animation: `ccFadeUp .3s ease ${i * 0.04}s both` }}>
                <span style={{ position: "absolute", left: -20, top: 9, width: 10, height: 10,
                  borderRadius: "50%", background: dot,
                  boxShadow: state === "active" ? "0 0 0 3px color-mix(in srgb, var(--primary) 25%, transparent)" : "none" }} />
                <span style={{ fontWeight: state === "active" ? 600 : 400 }}>{str(s.station)}</span>
                {s.time != null && <span style={{ color: "var(--muted-foreground)", marginLeft: 8,
                  fontVariantNumeric: "tabular-nums" }}>{str(s.time)}</span>}
              </div>
            );
          })}
        </div>
      )}
      {data.note != null && (
        <div style={{ marginTop: 8, fontSize: TYPE.caption, color: "var(--muted-foreground)" }}>{str(data.note)}</div>
      )}
      </TemplateBody>
      <style>{`@keyframes ccFadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}`}</style>
    </div>
  );
}

const FIELD_INPUT_STYLE: React.CSSProperties = {
  width: "100%", fontSize: TYPE.xs, color: "var(--foreground)", background: "var(--secondary)",
  border: "1px solid var(--border)", borderRadius: 8, padding: "7px 10px", outline: "none",
};

function FormCard({ data, ctx }: { data: Data; ctx?: TemplateCtx }) {
  const fields = arr(data.fields).map((f) => (f ?? {}) as Data);
  const [values, setValues] = useState<Record<string, unknown>>(() => {
    const init: Record<string, unknown> = {};
    for (const f of fields) {
      const name = str(f.name);
      if (!name) continue;
      init[name] = f.value ?? (str(f.type) === "toggle" || str(f.type) === "checkbox" ? false
        : str(f.type) === "slider" ? num(f.min, 0) : "");
    }
    return init;
  });
  const [submitted, setSubmitted] = useState(false);
  const set = (name: string, v: unknown) => setValues((p) => ({ ...p, [name]: v }));
  const missing = fields.some((f) => {
    if (!f.required) return false;
    const v = values[str(f.name)];
    return v == null || v === "";
  });
  const submit = () => {
    if (!ctx?.onAction || submitted) return;
    setSubmitted(true);
    const label = str(data.submitLabel ?? data.title, "Form");
    ctx.onAction(`${label} — ${JSON.stringify(values)}`);
  };
  return (
    <div style={{ borderRadius: 14, border: "1px solid var(--border)", background: "var(--card)", padding: 16 }}>
      <TemplateHead icon="clipboard-list" rich gap={4} title={data.title != null ? str(data.title) : null} />
      <TemplateBody>
      {data.description != null && (
        <div style={{ fontSize: TYPE.xs, color: "var(--muted-foreground)", marginBottom: 10 }}><GenUiText text={str(data.description)} /></div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {fields.map((f, i) => {
          const name = str(f.name);
          const type = str(f.type, "text");
          const v = values[name];
          if (type === "checkbox") {
            // WS-46 P13: one row of a batch the member can leave out. The
            // label is the row, and the hint carries its facts in plain words.
            return (
              <label key={name || i} className="flex min-w-0 items-start gap-2"
                style={{ animation: `ccFadeUp .3s ease ${i * 0.04}s both` }}>
                <Checkbox size="sm" checked={!!v} disabled={submitted} className="mt-0.5"
                  onChange={(e) => set(name, e.target.checked)} />
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="text-xs text-foreground" style={{ overflowWrap: "anywhere" }}>
                    {str(f.label, name)}
                  </span>
                  {f.hint != null && (
                    <span className="text-[11px] text-muted-foreground" style={{ overflowWrap: "anywhere" }}>
                      <GenUiText text={str(f.hint)} />
                    </span>
                  )}
                </span>
              </label>
            );
          }
          return (
            <label key={name || i} style={{ display: "flex", flexDirection: "column", gap: 4,
              animation: `ccFadeUp .3s ease ${i * 0.04}s both` }}>
              <span style={{ fontSize: TYPE.caption, color: "var(--muted-foreground)" }}>
                {str(f.label, name)}{f.required ? " *" : ""}
                {type === "slider" && (
                  <span style={{ float: "right", color: "var(--foreground)", fontVariantNumeric: "tabular-nums" }}>
                    {String(v)}{f.unit != null ? ` ${str(f.unit)}` : ""}
                  </span>
                )}
              </span>
              {type === "select" ? (
                <select value={str(v)} disabled={submitted} style={FIELD_INPUT_STYLE}
                  onChange={(e) => set(name, e.target.value)}>
                  <option value="" disabled>{str(f.placeholder, "Choose…")}</option>
                  {arr(f.options).map((o) => (
                    <option key={str(o)} value={str(o)}>{str(o)}</option>
                  ))}
                </select>
              ) : type === "textarea" ? (
                <textarea value={str(v)} disabled={submitted} rows={3} placeholder={str(f.placeholder)}
                  style={{ ...FIELD_INPUT_STYLE, resize: "vertical" }}
                  onChange={(e) => set(name, e.target.value)} />
              ) : type === "toggle" ? (
                <button type="button" disabled={submitted} onClick={() => set(name, !v)}
                  aria-pressed={!!v}
                  style={{ width: 40, height: 22, borderRadius: 999, border: "1px solid var(--border)",
                    background: v ? "var(--primary)" : "var(--secondary)", position: "relative",
                    cursor: submitted ? "default" : "pointer", transition: "background 0.2s var(--cc-ease, ease)" }}>
                  <span style={{ position: "absolute", top: 2, left: v ? 20 : 2, width: 16, height: 16,
                    borderRadius: "50%", background: v ? "var(--primary-foreground)" : "var(--foreground)",
                    transition: "left 0.2s var(--cc-ease, ease)" }} />
                </button>
              ) : type === "slider" ? (
                <input type="range" disabled={submitted} min={num(f.min, 0)} max={num(f.max, 100)}
                  step={num(f.step, 1)} value={num(v, num(f.min, 0))}
                  onChange={(e) => set(name, parseFloat(e.target.value))}
                  style={{ width: "100%", accentColor: "var(--primary)" }} />
              ) : (
                <input type={type === "number" ? "number" : type === "date" ? "date" : "text"}
                  value={str(v)} disabled={submitted} placeholder={str(f.placeholder)}
                  min={f.min != null ? num(f.min) : undefined} max={f.max != null ? num(f.max) : undefined}
                  style={FIELD_INPUT_STYLE}
                  onChange={(e) => set(name, type === "number" && e.target.value !== ""
                    ? parseFloat(e.target.value) : e.target.value)} />
              )}
            </label>
          );
        })}
      </div>
      {submitted ? (
        <div role="status" className="mt-3 flex items-center gap-2">
          <Badge tone="success" icon="Check">{PICKER_SENT}</Badge>
          <span style={{ fontSize: TYPE.caption, color: "var(--muted-foreground)" }}>{PICKER_SENT_NOTE}</span>
        </div>
      ) : (
        <Button size="sm" onClick={submit} disabled={!ctx?.onAction || missing} className="mt-3">
          {str(data.submitLabel, "Submit")}
        </Button>
      )}
      </TemplateBody>
      <style>{`@keyframes ccFadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}`}</style>
    </div>
  );
}

/** The words under a picker once its answer went, or when it cannot send. */
export const PICKER_SENT = "Sent";
export const PICKER_SENT_NOTE = "Your answer went to the assistant.";
export const PICKER_INERT_NOTE = "This card cannot send an answer here.";

function OptionPicker({ data, ctx }: { data: Data; ctx?: TemplateCtx }) {
  const options = arr(data.options).map((o) => (o ?? {}) as Data);
  const multi = !!data.multi;
  const [clicked, setPicked] = useState<string[]>([]);
  const [clickedSend, setSubmitted] = useState(false);
  // An answer the run already got survives a remount: a reload, or a move of
  // the card in the tree. Without it an answered picker drew as new, and a
  // second click sent the answer again (review round 1, P2-c).
  const answered = ctx?.answered ?? "";
  const submitted = clickedSend || !!answered;
  const picked = clicked.length > 0
    ? clicked
    : pickedFromAnswer(answered, options.map((o, i) => ({
        id: str(o.id, str(o.label, String(i))), label: str(o.label) })));
  // No handler means no way to answer: say so, and take no click. A click
  // that does nothing reads as a broken card (owner report, 2026-10-09).
  const inert = !ctx?.onAction && !submitted;
  const locked = submitted || inert;
  const submit = (ids: string[]) => {
    if (!ctx?.onAction || submitted || !ids.length) return;
    setSubmitted(true);
    const labels = ids.map((id) =>
      str(options.find((o) => str(o.id) === id)?.label, id));
    ctx.onAction(`Selected: ${labels.join(", ")}`);
  };
  const toggle = (id: string) => {
    if (locked) return;
    if (!multi) {
      setPicked([id]);
      submit([id]);
      return;
    }
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  };
  return (
    <div style={CARD_BOX}>
      <TemplateHead icon="list-checks" gap={4} rich title={data.title != null ? str(data.title) : null} />
      <TemplateBody>
      {data.description != null && (
        <div style={{ fontSize: TYPE.xs, color: "var(--muted-foreground)", marginBottom: 10 }}><GenUiText text={str(data.description)} /></div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 8 }}>
        {options.map((o, i) => {
          const id = str(o.id, str(o.label, String(i)));
          const active = picked.includes(id);
          const recommended = !!o.recommended;
          return (
            <button key={id} type="button" onClick={() => toggle(id)} disabled={locked}
              aria-pressed={active}
              // A column from the top: a button centres its content, so an
              // option with a Recommended badge set the row height and the
              // titles beside it sat lower (follow-up 5 of #766).
              style={{ textAlign: "left", display: "flex", flexDirection: "column", justifyContent: "flex-start",
                borderRadius: 12, padding: "10px 12px", cursor: locked ? "default" : "pointer",
                border: `1px solid ${active ? "var(--primary)" : recommended ? RECOMMENDED_RING : "var(--border)"}`,
                background: active ? "color-mix(in srgb, var(--primary) 10%, var(--card))" : "var(--card)",
                boxShadow: active ? "0 0 0 3px color-mix(in srgb, var(--primary) 15%, transparent)" : "none",
                // `backwards`, never `both`: a `both` fill holds the last
                // frame's opacity after the motion, and an animation value
                // beats the inline opacity below. So the lock never showed.
                transition: "all 0.15s var(--cc-ease, ease)", animation: `ccFadeUp .3s ease ${i * 0.05}s backwards`,
                opacity: locked && !active ? 0.5 : 1 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                <TIcon name={active ? "check" : o.icon} size={14} color="var(--primary)" />
                <span style={{ fontSize: TYPE.xs, fontWeight: 600, color: "var(--foreground)" }}>{str(o.label)}</span>
                {recommended ? <RecommendedBadge /> : null}
              </div>
              {o.description != null && (
                <div style={{ fontSize: TYPE.caption, color: "var(--muted-foreground)", marginTop: 3, lineHeight: 1.45 }}>
                  <GenUiText text={str(o.description)} />
                </div>
              )}
              {o.badge != null && (
                <div style={{ marginTop: 5 }}>
                  <Badge tone="primary" size="xs">{str(o.badge)}</Badge>
                </div>
              )}
            </button>
          );
        })}
      </div>
      {multi && !submitted && (
        <Button size="sm" onClick={() => submit(picked)} disabled={inert || !picked.length} className="mt-3">
          Confirm selection
        </Button>
      )}
      {submitted && (
        <div role="status" style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12 }}>
          <Badge tone="success" icon="Check">{PICKER_SENT}</Badge>
          <span style={{ fontSize: TYPE.caption, color: "var(--muted-foreground)" }}>{PICKER_SENT_NOTE}</span>
        </div>
      )}
      {inert && (
        <div style={{ marginTop: 12, fontSize: TYPE.caption, color: "var(--muted-foreground)" }}>{PICKER_INERT_NOTE}</div>
      )}
      </TemplateBody>
      <style>{`@keyframes ccFadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}`}</style>
    </div>
  );
}

// ── The Projects chat's views (WS-27bm S4) ────────────────────────────────────
//
// Five templates the Projects assistant draws from its own reads
// (`skill_projects/views.py`, `forms.py`). Same rules as the rest of the
// file: tokens only, CSS motion only, an unknown or missing field renders
// as nothing. A row with an `id` is a plain link into the app (`?task=`),
// which is the same door the tool cards use.

const CELL: React.CSSProperties = { fontSize: TYPE.xs, color: "var(--foreground)" };
const MUTED: React.CSSProperties = { fontSize: TYPE.caption, color: "var(--muted-foreground)" };
/** The frame of a chat template: the radius and the padding of the
 *  generative-UI `card` (`rounded-lg p-3`), so the two read as one kind. */
const CARD_BOX: React.CSSProperties = {
  borderRadius: "var(--radius)", border: "1px solid var(--border)", background: "var(--card)", padding: 12,
};

/**
 * The templates that draw their own title row as the chat card's one header
 * (owner report, 2026-10-13). In the transcript the roll-up drew its own
 * frame and title round a template, and the template drew its title again
 * inside: two titles and a card in a card. Each of these draws
 * {@link TemplateHead} instead, which is the card's `RollupHeader` inside
 * a fold and the template's own row anywhere else. `MessageBubble` passes
 * `header="own"` for them (`genUiRootOwnsHead`).
 * Fence: `src/lib/chatCardConformance.test.ts`.
 */
export const OWN_HEAD_TEMPLATES: ReadonlySet<string> = new Set([
  "timeline", "taskBoard", "dataGrid", "reportCard", "planCard", "optionPicker",
  // The follow-up of #945: every other template that drew a title of its own.
  "weatherCard", "statDashboard", "sparkTrend", "comparison", "progressTracker",
  "recipeCard", "flightStatus", "trainStatus", "formCard", "barChart", "chart",
  // A report the agent makes (2026-10-16).
  "report",
]);

/**
 * A template's title row. Inside a folding chat card it is the card's one
 * header and toggle (`CollapsibleCardHeader`, with the template's icon in
 * the icon slot). Anywhere else (the side panel, a nested table) it draws
 * the row the template always drew. With no title, a chat card still gets
 * the toggle while it folds, and draws nothing while it is open.
 */
function TemplateHead({ icon, title, href = null, rich = false, aside, end, actions, foldOnly = false, gap = 8 }: {
  icon: string;
  /** The title's words, or null for a template with no title. */
  title: string | null;
  /** Where the title links to, in the app. */
  href?: string | null;
  /** The title is generative-UI text: Markdown and «names» outside a fold. */
  rich?: boolean;
  /** Plain words after the title. Never a link or a control. */
  aside?: string;
  /** A mark at the row's end, such as a status badge. It draws BESIDE the
   *  toggle, never inside it, and it is never a control. */
  end?: React.ReactNode;
  /** The card's own controls, such as "Open" and "PDF". They draw BESIDE the
   *  toggle, never inside it, as the Email cards' `CardHead` draws them. The
   *  title gives way first: it cuts to one line, with its words as a tip. */
  actions?: React.ReactNode;
  /** Draw only inside a chat card. A template whose own body names it (the
   *  weather card's place) draws no title row anywhere else. */
  foldOnly?: boolean;
  gap?: number;
}) {
  const fold = useCardFold();
  if (fold) {
    // ⚠️ The header is the fold's trigger, a `<button>`. A link inside it
    // was invalid HTML, and one click followed the link AND folded the card
    // (review of #945, P2). So the title is plain words, and the link is
    // its own control beside the header, as `ToolCardShell` puts its X.
    return (
      <div className="flex min-w-0 items-center gap-1.5">
        <div className="min-w-0 flex-1">
          <CollapsibleCardHeader
            lead={<TIcon name={icon} size={14} color="var(--primary)" />}
            title={title === null ? undefined : plainTitle(title)}
            quiet={title === null}
            aside={aside}
            className="text-sm"
            titleClassName="font-semibold text-foreground"
          />
        </div>
        {end != null && <div data-template-end="" className="flex shrink-0 items-center">{end}</div>}
        {actions != null && <div data-template-actions="" className="flex shrink-0 items-center gap-1">{actions}</div>}
        {href && (
          <a href={href} data-template-open="" aria-label={`Open ${title === null ? "it" : plainTitle(title)}`}
            title="Open" className="cc-control flex shrink-0 rounded p-1 text-muted-foreground hover:text-foreground">
            <Icon name="ExternalLink" size={13} />
          </a>
        )}
      </div>
    );
  }
  if (title === null || foldOnly) return null;
  const words = rich ? <GenUiText text={title} /> : title;
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: gap }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
        <TIcon name={icon} size={15} color="var(--primary)" />
        <span title={actions != null ? plainTitle(title) : undefined} style={{ fontSize: TYPE.sm, fontWeight: 600, color: "var(--foreground)",
          ...(actions != null ? { minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const } : {}) }}>
          <TitleLink href={href}>{words}</TitleLink>
        </span>
      </div>
      {(aside != null || end != null || actions != null) && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          {aside != null && <span style={MUTED}>{aside}</span>}
          {end}
          {actions != null && <div data-template-actions="" className="flex items-center gap-1">{actions}</div>}
        </div>
      )}
    </div>
  );
}

/** The part of a template under its title: the fold's body in a chat card. */
function TemplateBody({ children }: { children: React.ReactNode }) {
  const fold = useCardFold();
  if (!fold) return <>{children}</>;
  return <CollapsibleCardBody className="pt-2">{children}</CollapsibleCardBody>;
}

/** The app link for a row that carries a task id, or null. Exported for its test. */
export function taskHref(id: unknown, base = "/projects?task="): string | null {
  const s = str(id);
  return /^[0-9a-f-]{36}$/i.test(s) ? `${base}${encodeURIComponent(s)}` : null;
}

function TitleLink({ href, children }: { href: string | null; children: React.ReactNode }) {
  return href
    ? <a href={href} style={{ color: "var(--foreground)", textDecoration: "none" }}>{children}</a>
    : <>{children}</>;
}

/** The dot of a report's milestone, by its state (`make_document`'s states). */
const MILESTONE_TONE: Record<string, string> = {
  done: "var(--success)",
  current: "var(--info)",
  late: "var(--destructive)",
  blocked: "var(--warning)",
};

function Timeline({ data }: { data: Data }) {
  const rows = arr(data.rows).map((r) => (r ?? {}) as Data);
  const tone = (type: string): string =>
    // A category, so the categorical ramp: never `--primary`, which is the
    // member's accent and means "selected", nor `--warning`, which means
    // caution (visual review, 2026-09-23).
    type === "comment" ? "var(--cat-1)"
      : type === "field_change" ? "var(--cat-3)"
        : type === "merge" || type === "attachment" ? "var(--cat-5)"
          // A report's milestone carries a STATE, and a state is a status:
          // its hue is information, so a semantic tone (AGENTS.md rule 7).
          : MILESTONE_TONE[type] ?? "var(--border)";
  return (
    <div style={CARD_BOX}>
      <TemplateHead icon="history" title={str(data.title, "Timeline")} href={taskHref(data.taskId)}
        aside={data.total != null ? `${rows.length} of ${num(data.total)}` : undefined} />
      <TemplateBody>
      {rows.length === 0 && <div style={MUTED}>Nothing has happened here yet.</div>}
      {/* The scroll box and the line are two boxes. The dots sit 20px left of
          each row, over the line. On one box with overflowY the scroll clip
          cut every dot in half (S8 visual review); the outer box now holds the
          scroll, and the line sits inside it with room for the dots. */}
      <div data-timeline-scroll style={{ maxHeight: 420, overflowY: "auto" }}>
      <div style={{ borderLeft: "2px solid var(--secondary)", marginLeft: 6, paddingLeft: 14 }}>
        {rows.map((r, i) => {
          const type = str(r.type);
          return (
            <div key={str(r.id) || i} style={{ position: "relative", padding: "6px 0",
              animation: `ccFadeUp .3s ease ${Math.min(i, 20) * 0.03}s both` }}>
              <span style={{ position: "absolute", left: -20, top: 11, width: 10, height: 10,
                borderRadius: "50%", background: tone(type) }} />
              <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
                <span style={{ ...CELL, fontWeight: 600 }}>{str(r.actor, "someone")}</span>
                <span style={MUTED}>{type.replace(/_/g, " ")}</span>
                <span style={{ ...MUTED, marginLeft: "auto", fontVariantNumeric: "tabular-nums" }}>
                  {formatCardDate(str(r.at))}
                </span>
              </div>
              {r.body != null && str(r.body) !== "" && (
                <div style={{ ...CELL, whiteSpace: "pre-wrap", marginTop: 2 }}><GenUiText text={str(r.body)} /></div>
              )}
              {r.field != null && (
                <div style={{ ...CELL, marginTop: 2 }}>
                  <span style={MUTED}>{fieldSpec(str(r.field)).label}: </span>
                  <span style={{ textDecoration: "line-through", color: "var(--muted-foreground)" }}>{str(r.before, "—")}</span>
                  <span style={MUTED}> → </span>
                  <span>{str(r.after, "—")}</span>
                </div>
              )}
              {r.via != null && <div style={MUTED}>via {str(r.via)}</div>}
            </div>
          );
        })}
      </div>
      </div>
      </TemplateBody>
      <style>{`@keyframes ccFadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}`}</style>
    </div>
  );
}

function TaskChip({ t }: { t: Data }) {
  const href = taskHref(t.id);
  // D78 (H-173): THE priority chip, the level the Projects card draws, from
  // the one client rule. Low Priority earns no chip there, so none here.
  const level = taskCell({
    importance: typeof t.importance === "number" ? t.importance : null,
    leveraged: t.leveraged === true,
    due_at: typeof t.due_at === "string" ? t.due_at : typeof t.due === "string" ? t.due : null,
  });
  const people = arr(t.assignees).map((a) => str(a)).filter(Boolean);
  const chip = level !== "low-priority";
  const due = t.due != null && str(t.due) !== "";
  // The title has the card's whole width (follow-up of #716 and #735). Beside
  // the priority chip in a 180px lane it kept about 60px, and a long title
  // wrapped one word per line. The chip now sits in the facts row under it,
  // with the assignees as person chips and the due date.
  const body = (
    <div data-board-card="" style={{ borderRadius: 10, border: "1px solid var(--border)", background: "var(--secondary)",
      padding: "6px 8px", marginBottom: 6, opacity: t.done ? 0.6 : 1 }}>
      <div style={{ ...CELL, overflowWrap: "anywhere" }}>
        {t.number != null && <span style={{ ...MUTED, marginRight: 4 }}>#{str(t.number)}</span>}
        <span style={{ textDecoration: t.done ? "line-through" : "none" }}>{str(t.title)}</span>
      </div>
      {/* A report's board card: one muted line under the title. */}
      {str(t.meta) !== "" && <div style={{ ...MUTED, marginTop: 2, overflowWrap: "anywhere" }}>{str(t.meta)}</div>}
      {(chip || people.length > 0 || due) && (
        <div style={{ ...MUTED, display: "flex", gap: 4, marginTop: 4, flexWrap: "wrap", alignItems: "center" }}>
          {chip && <PriorityChip chip={priorityChip(level)} />}
          {people.map((p, i) => <PersonChip key={i} text={p} />)}
          {due && <span>due {formatCardDate(str(t.due))}</span>}
        </div>
      )}
    </div>
  );
  return href ? <a href={href} style={{ textDecoration: "none", display: "block" }}>{body}</a> : body;
}

function TaskBoard({ data }: { data: Data }) {
  const columns = arr(data.columns).map((c) => (c ?? {}) as Data);
  // A cue while lanes sit off to the right (`lib/scrollCue.ts`). In the rail
  // the third lane was cut at the edge with nothing to say it scrolls.
  const stripRef = useRef<HTMLDivElement>(null);
  const [more, setMore] = useState(false);
  useEffect(() => {
    const el = stripRef.current;
    if (!el) return;
    const check = () => setMore(hasMoreToTheRight(el.scrollWidth, el.clientWidth, el.scrollLeft));
    check();
    el.addEventListener("scroll", check, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(check);
    observer?.observe(el);
    return () => {
      el.removeEventListener("scroll", check);
      observer?.disconnect();
    };
  }, [columns.length]);
  return (
    <div style={CARD_BOX}>
      <TemplateHead icon="kanban" title={str(data.title, "Board")} gap={10}
        aside={data.total != null ? `${num(data.total)} tasks` : undefined} />
      <TemplateBody>
      <div ref={stripRef} style={{ display: "flex", gap: 10, overflowX: "auto", paddingBottom: 4 }}>
        {columns.map((c, i) => {
          const tasks = arr(c.tasks).map((t) => (t ?? {}) as Data);
          return (
            <div key={str(c.id) || i} style={{ minWidth: 180, flex: "0 0 180px" }}>
              <div style={{ ...MUTED, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.4,
                marginBottom: 6, display: "flex", justifyContent: "space-between" }}>
                <span>{str(c.name)}</span><span>{tasks.length}</span>
              </div>
              {tasks.map((t, k) => <TaskChip key={str(t.id) || k} t={t} />)}
            </div>
          );
        })}
      </div>
      {more && (
        <div style={{ ...MUTED, display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 4, marginTop: 4 }}>
          More lanes to the right <TIcon name="arrow-right" size={12} color="var(--muted-foreground)" />
        </div>
      )}
      </TemplateBody>
    </div>
  );
}

/**
 * A date cell (`2026-09-30`, or a date and a time) never wraps (WS-27bm S9).
 * In the 26rem rail the column was narrow enough that the browser broke the
 * date at a hyphen, and "2026-09-" sat above "30". Exported for its test.
 */
export function isDateCell(cell: unknown): boolean {
  return /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?$/.test(str(cell).trim());
}

/**
 * A person as a chip, through the one value renderer (`ValueElement`). It
 * owns the `agent:` rule and `personOf`, so the board, the grid and a card
 * draw a person the same way (review round 1, AGENTS.md rule 4).
 */
function PersonChip({ text }: { text: string }) {
  return <ValueElement item={{ text, named: true }} kind="person" />;
}

/**
 * One cell, drawn by its kind (`lib/dataGridLayout.ts` rule 1). A status
 * draws here, because its colour can come from a hidden category cell. Every
 * other kind draws through `ValueElement`, the one value renderer.
 */
function GridCell({ kind, cell, category }: { kind: CellKind; cell: unknown; category?: unknown }) {
  if (isEmptyCell(cell)) {
    return <span style={{ color: "var(--muted-foreground)" }}>{kind === "text" ? cellText(cell) : ""}</span>;
  }
  const text = cellText(cell).trim();
  if (kind === "status") {
    return (
      <EntityPill fit kind="status" label={text}
        accent={statusAccent({ category: category == null ? undefined : cellText(category), name: text })} />
    );
  }
  if (kind === "tag" || kind === "person") {
    return (
      <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 4, maxWidth: "100%" }}>
        {splitMany(cell).map((v, i) => <ValueElement key={i} item={{ text: v, named: true }} kind={kind} />)}
      </span>
    );
  }
  if (kind === "text") return <>{text}</>;
  return <ValueElement item={{ text, named: true }} kind={kind} />;
}

/** The kinds whose cell never wraps: a short value or one chip. */
const NOWRAP: ReadonlySet<CellKind> = new Set(["status", "category", "date"]);

/** The root font size in px, so the stack threshold follows the density. */
function rootRemPx(): number {
  if (typeof window === "undefined") return 16;
  const px = parseFloat(window.getComputedStyle(document.documentElement).fontSize);
  return Number.isFinite(px) && px > 0 ? px : 16;
}

const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/**
 * The `dataGrid` template. It draws a table where the box is wide enough,
 * and stacked rows where it is not (`lib/dataGridLayout.ts`). `layout` fixes
 * one of the two, for a test. The data may also carry `kinds`, one card kind
 * per column (`task_dataset` sends them). Without them, each column takes
 * the kind of its card label.
 */
export function DataGrid({ data, layout }: { data: Data; layout?: GridLayout }) {
  const kinds = Array.isArray(data.kinds)
    ? data.kinds.map((k) => (typeof k === "string" ? k : undefined))
    : undefined;
  const rows = arr(data.rows).map((r) => (r ?? {}) as Data);
  // A cell past the last label gets a "Column N" label, and never drops.
  const labels = gridLabels(arr(data.columns).map((c) => str(c)), rows);
  const { shown, categoryAt, primaryAt } = gridColumns(labels, kinds);
  const base = str(data.openBase, "/projects?task=");
  const [sort, setSort] = useState<{ col: number; dir: 1 | -1 } | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [more, setMore] = useState(false);
  const mode = layout ?? gridLayout(width, shown, rootRemPx());
  useIsoLayoutEffect(() => {
    const el = boxRef.current;
    if (!el || layout) return;
    const measure = () => setWidth(el.clientWidth);
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(el);
    return () => observer?.disconnect();
  }, [layout]);
  // The cue while columns sit off to the right (`lib/scrollCue.ts`): a
  // column is never cut at the edge with nothing to say the table scrolls.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || mode !== "table") return;
    const check = () => setMore(hasMoreToTheRight(el.scrollWidth, el.clientWidth, el.scrollLeft));
    check();
    el.addEventListener("scroll", check, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(check);
    observer?.observe(el);
    return () => {
      el.removeEventListener("scroll", check);
      observer?.disconnect();
    };
  }, [mode, rows.length]);
  const sorted = sort
    ? [...rows].sort((a, b) => {
      const av = arr(a.cells)[sort.col];
      const bv = arr(b.cells)[sort.col];
      const an = typeof av === "number" ? av : parseFloat(cellText(av));
      const bn = typeof bv === "number" ? bv : parseFloat(cellText(bv));
      if (Number.isFinite(an) && Number.isFinite(bn)) return (an - bn) * sort.dir;
      return cellText(av).localeCompare(cellText(bv)) * sort.dir;
    })
    : rows;
  const cellOf = (r: Data, c: GridColumn) => (
    <GridCell kind={c.kind} cell={arr(r.cells)[c.index]}
      category={categoryAt >= 0 ? arr(r.cells)[categoryAt] : undefined} />
  );
  const titleOf = (r: Data) => {
    const href = taskHref(r.id, base);
    // The title draws by its column's kind: a groups table by stage puts the
    // stage first, and it reads as its label, never the key (review round 1).
    const kind = shown.find((c) => c.index === primaryAt)?.kind ?? "text";
    const cell = <GridCell kind={kind} cell={arr(r.cells)[primaryAt]}
      category={categoryAt >= 0 ? arr(r.cells)[categoryAt] : undefined} />;
    return href ? <a href={href} style={{ color: "var(--primary)", textDecoration: "none" }}>{cell}</a> : cell;
  };
  const lead = shown.find((c) => c.role === "lead");
  return (
    <div ref={boxRef} data-grid-layout={mode} style={CARD_BOX}>
      <TemplateHead icon="table-2" title={data.title != null ? str(data.title) : null} />
      <TemplateBody>
      {mode === "stacked" ? (
        <div style={{ maxHeight: 420, overflowY: "auto" }}>
          {rows.map((r, i) => {
            const facts = shown.filter((c) => c.role === "secondary" && !isEmptyCell(arr(r.cells)[c.index]));
            return (
              <div key={str(r.id) || i} data-grid-row="" style={{ padding: "6px 0",
                borderBottom: i < rows.length - 1 ? "1px solid var(--border)" : "none" }}>
                <div style={{ ...CELL, overflowWrap: "anywhere" }}>
                  {lead && <span style={{ ...MUTED, marginRight: 4 }}>{cellText(arr(r.cells)[lead.index])}</span>}
                  {titleOf(r)}
                </div>
                {facts.length > 0 && (
                  <div style={{ ...CELL, display: "flex", flexWrap: "wrap", alignItems: "center", gap: "4px 10px", marginTop: 3 }}>
                    {facts.map((c) => (
                      c.kind === "text" || c.kind === "date" ? (
                        <span key={c.index} data-grid-fact="" style={{ overflowWrap: "anywhere" }}>
                          <span style={MUTED}>{c.label} </span>{cellOf(r, c)}
                        </span>
                      ) : (
                        <span key={c.index} title={c.label} style={{ display: "inline-flex", maxWidth: "100%" }}>
                          {cellOf(r, c)}
                        </span>
                      )
                    ))}
                  </div>
                )}
              </div>
            );
          })}
          {rows.length === 0 && <div style={{ ...MUTED, padding: 8 }}>No rows.</div>}
        </div>
      ) : (
        <>
          <div ref={scrollRef} style={{ overflowX: "auto", maxHeight: 420, overflowY: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  {shown.map((c) => (
                    <th key={c.index} onClick={() => setSort((s) =>
                      s && s.col === c.index ? { col: c.index, dir: s.dir === 1 ? -1 : 1 } : { col: c.index, dir: 1 })}
                      style={{ ...MUTED, textAlign: "left", padding: "4px 6px", cursor: "pointer",
                        borderBottom: "1px solid var(--border)", position: "sticky", top: 0, zIndex: 1,
                        background: "var(--card)", userSelect: "none", whiteSpace: "nowrap" }}>
                      {c.label}{sort && sort.col === c.index ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sorted.map((r, i) => (
                  <tr key={str(r.id) || i} style={{ borderBottom: "1px solid var(--border)" }}>
                    {shown.map((c) => {
                      const cell = arr(r.cells)[c.index];
                      const nowrap = c.role === "lead" || NOWRAP.has(c.kind) || isDateCell(cell);
                      return (
                        <td key={c.index} style={{ ...CELL, padding: "5px 6px", verticalAlign: "top",
                          ...(c.role === "primary" ? { minWidth: "10rem", overflowWrap: "anywhere" as const } : {}),
                          ...(nowrap ? { whiteSpace: "nowrap" as const } : {}) }}>
                          {c.role === "primary" ? titleOf(r) : cellOf(r, c)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
                {sorted.length === 0 && (
                  <tr><td colSpan={Math.max(1, shown.length)} style={{ ...MUTED, padding: 8 }}>No rows.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          {more && (
            <div data-grid-more="" style={{ ...MUTED, display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 4, marginTop: 4 }}>
              More columns to the right <TIcon name="arrow-right" size={12} color="var(--muted-foreground)" />
            </div>
          )}
        </>
      )}
      </TemplateBody>
    </div>
  );
}

function ReportCard({ data }: { data: Data }) {
  const stats = arr(data.stats);
  const tables = arr(data.tables).map((t) => (t ?? {}) as Data);
  // In a chat card the report is ONE card: its title row is the card's
  // header, and the tiles and the tables are its body (owner report,
  // 2026-10-13). Elsewhere it is the column it always was.
  const fold = useCardFold();
  return (
    <div style={fold ? CARD_BOX : undefined}>
      {/* The one address builder of Reports (`projects_reports.md` §6.5 item 18). */}
      <TemplateHead icon="file-text" gap={10} title={str(data.title, "Report")} href={reportLink()}
        aside={data.period != null ? str(data.period) : undefined} />
      <TemplateBody>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {/* WS-27bm S8: the saved report as a Markdown or PDF file. Drawn only
          when the card carries a real report id, and rendered again on the
          click, so the file is the Reports app's numbers of that moment. */}
      <ReportFileButtons reportId={data.reportId} />
      {stats.length > 0 && <StatDashboard data={{ stats }} />}
      {tables.map((t, i) => <DataGrid key={i} data={{ title: t.title, columns: t.columns, rows: t.rows, openBase: "" }} />)}
      </div>
      </TemplateBody>
    </div>
  );
}

/** The four short fields share one wrapping grid under the title. The rail
 *  beside the board is about 380px wide, and a five-column table there cut
 *  the title and the owner to a few letters (S7d visual review). */
const PLAN_GRID: React.CSSProperties = {
  display: "grid", gap: 6, marginTop: 6,
  gridTemplateColumns: "repeat(auto-fit, minmax(7.5rem, 1fr))",
};
const PLAN_WRAP: React.CSSProperties = { overflowWrap: "anywhere" };

/** A warning tint behind foreground text. Warning TEXT on a light card is too
 *  faint to read (S7c visual review), so the tint carries the tone. */
const MARK_BOX: React.CSSProperties = {
  ...CELL, fontSize: TYPE.caption, borderRadius: 6, padding: "3px 8px", marginTop: 2,
  background: "color-mix(in srgb, var(--warning) 14%, transparent)",
};

function PlanCard({ data, ctx }: { data: Data; ctx?: TemplateCtx }) {
  const project = (data.project ?? {}) as Data;
  const [name, setName] = useState(str(project.name));
  const [rows, setRows] = useState<PlanRow[]>(() => planRowsFrom(data.tasks));
  const [submitted, setSubmitted] = useState(false);
  const risks = arr(data.risks).map((r) => str(r)).filter(Boolean);
  const warnings = arr(data.warnings).map((w) => str(w)).filter(Boolean);
  const capacity = str(data.capacity);
  const setCell = (i: number, key: keyof PlanRow, v: unknown) =>
    setRows((p) => p.map((r, k) => (k === i ? { ...r, [key]: v } : r)));
  const drop = (i: number) => setRows((p) => p.filter((_, k) => k !== i));
  const add = () => setRows((p) => [...p, blankRow(p)]);
  const blocked = planIncomplete(name, rows);
  const submit = () => {
    if (!ctx?.onAction || submitted || blocked) return;
    setSubmitted(true);
    const label = str(data.submitLabel ?? data.title, "Plan");
    ctx.onAction(`${label} — ${JSON.stringify(planSubmit(project, name, rows))}`);
  };
  return (
    <div style={CARD_BOX}>
      <TemplateHead icon="list-todo" title={str(data.title, "Plan")} gap={4} />
      <TemplateBody>
      {data.description != null && <div style={{ ...MUTED, marginBottom: 10 }}><GenUiText text={str(data.description)} /></div>}
      <label style={{ ...MUTED, display: "block", marginBottom: 10 }}>
        Project name
        <input value={name} onChange={(e) => setName(e.target.value)} disabled={submitted}
          style={{ ...FIELD_INPUT_STYLE, marginTop: 4 }} />
      </label>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {rows.map((r, i) => {
          const after = afterLabel(r, rows);
          const options = afterOptions(r, rows);
          const facts = [
            after ? `After: ${after}` : "",
            r.fit != null ? `Fit: ${r.fit}` : "",
            r.hours != null ? `Hours: ${r.hours}` : "",
          ].filter(Boolean);
          const [titleCol, ...shortCols] = PLAN_EDIT_COLS;
          const field = (c: (typeof PLAN_EDIT_COLS)[number]) => (
            <label key={c.key} style={{ ...MUTED, display: "block", minWidth: 0 }}>
              {c.label}
              <input type={c.type} value={str(r[c.key])} disabled={submitted}
                onChange={(e) => setCell(i, c.key, c.type === "number" ? num(e.target.value) : e.target.value)}
                style={{ ...FIELD_INPUT_STYLE, padding: "5px 8px", marginTop: 2 }} />
            </label>
          );
          return (
            <div key={r.key} style={{ borderTop: "1px solid var(--border)", paddingTop: 8 }}>
              <div style={{ display: "flex", alignItems: "flex-end", gap: 6 }}>
                <div style={{ flex: 1, minWidth: 0 }}>{field(titleCol)}</div>
                {r.priority != null && (
                  <span title="Priority score, a sorting aid" style={{ ...MUTED, paddingBottom: 7,
                    fontVariantNumeric: "tabular-nums" }}>{str(r.priority)}</span>
                )}
                {!submitted && (
                  <Button variant="ghost" size="icon-xs" icon="X" onClick={() => drop(i)}
                    aria-label="Remove task" className="mb-1" />
                )}
              </div>
              <div style={PLAN_GRID}>{shortCols.map(field)}</div>
              {/* WS-27bm S10: what this row waits on is an input. The list
                  leaves out every row that waits on this one, so a tick
                  cannot make a cycle. The server still refuses one. */}
              {options.length === 0 && rows.length > 1 && (
                <div className="mt-1.5 text-[11px] text-muted-foreground" style={PLAN_WRAP}>
                  Waits on: none. Every other task waits on this one.
                </div>
              )}
              {options.length > 0 && (
                <CollapsibleSection label="Waits on" count={afterCount(r, rows)} defaultOpen={false}
                  ariaLabel={waitsOnName(r)} className="mt-1.5">
                  <div className="flex flex-col gap-1">
                    {options.map((o) => (
                      <label key={o.key} className="flex min-w-0 items-center gap-2 text-xs text-foreground">
                        <Checkbox size="sm" checked={r.after.includes(o.key)} disabled={submitted}
                          onChange={(e) => setCell(i, "after", withAfter(r, rows, o.key, e.target.checked))} />
                        <span style={PLAN_WRAP}>{o.title.trim() || "Untitled task"}</span>
                      </label>
                    ))}
                  </div>
                </CollapsibleSection>
              )}
              {facts.length > 0 && <div style={{ ...MUTED, ...PLAN_WRAP, marginTop: 4 }}>{facts.join(" · ")}</div>}
              {isMarked(r) && (
                <div style={{ ...MARK_BOX, ...PLAN_WRAP, display: "flex", alignItems: "flex-start", gap: 4 }}>
                  <Icon name="TriangleAlert" size={12} className="mt-px shrink-0" />
                  <span>{markLine(r)}</span>
                </div>
              )}
            </div>
          );
        })}
      </div>
      {!submitted && (
        <Button variant="ghost" size="sm" icon="Plus" onClick={add} className="mt-1.5">
          Add a task
        </Button>
      )}
      {capacity && <div style={{ ...MUTED, marginTop: 8 }}>{capacity}</div>}
      {warnings.length > 0 && (
        <div style={{ marginTop: 10, borderRadius: 10, padding: "8px 10px",
          background: "color-mix(in srgb, var(--warning) 12%, transparent)" }}>
          <div style={{ ...MUTED, fontWeight: 600, marginBottom: 4 }}>Order warnings</div>
          <ul className="ml-4 list-disc space-y-0.5 marker:text-muted-foreground" style={CELL}>
            {warnings.map((w, i) => <li key={i}><GenUiText text={w} /></li>)}
          </ul>
        </div>
      )}
      {risks.length > 0 && (
        <div style={{ marginTop: 10, borderRadius: 10, padding: "8px 10px",
          background: "color-mix(in srgb, var(--warning) 12%, transparent)" }}>
          <div style={{ ...MUTED, fontWeight: 600, marginBottom: 4 }}>How this plan fails</div>
          <ul className="ml-4 list-disc space-y-0.5 marker:text-muted-foreground" style={CELL}>
            {risks.map((r, i) => <li key={i}><GenUiText text={r} /></li>)}
          </ul>
        </div>
      )}
      {submitted ? (
        <div role="status" className="mt-3 flex items-center gap-2">
          <Badge tone="success" icon="Check">{PICKER_SENT}</Badge>
          <span style={MUTED}>A confirmation card follows.</span>
        </div>
      ) : (
        <Button size="sm" onClick={submit} disabled={!ctx?.onAction || !!blocked} className="mt-3">
          {str(data.submitLabel, "Review plan")}
        </Button>
      )}
      {!submitted && (
        <div style={{ ...MUTED, marginTop: 6 }}>
          {blocked ? `${blocked} ` : ""}
          A confirmation card follows. Nothing is created until you approve it. A mark warns and never blocks.
        </div>
      )}
      </TemplateBody>
    </div>
  );
}

// ── A report the agent makes (owner directive, 2026-10-16) ──────────────────
//
// `make_report` writes a report as data and its PDF beside it. ONE component
// draws the data: `ReportView`. The chat card draws its short form, and the
// side panel, the file viewer and `/artifacts` draw it whole. Every visual
// draws through the template that already draws its kind, so a report adds
// no second chart, table or board. `lib/report.ts` reads the data and maps
// each visual. A field it cannot read draws nothing, never a blank view.

/** The note in place of a visual that no template draws. */
export const REPORT_VISUAL_MISSING = "This visual cannot show here.";

/** One visual, by the template that draws its kind, or the note. */
/** The tallest chart in a report card. The full view keeps the chart's own height. */
export const REPORT_CARD_CHART_MAX = 240;

function ReportVisualBlock({ visual, minWidth, heading = "", card = false }: {
  visual: ReportVisual;
  minWidth?: string;
  /** The section's heading. A chart title that only repeats it draws once. */
  heading?: string;
  card?: boolean;
}) {
  const mapped = mapVisual(visual);
  if (!mapped) {
    return (
      <p role="note" data-report-visual-missing="" className="flex items-center gap-1.5 text-muted-foreground"
        style={{ fontSize: TYPE.caption }}>
        <Icon name="ImageOff" size={12} /> {REPORT_VISUAL_MISSING}
      </p>
    );
  }
  const drawn = (() => {
    switch (mapped.template) {
      case "chart": {
        // The chart's title draws as the report's own words, at the report's
        // size, never inside the picture: there it drew larger than the
        // heading above it. A title that repeats the heading draws once.
        const title = visual.title.trim();
        const caption = card ? title || heading.trim() : title !== heading.trim() ? title : "";
        return (
          <>
            {caption && <div data-report-chart-title="" className="mb-1 truncate text-xs font-medium text-foreground" title={caption}>{caption}</div>}
            <LiveChart spec={bareChartSpec(mapped.data)}
              maxHeight={card ? REPORT_CARD_CHART_MAX : undefined} />
          </>
        );
      }
      case "statDashboard":
        return <StatDashboard data={mapped.data} />;
      case "dataGrid":
        return <DataGrid data={mapped.data} />;
      case "taskBoard":
        return <TaskBoard data={mapped.data} />;
      case "timeline":
        return <Timeline data={mapped.data} />;
    }
  })();
  // In a narrow chat column a chart drew as a thumbnail. With a least
  // width it keeps a readable size, and the column scrolls it sideways.
  if (minWidth) {
    return (
      <div data-report-visual={visual.kind} data-template={mapped.template} className="min-w-0 overflow-x-auto">
        <div style={{ minWidth }}>{drawn}</div>
      </div>
    );
  }
  return <div data-report-visual={visual.kind} data-template={mapped.template} className="min-w-0">{drawn}</div>;
}

/** A refusal this button words itself, as against a network fault's words. */
class PdfMissing extends Error {}

/**
 * "Download PDF": the companion file, through the one save seam
 * (`lib/export.ts`). It fetches first, so a PDF that is not there says so in
 * words instead of a failed browser download. A hook, so the chat card puts
 * the button in its header row and the words of a failure in its body.
 */
function usePdfDownload(url: string | null, name: string) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run() {
    if (!url) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new PdfMissing(res.status === 404
        ? "The PDF is not there yet. Try again in a moment."
        : "The PDF could not be downloaded. Try again.");
      saveBlob(await res.blob(), name);
    } catch (e) {
      setError(e instanceof PdfMissing ? e.message : "The PDF could not be downloaded. Try again.");
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, run };
}

/** The words of a failed PDF download. */
function PdfError({ error }: { error: string | null }) {
  if (!error) return null;
  return <p role="alert" className="w-full text-destructive" style={{ fontSize: TYPE.caption }}>{error}</p>;
}

/** A small control in a dense card row: the Email cards' size (#945). */
const DENSE_ACT = "gap-0.5 px-1.5 py-0.5 text-[10px]";

/**
 * The chat card's two acts, in its header row. Each is an icon and a short
 * word. Where the card is narrow, the word gives way and the icon stays,
 * named for a screen reader.
 */
function ReportCardActions({ onOpen, pdf, pdfName }: {
  onOpen: (() => void) | null;
  pdf: ReturnType<typeof usePdfDownload> | null;
  pdfName: string;
}) {
  if (!onOpen && !pdf) return null;
  return (
    <>
      {onOpen && (
        <Button variant="secondary" size="none" className={DENSE_ACT} onClick={onOpen}
          aria-label="Open report" title="Open the report in full">
          <Icon name="PanelRight" size={10} /><span className="hidden @[17rem]:inline">Open</span>
        </Button>
      )}
      {pdf && (
        // Below 12rem the title needs the room. The PDF is then in the side
        // panel, which is what makes a chat column that narrow.
        <Button variant="secondary" size="none" className={DENSE_ACT} onClick={pdf.run} loading={pdf.busy}
          layout="hidden items-center justify-center @[12rem]:inline-flex"
          aria-label="Download PDF" title={`Download ${pdfName}`}>
          {!pdf.busy && <Icon name="FileDown" size={10} />}<span className="hidden @[17rem]:inline">PDF</span>
        </Button>
      )}
    </>
  );
}

/** Markdown-lite through the one chat renderer, with no raw HTML. */
function ReportText({ text }: { text: string }) {
  return (
    <div data-report-text="" className="min-w-0 leading-relaxed text-foreground" style={{ overflowWrap: "anywhere" }}>
      <MarkdownBody content={liteMarkdown(text)} fences />
    </div>
  );
}

function ReportSectionBlock({ section }: { section: Report["sections"][number] }) {
  return (
    <section data-report-section="" className="flex min-w-0 flex-col gap-2">
      {section.heading && <h3 className="text-sm font-semibold text-foreground">{section.heading}</h3>}
      {section.text && <ReportText text={section.text} />}
      {section.bullets.length > 0 && (
        <ul className="ml-5 list-outside list-disc space-y-1 text-foreground marker:text-muted-foreground">
          {section.bullets.map((b, i) => <li key={i} style={{ overflowWrap: "anywhere" }}><GenUiText text={b} /></li>)}
        </ul>
      )}
      {section.visual && <ReportVisualBlock visual={section.visual} heading={section.heading} />}
    </section>
  );
}

export interface ReportViewProps {
  report: Report;
  /** The companion PDF's address. Null or absent: no download. */
  pdfUrl?: string | null;
  /** `full` draws the whole report. `card` draws the chat card's short form,
   *  under the card's own header, which carries the title. */
  variant?: "full" | "card";
}

/**
 * THE report renderer. The chat card, the side panel, the file viewer and
 * `/artifacts` all draw a report through it, so the four cannot disagree.
 */
export function ReportView({ report, pdfUrl = null, variant = "full" }: ReportViewProps) {
  const card = variant === "card";
  // The card's header carries the title alone: in a narrow chat column an
  // aside there took the title's room. So the period is in the meta line.
  const meta = reportMeta(report, { period: true });
  const pdfName = report.pdf ? baseName(report.pdf) : "report.pdf";
  const pdf = usePdfDownload(pdfUrl, pdfName);
  // The full form's one act. The card's acts sit in its header row
  // (`ReportTemplate`), so the card form draws none here.
  const actions = !card && pdfUrl && (
    <div data-report-actions="" className="flex flex-wrap items-center gap-1.5">
      <Button variant="secondary" size="sm" icon="FileDown" loading={pdf.busy} onClick={pdf.run} title={`Download ${pdfName}`}>
        Download PDF
      </Button>
      <PdfError error={pdf.error} />
    </div>
  );
  // The KPI tiles are the stat template's tiles, in a grid by their count:
  // two or four to a row, never one alone at the end of a row.
  const tiles = report.kpis.length > 0 && (
    <div data-report-kpis="" className="@container min-w-0">
      <div className={kpiGridClass(report.kpis.length)}>
        {report.kpis.map((k, i) => <StatTile key={i} s={k as unknown as Data} dense={card} />)}
      </div>
      <style>{`@keyframes ccFadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}`}</style>
    </div>
  );

  if (card) {
    const leadSection = firstVisualSection(report);
    const lead = leadSection?.visual ?? null;
    const summary = clipWords(plainSummary(report.summary), REPORT_LIMITS.cardSummary);
    const heads = report.sections.map((s) => s.heading).filter(Boolean);
    return (
      <div data-report-view="card" className="flex min-w-0 flex-col gap-2 text-xs">
        {(report.subtitle || meta.length > 0) && (
          <div className="min-w-0 text-muted-foreground">
            {report.subtitle && <div className="truncate text-foreground" title={report.subtitle}>{report.subtitle}</div>}
            {meta.length > 0 && <div className="truncate" title={meta.join(" · ")}>{meta.join(" · ")}</div>}
          </div>
        )}
        {tiles}
        {summary && (
          <p data-report-summary="" className="line-clamp-3 text-foreground" title={summary} style={{ overflowWrap: "anywhere" }}>
            {summary}
          </p>
        )}
        {lead && <ReportVisualBlock visual={lead} minWidth="18rem" heading={leadSection?.heading ?? ""} card />}
        {heads.length > 0 && (
          <div data-report-contents="">
            <div className="mb-1 font-medium text-muted-foreground">In this report</div>
            <ol className="flex flex-col gap-0.5">
              {heads.map((h, i) => (
                <li key={i} className="flex min-w-0 gap-2">
                  <span className="shrink-0 tabular-nums text-muted-foreground">{i + 1}</span>
                  <span className="min-w-0 truncate text-foreground" title={h}>{h}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
    );
  }

  return (
    <article data-report-view="full" className="mx-auto flex w-full min-w-0 max-w-4xl flex-col gap-5 text-sm">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Icon name="FileChartColumn" size={18} className="shrink-0 text-primary" />
            <h2 className="min-w-0 text-base font-semibold text-foreground" style={{ overflowWrap: "anywhere" }}>
              {report.title}
            </h2>
          </div>
          {report.subtitle && <p className="mt-1 text-muted-foreground">{report.subtitle}</p>}
          {meta.length > 0 && <p className="mt-1 text-xs text-muted-foreground">{meta.join(" · ")}</p>}
        </div>
        {actions}
      </header>
      {tiles}
      {report.summary && (
        <section data-report-summary="" className="rounded-lg border border-border bg-muted/30 p-3">
          <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Summary</h3>
          <ReportText text={report.summary} />
        </section>
      )}
      {report.sections.map((s, i) => <ReportSectionBlock key={i} section={s} />)}
      {report.sources.length > 0 && (
        <footer data-report-sources="" className="border-t border-border pt-3 text-xs text-muted-foreground">
          <h3 className="mb-1 font-semibold">Sources</h3>
          <ul className="flex flex-col gap-0.5">
            {report.sources.map((s, i) => <li key={i} style={{ overflowWrap: "anywhere" }}>{s}</li>)}
          </ul>
        </footer>
      )}
    </article>
  );
}

/**
 * The `report` template: the chat card of a report. Its title row is the
 * card's one header, and its body is `ReportView`'s short form. "Open
 * report" and the PDF come from the chat that hosts the card
 * (`lib/genUiFiles.ts`), because the card holds a path and no session.
 */
function ReportTemplate({ data }: { data: Data }) {
  const report = reportFrom(data);
  const { open, pdfUrl } = reportCardActions(report, useContext(GenUiFileContext));
  const pdfName = report.pdf ? baseName(report.pdf) : "report.pdf";
  const pdf = usePdfDownload(pdfUrl, pdfName);
  // The card is its own size container: its acts drop their words, and its
  // tiles set their row, by the card's width and not the window's.
  return (
    <div data-report-card="" className="@container min-w-0" style={CARD_BOX}>
      <TemplateHead icon="file-chart-column" gap={10} title={report.title}
        actions={open || pdfUrl ? <ReportCardActions onOpen={open} pdf={pdfUrl ? pdf : null} pdfName={pdfName} /> : undefined} />
      <TemplateBody>
        <PdfError error={pdf.error} />
        <ReportView report={report} variant="card" />
      </TemplateBody>
    </div>
  );
}

/**
 * A chart template. In a chat card its title is the card's one header, so
 * the chart draws no title of its own: it drew it in the picture, under the
 * fold's header, and the title read twice. Anywhere else the chart draws as
 * before, its title in the picture (the WhatsApp bot draws the same one).
 */
function ChartCard({ spec }: { spec: Data }) {
  const fold = useCardFold();
  if (!fold) return <LiveChart spec={spec} />;
  const title = str(spec.title).trim();
  const subtitle = str(spec.subtitle).trim();
  const bare: Data = { ...spec, title: undefined, subtitle: undefined };
  return (
    <div style={CARD_BOX}>
      <TemplateHead icon="chart-column" title={title || null} aside={subtitle || undefined} />
      <TemplateBody>
        <LiveChart spec={bare} bare />
      </TemplateBody>
    </div>
  );
}

// ── Registry ─────────────────────────────────────────────────────────────────

/** Interaction context threaded from the transcript/panel host: `onAction`
 *  sends a message back to the agent — via the blocking HITL resume when the
 *  spec carried a request_id, else as the user's next chat message. */
export interface TemplateCtx {
  onAction?: (message: string) => void;
  /** The answer a blocking card already got (`lib/askAnswers.ts`). A card
   *  that has one draws as sent and takes no click, after any remount. */
  answered?: string;
}

export type TemplateRenderer = (data: Data, ctx?: TemplateCtx) => React.ReactElement;

export const TEMPLATE_REGISTRY: Record<string, TemplateRenderer> = {
  weatherCard: (data) => <WeatherCard data={data} />,
  statDashboard: (data) => <StatDashboard data={data} />,
  barChart: (data) => <ChartCard spec={barChartSpec(data)} />,
  sparkTrend: (data) => <SparkTrend data={data} />,
  comparison: (data) => <Comparison data={data} />,
  progressTracker: (data) => <ProgressTracker data={data} />,
  recipeCard: (data) => <RecipeCard data={data} />,
  flightStatus: (data) => <FlightStatus data={data} />,
  trainStatus: (data) => <TrainStatus data={data} />,
  formCard: (data, ctx) => <FormCard data={data} ctx={ctx} />,
  optionPicker: (data, ctx) => <OptionPicker data={data} ctx={ctx} />,
  timeline: (data) => <Timeline data={data} />,
  taskBoard: (data) => <TaskBoard data={data} />,
  dataGrid: (data) => <DataGrid data={data} />,
  reportCard: (data) => <ReportCard data={data} />,
  planCard: (data, ctx) => <PlanCard data={data} ctx={ctx} />,
  chart: (data) => <ChartCard spec={data} />,
  report: (data) => <ReportTemplate data={data} />,
};

/** Render a template node, or an inert fallback if the name is unknown. */
export function renderTemplate(
  name: string, data: unknown, ctx?: TemplateCtx,
): React.ReactElement {
  const renderer = TEMPLATE_REGISTRY[name];
  const safeData = (data && typeof data === "object" ? data : {}) as Data;
  if (!renderer) {
    // Not "unknown template: X". The member sees the words the data holds,
    // in the neutral fallback (`GenUIFallback.tsx`), or nothing.
    return <GenUIFallback node={{ type: name, props: safeData }} />;
  }
  return renderer(safeData, ctx);
}
