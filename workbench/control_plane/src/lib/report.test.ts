/**
 * The pure half of a report (owner directive, 2026-10-16): read the data, and
 * map each visual onto the template that draws its kind.
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - `safePdfPath` takes `..` or a path outside `outputs/` -> "a PDF path
 *   stays under outputs/";
 * - `parseReportFile` takes another `kind` -> "a file of another kind is
 *   not a report";
 * - `mapVisual` maps an unknown kind -> "an unknown kind maps to nothing";
 * - `reportFrom` drops the title default -> "a report with no title is
 *   still a report".
 */
import { describe, expect, it } from "vitest";

import {
  REPORT_KIND,
  REPORT_LIMITS,
  REPORT_VISUAL_KINDS,
  bareChartSpec,
  companionPdfPath,
  firstVisualSection,
  kpiGridClass,
  liteMarkdown,
  mapVisual,
  parseReportFile,
  plainSummary,
  reportFrom,
  reportMeta,
  safePdfPath,
  safeReportPath,
  siblingFileUrl,
  text,
} from "./report";

const FULL = {
  kind: REPORT_KIND,
  version: 1,
  path: "outputs/q3-review.report.json",
  title: "Q3 review",
  subtitle: "Sales and delivery",
  for: "The board",
  date: "16 Oct 2026",
  period: "Jul to Sep 2026",
  summary: "**Revenue grew.**\n- Pipeline is up\n- Churn is down",
  kpis: [
    { label: "Revenue", value: "4.2", unit: "Cr", delta: "+12%", good: "up" },
    { label: "Churn", value: 3, unit: "%", delta: "-1", good: "down" },
  ],
  sections: [
    { heading: "Pipeline", text: "It grew.", visual: { kind: "chart", type: "bar", labels: ["A"], values: [1] } },
    { heading: "Risks", bullets: ["One", "Two"] },
  ],
  sources: ["CRM", "Projects"],
  pdf: "outputs/q3-review.pdf",
};

describe("reportFrom", () => {
  it("reads a full report", () => {
    const r = reportFrom(FULL);
    expect(r.title).toBe("Q3 review");
    expect(r.for).toBe("The board");
    expect(r.kpis).toHaveLength(2);
    expect(r.kpis[1]).toEqual({ label: "Churn", value: 3, unit: "%", delta: "-1", good: "down" });
    expect(r.sections.map((s) => s.heading)).toEqual(["Pipeline", "Risks"]);
    expect(r.sections[0].visual).toEqual({
      kind: "chart", title: "", data: { type: "bar", labels: ["A"], values: [1] },
    });
    expect(r.sources).toEqual(["CRM", "Projects"]);
    expect(r.pdf).toBe("outputs/q3-review.pdf");
    expect(r.path).toBe("outputs/q3-review.report.json");
  });

  it("a report with no title is still a report, and no field blanks it", () => {
    for (const raw of [null, undefined, 7, "x", [], {}, { title: { bad: 1 }, kpis: "no", sections: { a: 1 } }]) {
      const r = reportFrom(raw);
      expect(r.title, JSON.stringify(raw)).toBe("Report");
      expect(r.kpis).toEqual([]);
      expect(r.sections).toEqual([]);
      expect(r.sources).toEqual([]);
      expect(r.pdf).toBeNull();
    }
  });

  it("drops an object where text belongs, never drawing [object Object]", () => {
    const r = reportFrom({ title: "T", subtitle: { a: 1 }, kpis: [{ label: { x: 1 }, value: [1] }, null, "x"] });
    expect(r.subtitle).toBe("");
    expect(r.kpis).toEqual([]);
    expect(text({ a: 1 })).toBe("");
    expect(text(NaN)).toBe("");
    expect(text(4.5)).toBe("4.5");
  });

  it("drops an empty section and caps each list", () => {
    const r = reportFrom({
      title: "T",
      kpis: Array.from({ length: 12 }, (_, i) => ({ label: `K${i}`, value: i })),
      sections: [{}, { heading: "" }, ...Array.from({ length: 40 }, (_, i) => ({ heading: `S${i}` }))],
    });
    expect(r.kpis).toHaveLength(REPORT_LIMITS.kpis);
    expect(r.sections).toHaveLength(REPORT_LIMITS.sections);
    expect(r.sections[0].heading).toBe("S0");
  });

  it("takes the PDF from the path when the data names none", () => {
    expect(reportFrom({ title: "T" }, "outputs/a.report.json").pdf).toBe("outputs/a.pdf");
    expect(reportFrom({ title: "T", pdf: "../etc/passwd.pdf" }, "outputs/a.report.json").pdf).toBe("outputs/a.pdf");
  });
});

describe("parseReportFile", () => {
  it("reads a report file", () => {
    expect(parseReportFile(JSON.stringify(FULL))?.title).toBe("Q3 review");
  });

  it("bad JSON is not a report, so the viewer shows the raw file", () => {
    for (const bad of ["", "{", "{\"kind\":", "not json", "[]", "null", "42"]) {
      expect(parseReportFile(bad), bad).toBeNull();
    }
  });

  it("a file of another kind is not a report", () => {
    expect(parseReportFile(JSON.stringify({ ...FULL, kind: "something.else" }))).toBeNull();
    expect(parseReportFile(JSON.stringify({ title: "No kind" }))).toBeNull();
  });

  it("names its own path when the data carries none", () => {
    const rest: Record<string, unknown> = { ...FULL };
    delete rest.path;
    delete rest.pdf;
    const r = parseReportFile(JSON.stringify(rest), "outputs/x.report.json");
    expect(r?.path).toBe("outputs/x.report.json");
    expect(r?.pdf).toBe("outputs/x.pdf");
  });
});

describe("the paths", () => {
  it("a PDF path stays under outputs/", () => {
    expect(safePdfPath("outputs/a.pdf")).toBe("outputs/a.pdf");
    expect(safePdfPath("outputs/sub/a b.PDF")).toBe("outputs/sub/a b.PDF");
    for (const bad of [
      "inputs/a.pdf", "/outputs/a.pdf", "outputs/../a.pdf", "outputs//a.pdf", "outputs/a.pdf.exe",
      "outputs\\a.pdf", "https://x.test/a.pdf", "outputs/a.pdf?x=1", 7, null, "outputs/\u0000.pdf",
    ]) {
      expect(safePdfPath(bad), String(bad)).toBeNull();
    }
    expect(safeReportPath("outputs/a.report.json")).toBe("outputs/a.report.json");
    expect(safeReportPath("outputs/a.json")).toBeNull();
  });

  it("the PDF beside a report has the report's name", () => {
    expect(companionPdfPath("outputs/q3.report.json")).toBe("outputs/q3.pdf");
    expect(companionPdfPath("outputs/q3.json")).toBeNull();
  });

  it("a sibling address keeps the route and changes the path", () => {
    expect(siblingFileUrl("/api/agent/artifacts/file?agent=a%20b&path=outputs%2Fq.report.json", "outputs/q.pdf"))
      .toBe("/api/agent/artifacts/file?agent=a+b&path=outputs%2Fq.pdf");
    expect(siblingFileUrl("/api/x/file?path=a&format=pdf", "outputs/q.pdf")).toBe("/api/x/file?path=outputs%2Fq.pdf");
    expect(siblingFileUrl("/api/x/file", "outputs/q.pdf")).toBeNull();
    expect(siblingFileUrl("/api/x/file?agent=a", "outputs/q.pdf")).toBeNull();
  });
});

describe("the words", () => {
  it("the meta line names who it is for and when", () => {
    const r = reportFrom(FULL);
    expect(reportMeta(r)).toEqual(["For The board", "16 Oct 2026"]);
    expect(reportMeta(r, { period: true })).toEqual(["For The board", "16 Oct 2026", "Jul to Sep 2026"]);
    expect(reportMeta(reportFrom({}))).toEqual([]);
  });

  it("the card's summary is plain words, so a cut leaves no lone mark", () => {
    expect(plainSummary(FULL.summary)).toBe("Revenue grew. Pipeline is up · Churn is down");
    expect(plainSummary("![x](https://e.test/a.png) see [the site](https://e.test)")).toBe("see the site");
  });

  it("two list items never read as one, and a sentence keeps its own stop", () => {
    expect(plainSummary(["Done.", "- a", "- b"].join("\n"))).toBe("Done. a · b");
  });

  it("the KPI grid never leaves one tile alone at the end of a row of four", () => {
    // Two to a row in the narrowest column, four where it is wide.
    expect(kpiGridClass(4)).toBe("grid grid-cols-1 gap-1.5 @[13rem]:grid-cols-2 @[30rem]:grid-cols-4");
    expect(kpiGridClass(8)).toBe("grid grid-cols-1 gap-1.5 @[13rem]:grid-cols-2 @[30rem]:grid-cols-4");
    expect(kpiGridClass(6)).toContain("@[24rem]:grid-cols-3");
    expect(kpiGridClass(3)).toBe("grid grid-cols-1 gap-1.5 @[18rem]:grid-cols-3");
    expect(kpiGridClass(1)).toBe("grid grid-cols-1 gap-1.5");
    // From 13rem (the dock is 23rem), never one tile to a row once there are two.
    for (const n of [2, 4, 5, 6, 7, 8]) expect(kpiGridClass(n), String(n)).toContain("@[13rem]:grid-cols-2");
  });

  it("Markdown-lite keeps bold, lists and line breaks, and drops an image, a URL and HTML", () => {
    expect(liteMarkdown("one\ntwo")).toBe("one  \ntwo");
    expect(liteMarkdown("a\n- b\n- c")).toBe("a\n- b\n- c");
    expect(liteMarkdown("**b** ![p](https://e.test/x.png) [w](https://e.test) <img src=x>"))
      .toBe("**b** p w ");
  });
});

describe("mapVisual", () => {
  const v = (kind: string, data: Record<string, unknown>, title = "") => ({ kind, title, data });

  it("maps every visual kind of make_document onto a template", () => {
    const seen = new Set<string>();
    for (const kind of REPORT_VISUAL_KINDS) {
      const m = mapVisual(v(kind, {}));
      expect(m, kind).not.toBeNull();
      seen.add(m!.template);
    }
    expect([...seen].sort()).toEqual(["chart", "dataGrid", "statDashboard", "taskBoard", "timeline"]);
  });

  it("a report chart draws no title of its own: the report's words carry it", () => {
    const spec = bareChartSpec({ type: "bar", title: "Revenue", subtitle: "Q3", labels: ["a"], values: [1] });
    expect(spec.title).toBeUndefined();
    expect(spec.subtitle).toBeUndefined();
    expect(spec.labels).toEqual(["a"]);
  });

  it("an unknown kind maps to nothing", () => {
    for (const kind of ["map", "", "image", "constructor"]) expect(mapVisual(v(kind, {})), kind).toBeNull();
  });

  it("a chart keeps the chart language's spec, and a gantt is a gantt chart", () => {
    expect(mapVisual(v("chart", { type: "donut", labels: ["a"], values: [1], title: "T" }))).toEqual({
      template: "chart", data: { type: "donut", labels: ["a"], values: [1], title: "T" },
    });
    expect(mapVisual(v("chart", { labels: ["a"], values: [1] }))?.data.type).toBe("bar");
    expect(mapVisual(v("gantt", { rows: [{ label: "x", start: "2026-10-01", end: "2026-10-09" }] }))?.data.type).toBe("gantt");
  });

  it("a table caps its columns and rows, and a cell is never an object", () => {
    const m = mapVisual(v("table", {
      columns: ["a", "b", "c", "d", "e", "f", "g"],
      rows: [...Array.from({ length: 25 }, () => [1, "x", { o: 1 }, null, true, 2, 3]), { cells: ["c"] }],
    }, "Deals"));
    expect(m?.template).toBe("dataGrid");
    expect(m?.data.columns).toHaveLength(6);
    const rows = m?.data.rows as { cells: unknown[] }[];
    expect(rows).toHaveLength(20);
    expect(rows[0].cells).toEqual([1, "x", "", "", "Yes", 2]);
    expect(m?.data.title).toBe("Deals");
    expect(m?.data.openBase).toBe("");
  });

  it("stats, board, timeline and agenda carry their fields", () => {
    expect(mapVisual(v("stats", { tiles: [{ label: "A", value: 1, delta: "+2", good: "down" }] }))?.data.stats)
      .toEqual([{ label: "A", value: 1, unit: "", delta: "+2", good: "down" }]);
    expect(mapVisual(v("board", { columns: [{ name: "Doing", cards: [{ title: "X", meta: "Asha" }] }] }))?.data.columns)
      .toEqual([{ name: "Doing", tasks: [{ title: "X", meta: "Asha" }] }]);
    expect(mapVisual(v("timeline", { events: [{ date: "2026-10-01", title: "Kickoff", state: "done" }] }))?.data.rows)
      .toEqual([{ at: "2026-10-01", type: "done", actor: "Kickoff", body: undefined }]);
    const agenda = mapVisual(v("agenda", { items: [{ start: "09:30", end: "10:00", title: "Standup" }] }));
    expect(agenda?.data.columns).toEqual(["Time", "Title"]);
    expect(agenda?.data.rows).toEqual([{ cells: ["09:30–10:00", "Standup"] }]);
    const withDetail = mapVisual(v("agenda", { items: [{ start: "9", title: "A", detail: "Room 2" }] }));
    expect(withDetail?.data.columns).toEqual(["Time", "Title", "Details"]);
  });

  it("the first visual is the first section's that has one", () => {
    const r = reportFrom({ sections: [{ heading: "A" }, { heading: "B", visual: { kind: "table" } }] });
    expect(firstVisualSection(r)?.heading).toBe("B");
    expect(firstVisualSection(r)?.visual?.kind).toBe("table");
    expect(firstVisualSection(reportFrom({}))).toBeNull();
  });
});
