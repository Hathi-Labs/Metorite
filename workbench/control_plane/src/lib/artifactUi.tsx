/**
 * @cc/ui — the Metorite design kit as React components, for artifacts.
 *
 * Agents importing this write `<Stat label="Revenue" value={18} unit="%" delta={12} />`
 * instead of six nested divs with exactly-right `cc-` class names. That is the
 * whole point: it costs a fraction of the tokens, it cannot typo a class name,
 * and every artifact comes out on-brand by construction.
 *
 * ── How it reaches an artifact ─────────────────────────────────────────────
 * This file is NOT part of the Next bundle. compileArtifact aliases the bare
 * specifier "@cc/ui" to it and bundles it into the artifact, where `react` is in
 * turn aliased to preact/compat. Unused exports are tree-shaken, so importing
 * one component does not drag the library in.
 *
 * ── The one rule when editing this file ────────────────────────────────────
 * Every className here must be a class the sandbox stylesheet actually defines
 * (SandboxedHtml.tsx). There is no CSS of our own: these components are a typed
 * front-end over the same kit that HTML artifacts use, which is what keeps the
 * two from drifting apart visually. artifact_lint.py's CC_CLASSES registry and
 * its drift test are the source of truth for what exists.
 */
import type { ReactNode } from "react";

/** Accent colour for any tinted component. Omit for the default blue. */
export type Tone = "success" | "warning" | "danger" | "accent";

const toneClass = (tone?: Tone, prefix = "cc-t-"): string =>
  tone ? ` ${prefix}${tone}` : "";

const clampPct = (n: number): number =>
  Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : 0;

// ── Document scaffolding ───────────────────────────────────────────────────

/** The document wrapper. Every artifact should start with this. */
export function Report({ children }: { children?: ReactNode }) {
  return <div className="cc-report">{children}</div>;
}

/** Small uppercase kicker above the title. */
export function Eyebrow({ children }: { children?: ReactNode }) {
  return <p className="cc-eyebrow">{children}</p>;
}

/** The large intro paragraph under the title. */
export function Lede({ children }: { children?: ReactNode }) {
  return <p className="cc-lede">{children}</p>;
}

/** A section marker, e.g. <SectionNum>01 — Metrics</SectionNum> before an <h2>. */
export function SectionNum({ children }: { children?: ReactNode }) {
  return <span className="cc-sec-num">{children}</span>;
}

/** Responsive grid of cards. */
export function Grid({ children }: { children?: ReactNode }) {
  return <div className="cc-grid">{children}</div>;
}

/** A panel. `title` renders with the standard leading dot. */
export function Card({ title, children }: { title?: ReactNode; children?: ReactNode }) {
  return (
    <div className="cc-card">
      {title != null && (
        <h4>
          <span className="cc-dot" />
          {title}
        </h4>
      )}
      {children}
    </div>
  );
}

/** Metadata row: <Chips><Chip label="Period" value="Q3" /></Chips> */
export function Chips({ children }: { children?: ReactNode }) {
  return <div className="cc-chips">{children}</div>;
}

/** One metadata chip, e.g. label="Period" value="Q3 FY26". */
export function Chip({ label, value }: { label?: ReactNode; value?: ReactNode }) {
  return (
    <span className="cc-chip">
      {label}
      {value != null && <b>{value}</b>}
    </span>
  );
}

// ── Numbers ────────────────────────────────────────────────────────────────

/** Row of KPI tiles. Wrap <Stat> children in this. */
export function Stats({ children }: { children?: ReactNode }) {
  return <div className="cc-stats">{children}</div>;
}

/**
 * A KPI tile — the big-number-is-the-hero block.
 * `delta` renders a coloured ▲/▼ (positive is up). `feature` spotlights one tile.
 */
export function Stat({
  label, value, unit, delta, deltaLabel, feature,
}: {
  label: ReactNode;
  value: ReactNode;
  unit?: string;
  /** Signed change; sign picks the arrow and the colour. */
  delta?: number;
  /** Override the delta text, e.g. "+12% QoQ". */
  deltaLabel?: string;
  feature?: boolean;
}) {
  return (
    <div className={`cc-stat${feature ? " cc-feature" : ""}`}>
      <p className="cc-k">{label}</p>
      <div className="cc-v">
        {value}
        {unit && <small>{unit}</small>}
      </div>
      {delta != null && (
        <div className={`cc-d ${delta >= 0 ? "cc-up" : "cc-down"}`}>
          {deltaLabel ?? `${delta > 0 ? "+" : ""}${delta}%`}
        </div>
      )}
    </div>
  );
}

/** Horizontal bar chart. Pass percentages (0–100) — the CSS draws the fill. */
export function Bars({
  data,
}: {
  data: { label: ReactNode; value: number; tone?: Tone; display?: string }[];
}) {
  return (
    <div className="cc-bars">
      {data.map((d, i) => (
        <div key={i} className={`cc-bar${toneClass(d.tone)}`}
             style={{ "--v": clampPct(d.value) } as Record<string, unknown>}>
          <b>{d.label}</b>
          <div className="cc-track" />
          <span>{d.display ?? `${Math.round(d.value)}%`}</span>
        </div>
      ))}
    </div>
  );
}

/** Ring gauges for single share/completion figures. */
export function Donuts({
  data,
}: {
  data: { label: ReactNode; value: number; tone?: Tone }[];
}) {
  return (
    <div className="cc-donuts">
      {data.map((d, i) => (
        <div key={i} className={`cc-donut${toneClass(d.tone)}`}
             style={{ "--v": clampPct(d.value) } as Record<string, unknown>}>
          <div className="cc-ring">
            <span>
              {Math.round(d.value)}
              <small>%</small>
            </span>
          </div>
          <b>{d.label}</b>
        </div>
      ))}
    </div>
  );
}

/** Map values onto an SVG viewBox, returning "x,y x,y …". */
function plot(values: number[], w: number, h: number, pad = 0): string {
  if (!values.length) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = values.length > 1 ? w / (values.length - 1) : 0;
  return values
    .map((v, i) => `${(i * step).toFixed(2)},${(h - pad - ((v - min) / span) * (h - pad * 2)).toFixed(2)}`)
    .join(" ");
}

/** Tiny inline sparkline. You pass the numbers; the geometry is computed. */
export function Spark({ values }: { values: number[] }) {
  return (
    <div className="cc-spark">
      <svg viewBox="0 0 100 30" preserveAspectRatio="none">
        <polyline points={plot(values, 100, 30, 3)} />
      </svg>
    </div>
  );
}

/** Trend/area chart with grid, fill, and an emphasised endpoint. */
export function Chart({
  values, labels, tone,
}: {
  values: number[];
  labels?: string[];
  tone?: Tone;
}) {
  const W = 300;
  const H = 120;
  const pts = plot(values, W, H, 6);
  const last = pts.split(" ").pop()?.split(",") ?? ["0", "0"];
  return (
    <div className="cc-chart">
      <svg className="cc-plot" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        <g className="cc-grid">
          {[0.25, 0.5, 0.75].map((f) => (
            <line key={f} x1="0" x2={W} y1={H * f} y2={H * f} />
          ))}
        </g>
        {pts && <polyline className={`cc-area${toneClass(tone)}`} points={`0,${H} ${pts} ${W},${H}`} />}
        <polyline className={`cc-line${toneClass(tone)}`} points={pts} />
        <circle className="cc-end" cx={last[0]} cy={last[1]} r="3" />
      </svg>
      {labels?.length ? (
        <div className="cc-x">
          {labels.map((l, i) => (
            <span key={i}>{l}</span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// ── Tables ─────────────────────────────────────────────────────────────────

/**
 * Data table. `rows` are plain cell values — strings, numbers, or the cell
 * helpers below (<Status>, <Pill>, <MiniBar>). `stripes` adds a leading
 * severity bar per row.
 */
export function Table({
  columns, rows, stripes,
}: {
  columns: ReactNode[];
  rows: ReactNode[][];
  stripes?: (Tone | undefined)[];
}) {
  return (
    <div className="cc-table">
      <table>
        <thead>
          <tr>
            {stripes && <th />}
            {columns.map((c, i) => (
              <th key={i}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri}>
              {stripes && <td className={`cc-stripe${toneClass(stripes[ri])}`} />}
              {row.map((cell, ci) => (
                <td key={ci} className={typeof cell === "number" ? "cc-num" : undefined}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Table cell: coloured dot + label. */
export function Status({ tone, children }: { tone?: Tone; children?: ReactNode }) {
  return <span className={`cc-status${toneClass(tone)}`}>{children}</span>;
}

/** Table cell: solid status pill. */
export function Pill({ tone, children }: { tone?: Tone; children?: ReactNode }) {
  return <span className={`cc-tag-pill${toneClass(tone)}`}>{children}</span>;
}

/** Table cell: inline proportion bar (0–100). */
export function MiniBar({ value, tone }: { value: number; tone?: Tone }) {
  return (
    <span className={`cc-minibar${toneClass(tone)}`}
          style={{ "--v": clampPct(value) } as Record<string, unknown>} />
  );
}

// ── Narrative blocks ───────────────────────────────────────────────────────

/** Short status notice. Use Decision when there is a verdict. */
export function Note({ tone, children }: { tone?: "info" | "success" | "warning" | "danger"; children?: ReactNode }) {
  const glyph = tone === "success" ? "✓" : tone === "danger" ? "✕" : "!";
  return (
    <div className={`cc-note${tone ? ` cc-${tone}` : ""}`}>
      <span className="cc-ico">{glyph}</span>
      <p>{children}</p>
    </div>
  );
}

/** Tinted highlight panel with a small tag. */
export function Callout({ tag, primary, children }: { tag?: ReactNode; primary?: boolean; children?: ReactNode }) {
  return (
    <div className={`cc-callout${primary ? " cc-callout-key" : ""}`}>
      {tag != null && <span className="cc-tag">{tag}</span>}
      <p>{children}</p>
    </div>
  );
}

/** The recommendation box — a verdict as the hero. */
export function Decision({
  verdict = "Recommendation", title, tone, children,
}: {
  verdict?: ReactNode;
  title: ReactNode;
  tone?: Tone;
  children?: ReactNode;
}) {
  const mark = tone === "danger" ? "✕" : tone === "warning" ? "!" : "✓";
  return (
    <div className={`cc-decision${toneClass(tone)}`}>
      <div className="cc-mark">{mark}</div>
      <div>
        <p className="cc-verdict">{verdict}</p>
        <h4>{title}</h4>
        {children != null && <p>{children}</p>}
      </div>
    </div>
  );
}

/** Numbered sequence — only when order genuinely matters. */
export function Steps({ items }: { items: { title: ReactNode; body?: ReactNode }[] }) {
  return (
    <div className="cc-steps">
      {items.map((s, i) => (
        <div key={i} className="cc-step">
          <div className="cc-n">{i + 1}</div>
          <div>
            <h4>{s.title}</h4>
            {s.body != null && <p>{s.body}</p>}
          </div>
        </div>
      ))}
    </div>
  );
}

/** Gantt-style roadmap. `start` is 1-based; `span` is a column count. */
export function Timeline({
  cols = 12, rows, labels,
}: {
  cols?: number;
  rows: { label: ReactNode; start: number; span: number; tone?: Tone; ghost?: boolean }[];
  labels?: string[];
}) {
  return (
    <>
      <div className="cc-timeline" style={{ "--cols": cols } as Record<string, unknown>}>
        {rows.map((r, i) => (
          <div key={i} className="cc-tl-row">
            <b>{r.label}</b>
            <div className="cc-tl-track">
              <span
                className={`cc-tl-bar${toneClass(r.tone)}${r.ghost ? " cc-ghost" : ""}`}
                style={{ "--s": r.start, "--e": r.span } as Record<string, unknown>}
              >
                {r.label}
              </span>
            </div>
          </div>
        ))}
      </div>
      {labels?.length ? (
        <div className="cc-tl-axis" style={{ "--cols": cols } as Record<string, unknown>}>
          <div />
          <div className="cc-tl-ticks">
            {labels.map((l, i) => (
              <span key={i}>{l}</span>
            ))}
          </div>
        </div>
      ) : null}
    </>
  );
}

// ── Icons ──────────────────────────────────────────────────────────────────

/**
 * A Lucide icon, by name (e.g. "trending-up", "check-circle", "building-2").
 *
 * The sandbox has NO network, so an icon cannot be fetched — the parent resolves
 * the ones your source mentions into inline SVG and injects them. That means the
 * name must be a literal string here, not built at runtime: `<Icon name="rocket" />`
 * works, `<Icon name={someVar} />` cannot be seen by the scanner and renders
 * nothing. Colour is inherited (`currentColor`), so set it on a parent.
 *
 * This is the ONLY way to show imagery in an artifact besides a data: URI —
 * remote logos and images are blocked by the sandbox and will not appear.
 */
export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  return (
    <span
      data-cc-icon={name}
      aria-hidden="true"
      style={{ display: "inline-flex", width: size, height: size,
               verticalAlign: "middle" }}
    />
  );
}

// ── Talking back to the agent ──────────────────────────────────────────────
// The reason to build a React artifact rather than an HTML one: the user does
// something, and the agent hears about it.

declare global {
  interface Window {
    ccSubmit?: (label: string, value?: unknown) => void;
    ccAction?: (message: string) => void;
  }
}

/** Sends a VALUE the user chose back to the agent. */
export function Submit({
  label, value, primary = true, children,
}: {
  label: string;
  value?: unknown;
  primary?: boolean;
  children?: ReactNode;
}) {
  return (
    <button className={primary ? "cc-primary" : undefined}
            onClick={() => window.ccSubmit?.(label, value)}>
      {children ?? label}
    </button>
  );
}

/** Sends a fixed follow-up message, like clicking a suggestion. */
export function Action({ message, children }: { message: string; children?: ReactNode }) {
  return <button onClick={() => window.ccAction?.(message)}>{children ?? message}</button>;
}
