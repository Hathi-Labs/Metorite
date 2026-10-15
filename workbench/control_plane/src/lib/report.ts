/**
 * A report the agent makes, as data (owner directive, 2026-10-16).
 *
 * `make_report` writes `outputs/<slug>.report.json` and its PDF beside it,
 * and it sends a `generative_ui` card with the template `report`. The data of
 * that card is the file's JSON plus `path`. ONE component draws it:
 * `ReportView` in `components/genUITemplates.tsx`, in the chat card, in the
 * side panel, in the file viewer and on `/artifacts`.
 *
 * This module holds the pure half: it reads the data into a `Report` that a
 * renderer can trust, and it maps each visual onto the data of the existing
 * template that draws its kind. The model gives text and numbers. Code draws
 * every visual, so this file names no colour and builds no markup.
 *
 * ⚠️ A missing or wrong field never blanks the view. Every reader here
 * returns an empty value for a field it cannot read, and the view draws what
 * is left. A page that blanked on one bad field is the trap this avoids.
 *
 * Which file IS a report is the one classifier's answer (`artifactKind.ts`,
 * `REPORT_SUFFIX`), never a second test here.
 *
 * Fence: `src/lib/report.test.ts` and `src/components/reportView.test.ts`.
 */

/** The `kind` of a report file. A file without it is not a report. */
export const REPORT_KIND = "metorite.report";

/** The visual kinds a section may hold: `make_document`'s kinds. */
export const REPORT_VISUAL_KINDS = [
  "chart", "stats", "table", "board", "timeline", "gantt", "agenda",
] as const;

export type ReportVisualKind = (typeof REPORT_VISUAL_KINDS)[number];

/**
 * The most of each list that the view draws. The server refuses more
 * (`genui_refusal` in `write_artifact.py`), so these only stop a bad file
 * from costing the page.
 */
export const REPORT_LIMITS = {
  kpis: 8,
  sections: 30,
  bullets: 16,
  sources: 30,
  tableColumns: 6,
  tableRows: 20,
  boardColumns: 6,
  boardCards: 12,
  events: 24,
  agendaItems: 24,
  /** The characters of the summary that the chat card shows. */
  cardSummary: 240,
} as const;

export interface ReportKpi {
  label: string;
  value: string | number;
  unit: string;
  /** Text such as "+12%" or "-3". Empty when the tile has none. */
  delta: string;
  /** Which way is good. A rise is good unless this says "down". */
  good: "up" | "down";
}

export interface ReportVisual {
  kind: string;
  title: string;
  /** The visual's own fields, without `kind`. */
  data: Record<string, unknown>;
}

export interface ReportSection {
  heading: string;
  text: string;
  bullets: string[];
  visual: ReportVisual | null;
}

export interface Report {
  title: string;
  subtitle: string;
  for: string;
  date: string;
  period: string;
  summary: string;
  kpis: ReportKpi[];
  sections: ReportSection[];
  sources: string[];
  /** The companion PDF, a safe `outputs/…pdf` path, or null. */
  pdf: string | null;
  /** The report file, a safe `outputs/…report.json` path, or null. */
  path: string | null;
}

type Data = Record<string, unknown>;

const obj = (v: unknown): Data | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Data) : null;

const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/**
 * A field as text. A number or a boolean is its words. An object or a list
 * is nothing, so the view never draws "[object Object]".
 */
export function text(v: unknown): string {
  if (typeof v === "string") return v.trim();
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  return "";
}

/** A table cell: text or a number, never an object. */
function cell(v: unknown): string | number {
  return typeof v === "number" && Number.isFinite(v) ? v : text(v);
}

const UNSAFE_PATH = /[\\:?#\u0000-\u001f]/;

/** A workspace path under `outputs/` with no `..`, and the given ending. */
function safeOutputsPath(raw: unknown, ending: RegExp): string | null {
  if (typeof raw !== "string") return null;
  const p = raw.trim();
  if (!p.startsWith("outputs/") || !ending.test(p) || UNSAFE_PATH.test(p) || p.length > 300) return null;
  if (p.split("/").some((seg) => seg === "" || seg === "." || seg === "..")) return null;
  return p;
}

/** The PDF path when it is a safe `outputs/…pdf` path, else null. */
export function safePdfPath(raw: unknown): string | null {
  return safeOutputsPath(raw, /\.pdf$/i);
}

/** The report path when it is a safe `outputs/…report.json` path, else null. */
export function safeReportPath(raw: unknown): string | null {
  return safeOutputsPath(raw, /\.report\.json$/i);
}

/** `outputs/q3.report.json` → `outputs/q3.pdf`, the name `make_report` gives. */
export function companionPdfPath(reportPath: string): string | null {
  if (!/\.report\.json$/i.test(reportPath)) return null;
  return safePdfPath(reportPath.replace(/\.report\.json$/i, ".pdf"));
}

/**
 * The address of another file beside `fileUrl`: the same route, with its
 * `path` set to `path`. The `/artifacts` browser reads a file through a
 * route of its own, so the PDF must use that route too. Null when the URL
 * names no `path`.
 */
export function siblingFileUrl(fileUrl: string, path: string): string | null {
  const at = fileUrl.indexOf("?");
  if (at < 0) return null;
  const params = new URLSearchParams(fileUrl.slice(at + 1));
  if (!params.has("path")) return null;
  params.set("path", path);
  params.delete("format");
  return `${fileUrl.slice(0, at)}?${params.toString()}`;
}

/** The last part of a path: its file name. */
export function baseName(path: string): string {
  return path.split("/").pop() || path;
}

function kpiOf(raw: unknown): ReportKpi | null {
  const k = obj(raw);
  if (!k) return null;
  const label = text(k.label);
  const value = typeof k.value === "number" && Number.isFinite(k.value) ? k.value : text(k.value);
  if (!label && value === "") return null;
  return {
    label,
    value,
    unit: text(k.unit),
    delta: text(k.delta),
    good: text(k.good).toLowerCase() === "down" ? "down" : "up",
  };
}

function visualOf(raw: unknown): ReportVisual | null {
  const v = obj(raw);
  if (!v) return null;
  const { kind, ...data } = v;
  return { kind: text(kind).toLowerCase(), title: text(v.title), data };
}

function sectionOf(raw: unknown): ReportSection | null {
  const s = obj(raw);
  if (!s) return null;
  const section: ReportSection = {
    heading: text(s.heading),
    text: text(s.text),
    bullets: list(s.bullets).map(text).filter(Boolean).slice(0, REPORT_LIMITS.bullets),
    visual: visualOf(s.visual),
  };
  const empty = !section.heading && !section.text && !section.bullets.length && !section.visual;
  return empty ? null : section;
}

/**
 * The report in `raw`, read field by field. It never throws and never
 * returns null: a card with a bad field still draws its title and the rest.
 * `path` names the file when the data does not.
 */
export function reportFrom(raw: unknown, path?: string): Report {
  const d = obj(raw) ?? {};
  const file = safeReportPath(d.path) ?? safeReportPath(path);
  const pdf = safePdfPath(d.pdf) ?? (file ? companionPdfPath(file) : null);
  return {
    title: text(d.title) || "Report",
    subtitle: text(d.subtitle),
    for: text(d.for),
    date: text(d.date),
    period: text(d.period),
    summary: text(d.summary),
    kpis: list(d.kpis).map(kpiOf).filter((k): k is ReportKpi => k !== null).slice(0, REPORT_LIMITS.kpis),
    sections: list(d.sections)
      .map(sectionOf)
      .filter((s): s is ReportSection => s !== null)
      .slice(0, REPORT_LIMITS.sections),
    sources: list(d.sources).map(text).filter(Boolean).slice(0, REPORT_LIMITS.sources),
    pdf,
    path: file,
  };
}

/**
 * A report file's text as a `Report`, or null when the text is not one: bad
 * JSON, not an object, or another `kind`. The viewers show the raw file for
 * null, so a broken file still opens.
 */
export function parseReportFile(source: string, path?: string): Report | null {
  let raw: unknown;
  try {
    raw = JSON.parse(source);
  } catch {
    return null;
  }
  const d = obj(raw);
  if (!d || d.kind !== REPORT_KIND) return null;
  return reportFrom(d, path);
}

/** The words under the title: subtitle, who it is for, the date. */
export function reportMeta(r: Report, opts: { period?: boolean } = {}): string[] {
  const out: string[] = [];
  if (r.for) out.push(`For ${r.for}`);
  if (r.date) out.push(r.date);
  if (opts.period && r.period) out.push(r.period);
  return out;
}

/**
 * The grid of the KPI tiles, by their count, so a row never ends with one
 * tile alone at full width. The tiles sit two to a row in a chat column
 * (the assistant dock, a phone) and four to a row where the box is wide.
 * The box is its own size container, so the card's width decides, not the
 * window's. Below 13rem a tile would cut its value, so each tile takes a row.
 * Only /chat with the Documents and Files panes open is that narrow.
 */
export function kpiGridClass(count: number): string {
  if (count <= 1) return "grid grid-cols-1 gap-1.5";
  if (count === 2) return "grid grid-cols-1 gap-1.5 @[13rem]:grid-cols-2";
  if (count === 3) return "grid grid-cols-1 gap-1.5 @[18rem]:grid-cols-3";
  if (count <= 4 || count >= 7) return "grid grid-cols-1 gap-1.5 @[13rem]:grid-cols-2 @[30rem]:grid-cols-4";
  return "grid grid-cols-1 gap-1.5 @[13rem]:grid-cols-2 @[24rem]:grid-cols-3";
}

/** The first section that holds a visual: the chat card draws it, under its heading. */
export function firstVisualSection(r: Report): ReportSection | null {
  return r.sections.find((s) => s.visual !== null) ?? null;
}

/**
 * The summary as plain words, for the chat card. The marks of Markdown-lite
 * go, and the lines join, so a cut never leaves a lone `**`. A line that
 * ends a sentence joins with a space. Any other line joins with a dot, so
 * two list items never read as one.
 */
export function plainSummary(summary: string): string {
  const lines = summary
    .split(/\r?\n/)
    .map((line) => line.replace(/^\s*(?:[-*+]|\d+[.)])\s+/, "").trim())
    .filter(Boolean);
  return lines
    .map((line, i) => (i === 0 ? line : `${/[.!?:][*_`]*$/.test(lines[i - 1]) ? " " : " · "}${line}`))
    .join("")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/(\*\*|__|\*|_|`)/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * The summary and a section's text as Markdown-lite: bold, italic, lists and
 * line breaks. An image goes, and a link keeps its words only, because the
 * contract holds no image and no URL. A single line break stays a break.
 */
export function liteMarkdown(source: string): string {
  return source
    .replace(/\r\n?/g, "\n")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/<[^>]*>/g, "")
    .replace(/([^\n])\n(?!\n|\s*(?:[-*+]|\d+[.)])\s)/g, "$1  \n");
}

// ── The visuals, onto the templates that draw their kind ───────────────────
//
// Each mapper returns the `data` of an existing genUI template. The view
// draws it with that template's component, so a report adds no second chart,
// table or board.

/** The name of the template that draws a visual, or null for an unknown kind. */
export type VisualTemplate = "chart" | "statDashboard" | "dataGrid" | "taskBoard" | "timeline";

export interface MappedVisual {
  template: VisualTemplate;
  data: Data;
}

/**
 * A chart's spec with no title or subtitle. The report draws a chart's title
 * as its own words, at the report's type size. Inside the picture the
 * engine drew it larger than the section heading above it.
 */
export function bareChartSpec(spec: Data): Data {
  return { ...spec, title: undefined, subtitle: undefined };
}

/** A visual as a template and its data, or null when no template draws it. */
export function mapVisual(v: ReportVisual): MappedVisual | null {
  const d = v.data;
  const title = v.title || undefined;
  switch (v.kind) {
    case "chart":
      // The chart language's own spec. The engine checks it, and a bad spec
      // shows its reason in place of the chart (`LiveChart`).
      return { template: "chart", data: { ...d, type: text(d.type) || "bar" } };
    case "gantt":
      return { template: "chart", data: { ...d, type: "gantt" } };
    case "stats":
      return {
        template: "statDashboard",
        data: {
          title,
          stats: list(d.tiles)
            .map(kpiOf)
            .filter((k): k is ReportKpi => k !== null)
            .slice(0, REPORT_LIMITS.kpis),
        },
      };
    case "table": {
      const columns = list(d.columns).map(text).slice(0, REPORT_LIMITS.tableColumns);
      const rows = list(d.rows).slice(0, REPORT_LIMITS.tableRows).map((r) => {
        const o = obj(r);
        const cells = Array.isArray(r) ? r : o && Array.isArray(o.cells) ? o.cells : [r];
        return { cells: cells.slice(0, REPORT_LIMITS.tableColumns).map(cell) };
      });
      // An empty base: a report row carries no id, so it links nowhere.
      return { template: "dataGrid", data: { title, columns, rows, openBase: "" } };
    }
    case "agenda": {
      const items = list(d.items).map(obj).filter((i): i is Data => i !== null).slice(0, REPORT_LIMITS.agendaItems);
      const detail = items.some((i) => text(i.detail) !== "");
      const rows = items.map((i) => {
        const when = [text(i.start), text(i.end)].filter(Boolean).join("–");
        const cells: (string | number)[] = [when, text(i.title)];
        if (detail) cells.push(text(i.detail));
        return { cells };
      });
      const columns = detail ? ["Time", "Title", "Details"] : ["Time", "Title"];
      return { template: "dataGrid", data: { title, columns, rows, openBase: "" } };
    }
    case "board":
      return {
        template: "taskBoard",
        data: {
          title,
          columns: list(d.columns)
            .map(obj)
            .filter((c): c is Data => c !== null)
            .slice(0, REPORT_LIMITS.boardColumns)
            .map((c) => ({
              name: text(c.name),
              tasks: list(c.cards)
                .map(obj)
                .filter((k): k is Data => k !== null)
                .slice(0, REPORT_LIMITS.boardCards)
                .map((k) => ({ title: text(k.title), meta: text(k.meta) })),
            })),
        },
      };
    case "timeline":
      return {
        template: "timeline",
        data: {
          title,
          rows: list(d.events)
            .map(obj)
            .filter((e): e is Data => e !== null)
            .slice(0, REPORT_LIMITS.events)
            .map((e) => ({
              at: text(e.date),
              type: text(e.state) || "upcoming",
              actor: text(e.title),
              body: text(e.detail) || undefined,
            })),
        },
      };
    default:
      return null;
  }
}
