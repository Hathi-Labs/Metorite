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
 * Unknown names fall back to an inert card (same safety posture as Tier 1).
 *
 * SOURCE OF TRUTH: TEMPLATE_CATALOG below lists every template + its data shape.
 * The backend emit_generative_ui docstring MUST mirror this list, or agents will
 * emit names the renderer drops. Keep them in lockstep.
 *
 * All animation is CSS/SVG only (no new bundle deps). Every template is
 * theme-agnostic: it styles via the app's CSS custom-property tokens (full
 * colors — see theme-css-vars memory) so it reads correctly in light and dark.
 */

import { createElement, useEffect, useState } from "react";

import { resolveIcon } from "@/lib/icons";

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
  /** Human-readable data shape, mirrored into the backend tool docstring. */
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
    summary: "Schema-driven form (text/number/select/slider/toggle/date/textarea) that submits values back — pair with hitl.",
    data: "{ title?, description?, submitLabel?, fields:[{ name, label, type('text'|'number'|'select'|'slider'|'toggle'|'date'|'textarea'), placeholder?, value?, required?, options?:[string], min?, max?, step?, unit? }] }",
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
    data: "{ title?, total?, columns:[{ id?, name, category?, tasks:[{ id, number?, title, assignees?:[string], due?, importance?, done?:bool }] }] }",
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
    summary: "An editable project plan (title, owner, effort, due per task, with a priority score) that submits the edited rows back — pair with hitl.",
    data: "{ title?, description?, submitLabel?, project:{ name, parent?, description? }, tasks:[{ title, owner, effort_mins, due, importance?, priority? }], risks?:[string] }",
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
  primary: "var(--primary)",
  success: "var(--success)",
  warning: "var(--warning)",
  danger: "var(--destructive)",
};

function DeltaArrow({ delta }: { delta: number }): React.ReactElement | null {
  if (!delta) return <span style={{ color: "var(--muted-foreground)" }}>—</span>;
  const up = delta > 0;
  return (
    <span style={{ color: up ? "var(--success)" : "var(--destructive)", fontVariantNumeric: "tabular-nums" }}>
      {up ? "▲" : "▼"} {Math.abs(delta)}
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
  const location = str(data.location, "Weather");
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
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <WeatherGlyph condition={condition} />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, color: "var(--muted-foreground)" }}>{location}</div>
          <div style={{ fontSize: 34, fontWeight: 600, color: "var(--foreground)", lineHeight: 1.1 }}>
            {Math.round(animated)}{unit}
          </div>
          <div style={{ fontSize: 12, color: "var(--muted-foreground)", textTransform: "capitalize" }}>
            {condition}
          </div>
        </div>
        <div style={{ textAlign: "right", fontSize: 11, color: "var(--muted-foreground)", lineHeight: 1.6 }}>
          {data.highC != null && <div>H {num(data.highC)}°</div>}
          {data.lowC != null && <div>L {num(data.lowC)}°</div>}
          {data.humidity != null && <div>💧 {num(data.humidity)}%</div>}
          {data.wind != null && <div>🌬 {str(data.wind)}</div>}
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
                <div style={{ fontSize: 10, color: "var(--muted-foreground)" }}>{str(day.day)}</div>
                <WeatherGlyph condition={str(day.condition, condition)} size={26} />
                <div style={{ fontSize: 11, color: "var(--foreground)" }}>
                  {num(day.high)}°<span style={{ color: "var(--muted-foreground)" }}> {num(day.low)}°</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
      <style>{`@keyframes ccFadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}`}</style>
    </div>
  );
}

function StatTile({ s }: { s: Data }) {
  const value = num(s.value);
  const animated = useCountUp(value);
  const isNumeric = typeof s.value === "number" || /^-?\d/.test(str(s.value));
  return (
    <div style={{
      flex: 1, minWidth: 110, padding: 12, borderRadius: 12,
      border: "1px solid var(--border)", background: "var(--card)",
      animation: "ccFadeUp .4s ease both",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: "var(--muted-foreground)" }}>
        <TIcon name={s.icon} size={13} />
        <span>{str(s.label)}</span>
      </div>
      <div style={{ fontSize: 24, fontWeight: 600, color: "var(--foreground)", fontVariantNumeric: "tabular-nums" }}>
        {isNumeric ? Math.round(animated).toLocaleString() : str(s.value)}
        {s.unit != null && <span style={{ fontSize: 13, color: "var(--muted-foreground)" }}> {str(s.unit)}</span>}
      </div>
      {s.delta != null && (
        <div style={{ fontSize: 11, marginTop: 2 }}><DeltaArrow delta={num(s.delta)} /></div>
      )}
    </div>
  );
}

function StatDashboard({ data }: { data: Data }) {
  const stats = arr(data.stats);
  return (
    <div>
      {data.title != null && (
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--foreground)", marginBottom: 8 }}>
          {str(data.title)}
        </div>
      )}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {stats.map((s, i) => <StatTile key={i} s={(s ?? {}) as Data} />)}
      </div>
      <style>{`@keyframes ccFadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}`}</style>
    </div>
  );
}

function BarChart({ data }: { data: Data }) {
  const bars = arr(data.bars).map((b) => (b ?? {}) as Data);
  const max = Math.max(1, ...bars.map((b) => num(b.value)));
  return (
    <div style={{ padding: 14, borderRadius: 12, border: "1px solid var(--border)", background: "var(--card)" }}>
      {data.title != null && (
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--foreground)", marginBottom: 10 }}>
          {str(data.title)}
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {bars.map((b, i) => {
          const pct = (num(b.value) / max) * 100;
          const color = TONE_COLOR[str(b.tone, "primary")] ?? TONE_COLOR.primary;
          return (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{ width: 90, fontSize: 11, color: "var(--muted-foreground)", textAlign: "right",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {str(b.label)}
              </div>
              <div style={{ flex: 1, height: 18, borderRadius: 6, background: "var(--secondary)", overflow: "hidden" }}>
                <div style={{
                  height: "100%", width: `${pct}%`, background: color, borderRadius: 6,
                  animation: `ccGrow .7s cubic-bezier(.22,1,.36,1) ${i * 0.06}s both`,
                }} />
              </div>
              <div style={{ width: 52, fontSize: 11, color: "var(--foreground)", fontVariantNumeric: "tabular-nums" }}>
                {num(b.value).toLocaleString()}{data.unit != null ? str(data.unit) : ""}
              </div>
            </div>
          );
        })}
      </div>
      <style>{`@keyframes ccGrow{from{width:0}}`}</style>
    </div>
  );
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
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 14, padding: 14, borderRadius: 12,
      border: "1px solid var(--border)", background: "var(--card)" }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 11, color: "var(--muted-foreground)" }}>{str(data.label)}</div>
        <div style={{ fontSize: 24, fontWeight: 600, color: "var(--foreground)", fontVariantNumeric: "tabular-nums" }}>
          {Math.round(animated).toLocaleString()}
          {data.unit != null && <span style={{ fontSize: 13, color: "var(--muted-foreground)" }}> {str(data.unit)}</span>}
        </div>
        {data.delta != null && <div style={{ fontSize: 11 }}><DeltaArrow delta={num(data.delta)} /></div>}
      </div>
      <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
        <polyline points={pts} fill="none" stroke="var(--primary)" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round"
          style={{ strokeDasharray: 400, strokeDashoffset: 400, animation: "ccDraw 1s ease forwards" }} />
      </svg>
      <style>{`@keyframes ccDraw{to{stroke-dashoffset:0}}`}</style>
    </div>
  );
}

function Comparison({ data }: { data: Data }) {
  const options = arr(data.options).map((o) => (o ?? {}) as Data);
  const rowLabels = Array.from(new Set(
    options.flatMap((o) => arr(o.rows).map((r) => str((r as Data).label))),
  ));
  return (
    <div style={{ borderRadius: 12, border: "1px solid var(--border)", overflow: "hidden" }}>
      {data.title != null && (
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--foreground)", padding: "10px 12px",
          borderBottom: "1px solid var(--border)", background: "var(--card)" }}>{str(data.title)}</div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: `140px repeat(${options.length}, 1fr)` }}>
        <div style={{ background: "var(--card)" }} />
        {options.map((o, i) => (
          <div key={i} style={{
            padding: "8px 10px", fontSize: 12, fontWeight: 600, textAlign: "center",
            color: o.recommended ? "var(--success)" : "var(--foreground)",
            background: o.recommended ? "color-mix(in srgb, var(--success) 12%, var(--card))" : "var(--card)",
            borderLeft: "1px solid var(--border)",
          }}>
            {str(o.name)}{o.recommended ? " ★" : ""}
          </div>
        ))}
        {rowLabels.map((label, ri) => (
          <div key={label} style={{ display: "contents" }}>
            <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--muted-foreground)",
              borderTop: "1px solid var(--border)", background: ri % 2 ? "var(--secondary)" : "transparent" }}>
              {label}
            </div>
            {options.map((o, ci) => {
              const row = arr(o.rows).map((r) => (r ?? {}) as Data).find((r) => str(r.label) === label);
              return (
                <div key={ci} style={{ padding: "8px 10px", fontSize: 12, textAlign: "center",
                  color: "var(--foreground)", borderTop: "1px solid var(--border)", borderLeft: "1px solid var(--border)",
                  background: o.recommended ? "color-mix(in srgb, var(--success) 6%, transparent)" : (ri % 2 ? "var(--secondary)" : "transparent") }}>
                  {row ? str(row.value) : "—"}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

function ProgressTracker({ data }: { data: Data }) {
  const steps = arr(data.steps).map((s) => (s ?? {}) as Data);
  const done = steps.filter((s) => str(s.state) === "done").length;
  const pct = steps.length ? (done / steps.length) * 100 : 0;
  return (
    <div style={{ padding: 14, borderRadius: 12, border: "1px solid var(--border)", background: "var(--card)" }}>
      {data.title != null && (
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--foreground)", marginBottom: 10 }}>{str(data.title)}</div>
      )}
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
                fontSize: 9, color: "var(--success-foreground)" }}>
                {state === "done" ? "✓" : ""}
              </span>
              <span style={{ fontSize: 12,
                color: state === "pending" ? "var(--muted-foreground)" : "var(--foreground)",
                fontWeight: state === "active" ? 600 : 400 }}>
                {str(s.label)}
              </span>
            </div>
          );
        })}
      </div>
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
  return (
    <div style={{ borderRadius: 14, border: "1px solid var(--border)", background: "var(--card)", overflow: "hidden" }}>
      <div style={{
        padding: "14px 16px", borderBottom: "1px solid var(--border)",
        background: "linear-gradient(135deg, color-mix(in srgb, var(--accent) 10%, var(--card)), var(--card))",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <TIcon name="utensils" size={16} color="var(--accent)" />
          <span style={{ fontSize: 15, fontWeight: 600, color: "var(--foreground)" }}>{str(data.title, "Recipe")}</span>
        </div>
        {data.description != null && (
          <div style={{ fontSize: 12, color: "var(--muted-foreground)", marginTop: 4 }}>{str(data.description)}</div>
        )}
        {meta.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 8 }}>
            {meta.map(([icon, label]) => (
              <span key={label} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11,
                color: "var(--muted-foreground)", background: "var(--secondary)", borderRadius: 999, padding: "3px 9px" }}>
                <TIcon name={icon} size={12} /> {label}
              </span>
            ))}
          </div>
        )}
      </div>
      <div style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 12 }}>
        {ingredients.length > 0 && (
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em",
              color: "var(--muted-foreground)", marginBottom: 6 }}>Ingredients</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 4 }}>
              {ingredients.map((ing, i) => (
                <div key={i} style={{ display: "flex", alignItems: "baseline", gap: 6, fontSize: 12,
                  color: "var(--foreground)", animation: `ccFadeUp .3s ease ${i * 0.03}s both` }}>
                  <TIcon name="check" size={11} color="var(--success)" />
                  <span>{str(ing.item)}</span>
                  {ing.amount != null && <span style={{ color: "var(--muted-foreground)", fontSize: 11 }}>{str(ing.amount)}</span>}
                </div>
              ))}
            </div>
          </div>
        )}
        {steps.length > 0 && (
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em",
              color: "var(--muted-foreground)", marginBottom: 6 }}>Steps</div>
            <ol style={{ display: "flex", flexDirection: "column", gap: 8, margin: 0, padding: 0, listStyle: "none" }}>
              {steps.map((s, i) => (
                <li key={i} style={{ display: "flex", gap: 10, fontSize: 12.5, color: "var(--foreground)",
                  animation: `ccFadeUp .3s ease ${i * 0.05}s both` }}>
                  <span style={{ flexShrink: 0, width: 20, height: 20, borderRadius: "50%", display: "flex",
                    alignItems: "center", justifyContent: "center", fontSize: 10, fontWeight: 600,
                    background: "color-mix(in srgb, var(--primary) 15%, transparent)",
                    color: "var(--primary)" }}>{i + 1}</span>
                  <span style={{ lineHeight: 1.55 }}>{s}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
        {data.tip != null && (
          <div style={{ display: "flex", gap: 8, fontSize: 12, color: "var(--foreground)", padding: "8px 10px",
            borderRadius: 10, background: "color-mix(in srgb, var(--accent) 10%, transparent)" }}>
            <TIcon name="lightbulb" size={14} color="var(--accent)" />
            <span>{str(data.tip)}</span>
          </div>
        )}
        {tags.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {tags.map((t) => (
              <span key={t} style={{ fontSize: 10, color: "var(--muted-foreground)",
                border: "1px solid var(--border)", borderRadius: 999, padding: "2px 8px" }}>{t}</span>
            ))}
          </div>
        )}
      </div>
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
    <span style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em",
      color: tone.color, background: tone.bg, borderRadius: 999, padding: "3px 10px" }}>
      {status}
    </span>
  );
}

function EndpointBlock({ e, align }: { e: Data; align: "left" | "right" }) {
  return (
    <div style={{ textAlign: align, minWidth: 72 }}>
      <div style={{ fontSize: 22, fontWeight: 700, color: "var(--foreground)", letterSpacing: "0.02em" }}>
        {str(e.code ?? e.station, "—")}
      </div>
      {e.city != null && <div style={{ fontSize: 11, color: "var(--muted-foreground)" }}>{str(e.city)}</div>}
      {e.time != null && <div style={{ fontSize: 12, color: "var(--foreground)", fontVariantNumeric: "tabular-nums" }}>{str(e.time)}</div>}
      <div style={{ fontSize: 10, color: "var(--muted-foreground)" }}>
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
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <TIcon name="plane" size={16} color="var(--primary)" />
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--foreground)" }}>
            {data.airline != null ? `${str(data.airline)} ` : ""}{str(data.flightNo)}
          </span>
          {data.date != null && <span style={{ fontSize: 11, color: "var(--muted-foreground)" }}>{str(data.date)}</span>}
        </div>
        <StatusBadge status={status} />
      </div>
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
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 10, fontSize: 11,
          color: "var(--muted-foreground)" }}>
          <span>{data.durationMin != null ? `${Math.floor(num(data.durationMin) / 60)}h ${num(data.durationMin) % 60}m` : ""}</span>
          <span>{data.note != null ? str(data.note) : ""}</span>
        </div>
      )}
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
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <TIcon name="train-front" size={16} color="var(--primary)" />
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--foreground)" }}>
            {[str(data.operator), str(data.trainNo), data.line != null ? `· ${str(data.line)}` : ""].filter(Boolean).join(" ")}
          </span>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {delay > 0 && (
            <span style={{ fontSize: 10.5, fontWeight: 600, color: "var(--warning)",
              background: "color-mix(in srgb, var(--warning) 14%, transparent)",
              borderRadius: 999, padding: "3px 10px" }}>+{delay} min</span>
          )}
          <StatusBadge status={status} />
        </div>
      </div>
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
              <div key={i} style={{ position: "relative", padding: "5px 0", fontSize: 12,
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
        <div style={{ marginTop: 8, fontSize: 11, color: "var(--muted-foreground)" }}>{str(data.note)}</div>
      )}
      <style>{`@keyframes ccFadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}`}</style>
    </div>
  );
}

const FIELD_INPUT_STYLE: React.CSSProperties = {
  width: "100%", fontSize: 12.5, color: "var(--foreground)", background: "var(--secondary)",
  border: "1px solid var(--border)", borderRadius: 8, padding: "7px 10px", outline: "none",
};

function FormCard({ data, ctx }: { data: Data; ctx?: TemplateCtx }) {
  const fields = arr(data.fields).map((f) => (f ?? {}) as Data);
  const [values, setValues] = useState<Record<string, unknown>>(() => {
    const init: Record<string, unknown> = {};
    for (const f of fields) {
      const name = str(f.name);
      if (!name) continue;
      init[name] = f.value ?? (str(f.type) === "toggle" ? false
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
      {data.title != null && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <TIcon name="clipboard-list" size={15} color="var(--primary)" />
          <span style={{ fontSize: 13.5, fontWeight: 600, color: "var(--foreground)" }}>{str(data.title)}</span>
        </div>
      )}
      {data.description != null && (
        <div style={{ fontSize: 12, color: "var(--muted-foreground)", marginBottom: 10 }}>{str(data.description)}</div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {fields.map((f, i) => {
          const name = str(f.name);
          const type = str(f.type, "text");
          const v = values[name];
          return (
            <label key={name || i} style={{ display: "flex", flexDirection: "column", gap: 4,
              animation: `ccFadeUp .3s ease ${i * 0.04}s both` }}>
              <span style={{ fontSize: 11.5, color: "var(--muted-foreground)" }}>
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
      <button type="button" onClick={submit} disabled={!ctx?.onAction || submitted || missing}
        style={{ marginTop: 12, fontSize: 12.5, fontWeight: 600, borderRadius: 8, padding: "8px 16px",
          border: "none", cursor: submitted || missing ? "default" : "pointer",
          color: "var(--primary-foreground)",
          background: submitted ? "var(--success)" : "var(--primary)",
          opacity: !ctx?.onAction || missing ? 0.5 : 1, transition: "all 0.2s var(--cc-ease, ease)" }}>
        {submitted ? "✓ Submitted" : str(data.submitLabel, "Submit")}
      </button>
      <style>{`@keyframes ccFadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}`}</style>
    </div>
  );
}

function OptionPicker({ data, ctx }: { data: Data; ctx?: TemplateCtx }) {
  const options = arr(data.options).map((o) => (o ?? {}) as Data);
  const multi = !!data.multi;
  const [picked, setPicked] = useState<string[]>([]);
  const [submitted, setSubmitted] = useState(false);
  const submit = (ids: string[]) => {
    if (!ctx?.onAction || submitted || !ids.length) return;
    setSubmitted(true);
    const labels = ids.map((id) =>
      str(options.find((o) => str(o.id) === id)?.label, id));
    ctx.onAction(`Selected: ${labels.join(", ")}`);
  };
  const toggle = (id: string) => {
    if (submitted) return;
    if (!multi) {
      setPicked([id]);
      submit([id]);
      return;
    }
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  };
  return (
    <div style={{ borderRadius: 14, border: "1px solid var(--border)", background: "var(--card)", padding: 16 }}>
      {data.title != null && (
        <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--foreground)", marginBottom: 4 }}>{str(data.title)}</div>
      )}
      {data.description != null && (
        <div style={{ fontSize: 12, color: "var(--muted-foreground)", marginBottom: 10 }}>{str(data.description)}</div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 8 }}>
        {options.map((o, i) => {
          const id = str(o.id, str(o.label, String(i)));
          const active = picked.includes(id);
          return (
            <button key={id} type="button" onClick={() => toggle(id)} disabled={submitted}
              style={{ textAlign: "left", borderRadius: 12, padding: "10px 12px", cursor: submitted ? "default" : "pointer",
                border: `1px solid ${active ? "var(--primary)" : "var(--border)"}`,
                background: active ? "color-mix(in srgb, var(--primary) 10%, var(--card))" : "var(--card)",
                boxShadow: active ? "0 0 0 3px color-mix(in srgb, var(--primary) 15%, transparent)" : "none",
                transition: "all 0.15s var(--cc-ease, ease)", animation: `ccFadeUp .3s ease ${i * 0.05}s both`,
                opacity: submitted && !active ? 0.5 : 1 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <TIcon name={o.icon} size={14} color="var(--primary)" />
                <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--foreground)" }}>{str(o.label)}</span>
                {o.recommended ? <span style={{ color: "var(--accent)", fontSize: 11 }}>★</span> : null}
              </div>
              {o.description != null && (
                <div style={{ fontSize: 11, color: "var(--muted-foreground)", marginTop: 3, lineHeight: 1.45 }}>
                  {str(o.description)}
                </div>
              )}
              {o.badge != null && (
                <span style={{ display: "inline-block", marginTop: 5, fontSize: 9.5, fontWeight: 600,
                  color: "var(--primary)", background: "color-mix(in srgb, var(--primary) 12%, transparent)",
                  borderRadius: 999, padding: "2px 7px" }}>{str(o.badge)}</span>
              )}
            </button>
          );
        })}
      </div>
      {multi && (
        <button type="button" onClick={() => submit(picked)}
          disabled={!ctx?.onAction || submitted || !picked.length}
          style={{ marginTop: 12, fontSize: 12.5, fontWeight: 600, borderRadius: 8, padding: "8px 16px",
            border: "none", cursor: "pointer", color: "var(--primary-foreground)",
            background: submitted ? "var(--success)" : "var(--primary)",
            opacity: !ctx?.onAction || !picked.length ? 0.5 : 1 }}>
          {submitted ? "✓ Submitted" : "Confirm selection"}
        </button>
      )}
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

const CELL: React.CSSProperties = { fontSize: 12, color: "var(--foreground)" };
const MUTED: React.CSSProperties = { fontSize: 11, color: "var(--muted-foreground)" };
const CARD_BOX: React.CSSProperties = {
  borderRadius: 14, border: "1px solid var(--border)", background: "var(--card)", padding: 14,
};

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

function Timeline({ data }: { data: Data }) {
  const rows = arr(data.rows).map((r) => (r ?? {}) as Data);
  const tone = (type: string): string =>
    type === "comment" ? "var(--primary)"
      : type === "field_change" ? "var(--warning)"
        : type === "merge" || type === "attachment" ? "var(--accent)"
          : "var(--border)";
  return (
    <div style={CARD_BOX}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <TIcon name="history" size={15} color="var(--primary)" />
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--foreground)" }}>
            <TitleLink href={taskHref(data.taskId)}>{str(data.title, "Timeline")}</TitleLink>
          </span>
        </div>
        {data.total != null && <span style={MUTED}>{rows.length} of {num(data.total)}</span>}
      </div>
      {rows.length === 0 && <div style={MUTED}>Nothing has happened here yet.</div>}
      <div style={{ borderLeft: "2px solid var(--secondary)", marginLeft: 6, paddingLeft: 14,
        maxHeight: 420, overflowY: "auto" }}>
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
                  {str(r.at).slice(0, 16).replace("T", " ")}
                </span>
              </div>
              {r.body != null && str(r.body) !== "" && (
                <div style={{ ...CELL, whiteSpace: "pre-wrap", marginTop: 2 }}>{str(r.body)}</div>
              )}
              {r.field != null && (
                <div style={{ ...CELL, marginTop: 2 }}>
                  <span style={MUTED}>{str(r.field)}: </span>
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
      <style>{`@keyframes ccFadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}`}</style>
    </div>
  );
}

function TaskChip({ t }: { t: Data }) {
  const href = taskHref(t.id);
  const people = arr(t.assignees).map((a) => str(a)).filter(Boolean);
  const body = (
    <div style={{ borderRadius: 10, border: "1px solid var(--border)", background: "var(--secondary)",
      padding: "6px 8px", marginBottom: 6, opacity: t.done ? 0.6 : 1 }}>
      <div style={{ ...CELL, display: "flex", gap: 6 }}>
        {t.number != null && <span style={MUTED}>#{str(t.number)}</span>}
        <span style={{ textDecoration: t.done ? "line-through" : "none", flex: 1 }}>{str(t.title)}</span>
        {num(t.importance) > 0 && (
          <span style={{ ...MUTED, fontVariantNumeric: "tabular-nums" }} title="importance">
            !{num(t.importance)}
          </span>
        )}
      </div>
      {(people.length > 0 || t.due != null) && (
        <div style={{ ...MUTED, display: "flex", gap: 8, marginTop: 2, flexWrap: "wrap" }}>
          {people.length > 0 && <span>{people.join(", ")}</span>}
          {t.due != null && str(t.due) !== "" && <span>due {str(t.due)}</span>}
        </div>
      )}
    </div>
  );
  return href ? <a href={href} style={{ textDecoration: "none", display: "block" }}>{body}</a> : body;
}

function TaskBoard({ data }: { data: Data }) {
  const columns = arr(data.columns).map((c) => (c ?? {}) as Data);
  return (
    <div style={CARD_BOX}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <TIcon name="kanban" size={15} color="var(--primary)" />
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--foreground)" }}>{str(data.title, "Board")}</span>
        </div>
        {data.total != null && <span style={MUTED}>{num(data.total)} tasks</span>}
      </div>
      <div style={{ display: "flex", gap: 10, overflowX: "auto", paddingBottom: 4 }}>
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
    </div>
  );
}

function DataGrid({ data }: { data: Data }) {
  const columns = arr(data.columns).map((c) => str(c));
  const rows = arr(data.rows).map((r) => (r ?? {}) as Data);
  const base = str(data.openBase, "/projects?task=");
  const [sort, setSort] = useState<{ col: number; dir: 1 | -1 } | null>(null);
  const sorted = sort
    ? [...rows].sort((a, b) => {
      const av = arr(a.cells)[sort.col];
      const bv = arr(b.cells)[sort.col];
      const an = typeof av === "number" ? av : parseFloat(str(av));
      const bn = typeof bv === "number" ? bv : parseFloat(str(bv));
      if (Number.isFinite(an) && Number.isFinite(bn)) return (an - bn) * sort.dir;
      return str(av).localeCompare(str(bv)) * sort.dir;
    })
    : rows;
  return (
    <div style={CARD_BOX}>
      {data.title != null && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <TIcon name="table-2" size={15} color="var(--primary)" />
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--foreground)" }}>{str(data.title)}</span>
        </div>
      )}
      <div style={{ overflowX: "auto", maxHeight: 420, overflowY: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              {columns.map((c, i) => (
                <th key={i} onClick={() => setSort((s) =>
                  s && s.col === i ? { col: i, dir: s.dir === 1 ? -1 : 1 } : { col: i, dir: 1 })}
                  style={{ ...MUTED, textAlign: "left", padding: "4px 6px", cursor: "pointer",
                    borderBottom: "1px solid var(--border)", position: "sticky", top: 0,
                    background: "var(--card)", userSelect: "none", whiteSpace: "nowrap" }}>
                  {c}{sort && sort.col === i ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => {
              const href = taskHref(r.id, base);
              return (
                <tr key={str(r.id) || i} style={{ borderBottom: "1px solid var(--border)" }}>
                  {arr(r.cells).map((cell, k) => (
                    <td key={k} style={{ ...CELL, padding: "5px 6px", verticalAlign: "top" }}>
                      {k === 1 && href
                        ? <a href={href} style={{ color: "var(--primary)", textDecoration: "none" }}>{str(cell)}</a>
                        : str(cell)}
                    </td>
                  ))}
                </tr>
              );
            })}
            {sorted.length === 0 && (
              <tr><td colSpan={Math.max(1, columns.length)} style={{ ...MUTED, padding: 8 }}>No rows.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ReportCard({ data }: { data: Data }) {
  const stats = arr(data.stats);
  const tables = arr(data.tables).map((t) => (t ?? {}) as Data);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <TIcon name="file-text" size={15} color="var(--primary)" />
        <span style={{ fontSize: 14, fontWeight: 600, color: "var(--foreground)" }}>
          <TitleLink href="/projects?app=reports">{str(data.title, "Report")}</TitleLink>
        </span>
        {data.period != null && <span style={MUTED}>{str(data.period)}</span>}
      </div>
      {stats.length > 0 && <StatDashboard data={{ stats }} />}
      {tables.map((t, i) => <DataGrid key={i} data={{ title: t.title, columns: t.columns, rows: t.rows, openBase: "" }} />)}
    </div>
  );
}

const PLAN_COLS: Array<{ key: string; label: string; type: "text" | "number" | "date"; width: string }> = [
  { key: "title", label: "Task", type: "text", width: "38%" },
  { key: "owner", label: "Owner", type: "text", width: "22%" },
  { key: "effort_mins", label: "Effort (min)", type: "number", width: "14%" },
  { key: "due", label: "Due", type: "date", width: "18%" },
];

function PlanCard({ data, ctx }: { data: Data; ctx?: TemplateCtx }) {
  const project = (data.project ?? {}) as Data;
  const [name, setName] = useState(str(project.name));
  const [rows, setRows] = useState<Data[]>(() => arr(data.tasks).map((t) => ({ ...((t ?? {}) as Data) })));
  const [submitted, setSubmitted] = useState(false);
  const risks = arr(data.risks).map((r) => str(r)).filter(Boolean);
  const setCell = (i: number, key: string, v: unknown) =>
    setRows((p) => p.map((r, k) => (k === i ? { ...r, [key]: v } : r)));
  const drop = (i: number) => setRows((p) => p.filter((_, k) => k !== i));
  const add = () => setRows((p) => [...p, { title: "", owner: "", effort_mins: 60, due: "" }]);
  const incomplete = !name.trim() || rows.length === 0 || rows.some((r) =>
    !str(r.title).trim() || !str(r.owner).trim() || !str(r.due).trim() || !(num(r.effort_mins) > 0));
  const submit = () => {
    if (!ctx?.onAction || submitted || incomplete) return;
    setSubmitted(true);
    const label = str(data.submitLabel ?? data.title, "Plan");
    ctx.onAction(`${label} — ${JSON.stringify({
      project: { ...project, name: name.trim() },
      tasks: rows.map((r) => ({
        title: str(r.title).trim(), owner: str(r.owner).trim(),
        effort_mins: num(r.effort_mins), due: str(r.due).slice(0, 10),
        importance: r.importance ?? null,
      })),
    })}`);
  };
  return (
    <div style={CARD_BOX}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <TIcon name="list-todo" size={15} color="var(--primary)" />
        <span style={{ fontSize: 13.5, fontWeight: 600, color: "var(--foreground)" }}>{str(data.title, "Plan")}</span>
      </div>
      {data.description != null && <div style={{ ...MUTED, marginBottom: 10 }}>{str(data.description)}</div>}
      <label style={{ ...MUTED, display: "block", marginBottom: 10 }}>
        Project name
        <input value={name} onChange={(e) => setName(e.target.value)} disabled={submitted}
          style={{ ...FIELD_INPUT_STYLE, marginTop: 4 }} />
      </label>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              {PLAN_COLS.map((c) => (
                <th key={c.key} style={{ ...MUTED, textAlign: "left", padding: "4px 4px", width: c.width,
                  borderBottom: "1px solid var(--border)" }}>{c.label}</th>
              ))}
              <th style={{ ...MUTED, textAlign: "right", padding: "4px 4px", borderBottom: "1px solid var(--border)" }}>Score</th>
              <th style={{ borderBottom: "1px solid var(--border)" }} />
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                {PLAN_COLS.map((c) => (
                  <td key={c.key} style={{ padding: "3px 4px" }}>
                    <input type={c.type} value={str(r[c.key])} disabled={submitted}
                      onChange={(e) => setCell(i, c.key, c.type === "number" ? num(e.target.value) : e.target.value)}
                      style={{ ...FIELD_INPUT_STYLE, padding: "5px 8px" }} />
                  </td>
                ))}
                <td style={{ ...MUTED, textAlign: "right", padding: "3px 4px", fontVariantNumeric: "tabular-nums" }}>
                  {r.priority != null ? str(r.priority) : ""}
                </td>
                <td style={{ padding: "3px 4px", textAlign: "right" }}>
                  {!submitted && (
                    <button type="button" onClick={() => drop(i)} aria-label="Remove task"
                      style={{ border: "none", background: "transparent", cursor: "pointer",
                        color: "var(--muted-foreground)", fontSize: 12 }}>✕</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!submitted && (
        <button type="button" onClick={add}
          style={{ ...MUTED, marginTop: 6, border: "1px dashed var(--border)", background: "transparent",
            borderRadius: 8, padding: "5px 10px", cursor: "pointer" }}>+ Add a task</button>
      )}
      {risks.length > 0 && (
        <div style={{ marginTop: 10, borderRadius: 10, padding: "8px 10px",
          background: "color-mix(in srgb, var(--warning) 12%, transparent)" }}>
          <div style={{ ...MUTED, fontWeight: 600, marginBottom: 4 }}>How this plan fails</div>
          {risks.map((r, i) => <div key={i} style={CELL}>• {r}</div>)}
        </div>
      )}
      <button type="button" onClick={submit} disabled={!ctx?.onAction || submitted || incomplete}
        style={{ marginTop: 12, fontSize: 12.5, fontWeight: 600, borderRadius: 8, padding: "8px 16px",
          border: "none", cursor: "pointer", color: "var(--primary-foreground)",
          background: submitted ? "var(--success)" : "var(--primary)",
          opacity: !ctx?.onAction || incomplete ? 0.5 : 1 }}>
        {submitted ? "✓ Submitted" : str(data.submitLabel, "Review plan")}
      </button>
      <div style={{ ...MUTED, marginTop: 6 }}>A confirmation card follows. Nothing is created until you approve it.</div>
    </div>
  );
}

// ── Registry ─────────────────────────────────────────────────────────────────

/** Interaction context threaded from the transcript/panel host: `onAction`
 *  sends a message back to the agent — via the blocking HITL resume when the
 *  spec carried a request_id, else as the user's next chat message. */
export interface TemplateCtx {
  onAction?: (message: string) => void;
}

export type TemplateRenderer = (data: Data, ctx?: TemplateCtx) => React.ReactElement;

export const TEMPLATE_REGISTRY: Record<string, TemplateRenderer> = {
  weatherCard: (data) => <WeatherCard data={data} />,
  statDashboard: (data) => <StatDashboard data={data} />,
  barChart: (data) => <BarChart data={data} />,
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
};

/** Render a template node, or an inert fallback if the name is unknown. */
export function renderTemplate(
  name: string, data: unknown, ctx?: TemplateCtx,
): React.ReactElement {
  const renderer = TEMPLATE_REGISTRY[name];
  const safeData = (data && typeof data === "object" ? data : {}) as Data;
  if (!renderer) {
    return (
      <div className="rounded border border-dashed border-border/60 px-2 py-1 text-[11px] text-muted-foreground">
        unknown template{name ? `: ${name}` : ""}
      </div>
    );
  }
  return renderer(safeData, ctx);
}
