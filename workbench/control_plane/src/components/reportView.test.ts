/**
 * The one report renderer, `ReportView`, in each place it draws (owner
 * directive, 2026-10-16): the chat card, the side panel, the file viewer and
 * `/artifacts`. Each visual draws through the template of its kind.
 *
 * Node-env vitest renders markup and no state, so a click is proved through
 * `lib/genUiFiles.ts` (its own test) and the wiring here by markup and source.
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - `ReportVisualBlock` draws nothing for an unknown kind -> "an unknown kind
 *   shows a note";
 * - the card drops its "Open report" -> "the card offers Open report";
 * - `classifyArtifact` loses the report test -> "a report file is a report"
 *   (`artifactKind.test.ts`) and "the file card downloads the PDF beside it";
 * - `DocumentPane` stops drawing `ReportView` -> "the side panel and the viewer
 *   draw a report through ReportView".
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement as rawElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push() {} }),
  usePathname: () => "/chat",
  useSearchParams: () => new URLSearchParams(),
}));

import { cardPdf } from "@/components/ArtifactCard";
import MessageBubble from "@/components/MessageBubble";
import { REPORT_VISUAL_MISSING, ReportView, TEMPLATE_CATALOG, TEMPLATE_REGISTRY, deltaWords, shownDelta, tileNumber } from "@/components/genUITemplates";
import type { ChatMessage } from "@/hooks/useAgentChat";
import { GenUiFileContext } from "@/lib/genUiFiles";
import { REPORT_KIND, parseReportFile, reportFrom } from "@/lib/report";
import { SidePanelFitContext } from "@/lib/sidePanelFit";

const h = rawElement as unknown as (type: unknown, props: unknown, ...children: unknown[]) => ReactElement;

/** A realistic report: 4 KPIs, a summary, and every visual kind. */
const FULL = {
  kind: REPORT_KIND,
  version: 1,
  path: "outputs/q3-review.report.json",
  pdf: "outputs/q3-review.pdf",
  title: "Q3 business review",
  subtitle: "Sales, delivery and support",
  for: "The leadership team",
  date: "16 Oct 2026",
  period: "Jul to Sep 2026",
  summary: "**Revenue grew 12%.** Delivery held.\n- Pipeline is up\n- Churn is down",
  kpis: [
    { label: "Revenue", value: "4.2", unit: "Cr", delta: "+12%", good: "up" },
    { label: "Deals won", value: 38, delta: "+6" },
    { label: "Churn", value: "2.1%", delta: "-0.4%", good: "down" },
    { label: "NPS", value: 61 },
  ],
  sections: [
    { heading: "Revenue by month", text: "Each month beat the plan.", visual: { kind: "chart", type: "bar", title: "Revenue", labels: ["Jul", "Aug", "Sep"], values: [1.2, 1.4, 1.6], unit: "Cr" } },
    { heading: "Headline numbers", visual: { kind: "stats", tiles: [{ label: "Open deals", value: 54, delta: "+4" }] } },
    { heading: "Top deals", visual: { kind: "table", title: "Top deals", columns: ["Title", "Owner", "Value"], rows: [["Acme", "Asha", "40L"], ["Globex", "Ravi", "22L"]] } },
    { heading: "Delivery board", visual: { kind: "board", columns: [{ name: "Doing", cards: [{ title: "Rollout", meta: "Asha" }] }] } },
    { heading: "Milestones", visual: { kind: "timeline", events: [{ date: "2026-07-01", title: "Kickoff", state: "done" }, { date: "2026-09-30", title: "Launch", state: "late" }] } },
    { heading: "Schedule", visual: { kind: "gantt", rows: [{ label: "Build", start: "2026-07-01", end: "2026-08-15" }] } },
    { heading: "Review day", visual: { kind: "agenda", items: [{ start: "09:30", end: "10:00", title: "Standup", detail: "Room 2" }] } },
    { heading: "Risks", bullets: ["**Hiring** is slow", "One vendor is late"] },
  ],
  sources: ["CRM pipeline, 15 Oct", "Projects board"],
};

const full = (data: unknown) => renderToStaticMarkup(h(ReportView, { report: reportFrom(data), pdfUrl: "/pdf" }));

describe("the full report", () => {
  const html = full(FULL);

  it("draws the header, the KPI tiles, the summary, each section and the sources", () => {
    expect(html).toContain('data-report-view="full"');
    expect(html).toMatch(/<h2[^>]*>Q3 business review<\/h2>/);
    expect(html).toContain("Sales, delivery and support");
    expect(html).toContain("For The leadership team · 16 Oct 2026 · Jul to Sep 2026");
    for (const k of ["Revenue", "Deals won", "Churn", "NPS"]) expect(html).toContain(k);
    expect(html).toContain("data-report-summary");
    expect(html).toContain("<strong");
    expect(html.match(/data-report-section=""/g)).toHaveLength(8);
    expect(html).toContain("CRM pipeline, 15 Oct");
    expect(html).toContain("Download PDF");
  });

  it("draws each visual kind through the template of its kind", () => {
    const want: Record<string, string> = {
      chart: "chart", stats: "statDashboard", table: "dataGrid", board: "taskBoard",
      timeline: "timeline", gantt: "chart", agenda: "dataGrid",
    };
    for (const [kind, template] of Object.entries(want)) {
      expect(html, kind).toContain(`data-report-visual="${kind}" data-template="${template}"`);
    }
    expect(html).toContain("data-board-card");
    expect(html).toContain("data-timeline-scroll");
    expect(html).toContain("Rollout");
    expect(html).toContain("Standup");
    expect(html).not.toContain(REPORT_VISUAL_MISSING);
  });

  it("an unknown kind shows a note, and the rest still draws", () => {
    const out = full({ ...FULL, sections: [{ heading: "Map", visual: { kind: "map", points: [] } }, { heading: "After" }] });
    expect(out).toContain(REPORT_VISUAL_MISSING);
    expect(out).toContain('role="note"');
    expect(out).toContain("After");
  });

  it("a minimal report draws, and no missing field blanks it", () => {
    const out = full({ title: "Only a title", sections: [{ text: "One line." }] });
    expect(out).toContain("Only a title");
    expect(out).toContain("One line.");
    expect(out).not.toContain("data-report-sources");
    expect(out).not.toContain("data-report-summary");
    for (const raw of [{}, null, { sections: "x", kpis: 3, sources: {} }]) {
      expect(full(raw), JSON.stringify(raw)).toContain(">Report</h2>");
    }
  });

  it("a bad shape inside a visual draws, never throws", () => {
    const out = full({
      title: "T",
      sections: [
        { visual: { kind: "table", columns: "x", rows: [{ a: 1 }, 7] } },
        { visual: { kind: "board", columns: [null, { cards: "x" }] } },
        { visual: { kind: "timeline", events: "x" } },
        { visual: { kind: "stats", tiles: [null] } },
        { visual: { kind: "chart", type: "nope" } },
      ],
    });
    expect(out.match(/data-report-visual=/g)).toHaveLength(5);
  });

  it("with no PDF, no download", () => {
    expect(renderToStaticMarkup(h(ReportView, { report: reportFrom({ title: "T" }) }))).not.toContain("Download PDF");
  });
});

describe("the chat card", () => {
  const card = (files: unknown) =>
    renderToStaticMarkup(h(GenUiFileContext.Provider, { value: files }, TEMPLATE_REGISTRY.report(FULL)));

  it("is a catalog template", () => {
    expect(TEMPLATE_CATALOG.some((t) => t.name === "report")).toBe(true);
  });

  it("draws the short form: tiles, a clipped summary, the first visual and the contents", () => {
    const html = card({ sessionId: "s1", open: () => {} });
    expect(html).toContain('data-report-view="card"');
    expect(html).toContain("Revenue grew 12%. Delivery held. Pipeline is up · Churn is down");
    expect(html.match(/data-report-visual=/g)).toHaveLength(1);
    expect(html).toContain('data-report-visual="chart"');
    expect(html).toContain("In this report");
    expect(html).toContain("Milestones");
    expect(html).not.toContain("data-report-sources");
  });

  it("the card offers Open report and the PDF when the chat can open the file", () => {
    const html = card({ sessionId: "s1", open: () => {} });
    expect(html).toContain("Open report");
    expect(html).toContain("Download PDF");
    expect(html).toContain('title="Download q3-review.pdf"');
  });

  it("with no host, the card still draws, with no act", () => {
    const html = card(null);
    expect(html).toContain("Q3 business review");
    expect(html).not.toContain("Open report");
    expect(html).not.toContain("Download PDF");
  });

  it("a long summary is cut at a word", () => {
    const long = "Word ".repeat(200);
    const html = renderToStaticMarkup(TEMPLATE_REGISTRY.report({ ...FULL, summary: long }));
    expect(html).toMatch(/Word…<\/p>/);
  });
});

describe("the chat transcript hands the card a way to open the report", () => {
  const message = {
    id: "m1",
    role: "assistant",
    content: "Here is the Q3 review.",
    timestamp: new Date("2026-10-16T10:00:00Z").getTime(),
    customEvents: [{ name: "generative_ui", value: { type: "template", props: { name: "report", data: FULL } } }],
  } as unknown as ChatMessage;

  const bubble = (fits: boolean, onFileOpen?: () => void) =>
    renderToStaticMarkup(
      h(SidePanelFitContext.Provider, { value: fits }, h(MessageBubble, { message, sessionId: "s1", onFileOpen })),
    );

  it("offers Open report in the side panel, and in the viewer where it does not fit", () => {
    expect(bubble(true)).toContain("Open report");
    expect(bubble(false, () => {})).toContain("Open report");
  });

  it("offers no Open report with no place to open it", () => {
    expect(bubble(false)).not.toContain("Open report");
  });
});

describe("the stat tile", () => {
  it("keeps a value's words unless it is a plain number", () => {
    expect(tileNumber(38)).toBe(38);
    expect(tileNumber("4.2")).toBe(4.2);
    for (const v of ["2.1%", "4.2L", "1,234", "", null]) expect(tileNumber(v), String(v)).toBeNull();
  });

  it("reads a text change, its sign and its words", () => {
    expect(shownDelta({ value: 9, delta: "-0.4%" })).toBe(-0.4);
    expect(shownDelta({ value: 9, delta: "− 3" })).toBe(-3);
    expect(shownDelta({ value: 9, delta: "" })).toBeNull();
    expect(deltaWords({ delta: "+12%" })).toBe("12%");
    expect(deltaWords({ delta: "-3" })).toBe("");
    expect(deltaWords({ delta: 3 })).toBe("");
  });

  it("a fall is green when down is good", () => {
    const html = renderToStaticMarkup(TEMPLATE_REGISTRY.statDashboard({
      stats: [{ label: "Churn", value: "2%", delta: "-0.4%", good: "down" }, { label: "Cost", value: "9", delta: "+1" , good: "down" }],
    }));
    const [churn, cost] = html.split("Cost");
    expect(churn).toContain("var(--success)");
    expect(cost).toContain("var(--destructive)");
    expect(churn).toContain("0.4%");
  });
});

describe("a report file", () => {
  it("the file card downloads the PDF beside it, by its own name", () => {
    expect(cardPdf("s1", { name: "q3.report.json", path: "outputs/q3.report.json" })).toEqual({
      url: "/api/agent/workspace/s1/file?path=outputs%2Fq3.pdf",
      name: "q3.pdf",
    });
    // A document still converts on the gateway.
    expect(cardPdf("s1", { name: "a.md", path: "outputs/a.md" })?.url).toBe("/api/agent/workspace/s1/file?path=outputs%2Fa.md&format=pdf");
    expect(cardPdf("s1", { name: "a.json", path: "outputs/a.json" })).toBeNull();
  });

  it("a bad file parses to nothing, which the viewers show as the raw file", () => {
    expect(parseReportFile("{ not json")).toBeNull();
  });

  it("the side panel and the viewer draw a report through ReportView, and the raw file when it does not parse", () => {
    const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");
    const pane = read("./DocumentPane.tsx");
    expect(pane).toContain("parseReportFile(content, path)");
    expect(pane).toMatch(/kind === "report"\) \{\s*body = report \? \(/);
    expect(pane).toContain("<ReportView report={report}");
    const modal = read("./ArtifactViewerModal.tsx");
    expect(modal).toContain("const report = parseReportFile(text, entry.path);");
    expect(modal).toContain('{ status: "text", content: text, lang: "json" }');
    expect(modal).toContain("<ReportView report={state.report}");
    expect(modal).toContain("siblingFileUrl(externalDownloadUrl, report.pdf)");
  });
});
