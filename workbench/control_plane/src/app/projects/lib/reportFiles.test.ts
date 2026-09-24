/**
 * WS-27bm S8 — a saved report as a Markdown or PDF file (spec
 * `projects_ai_chat.md` §14).
 *
 * `downloadReportFile` takes its browser calls as arguments, so every step is
 * driven here: the render, the one formatter, the PDF request and the save.
 * The last block reads the two surfaces' source: both draw the one button
 * component, and the chat's card hands it the card's own `reportId`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";

import type { RenderedReport } from "@/lib/reportEmail";

import {
  REPORT_FILE_ACTIONS,
  downloadReportFile,
  pdfEndpoint,
  reportFileActions,
} from "./reportFiles";

const ID = "3f1c2a9e-7b4d-4e1a-9c2b-1d2e3f4a5b6c";

const rendered: RenderedReport = {
  report: { name: "Weekly delivery", scope: "portfolio" },
  period_start: "2026-09-14",
  period_end: "2026-09-20",
  sections: {
    finished: {
      projects: [{ name: "Apollo", completed: 4, cancelled: 0 }],
      total_completed: 4,
      total_cancelled: 0,
    },
  },
};

function deps(res?: Response) {
  const saved: { body: Blob; filename: string }[] = [];
  return {
    render: vi.fn(async () => rendered),
    fetchImpl: vi.fn(async () => res ?? new Response("%PDF-1.7 bytes", { status: 200 })),
    save: vi.fn((body: Blob, filename: string) => {
      saved.push({ body, filename });
    }),
    saved,
  };
}

describe("reportFileActions", () => {
  it("offers Download then Download PDF for a real report id", () => {
    expect(reportFileActions(ID).map((a) => [a.format, a.label])).toEqual([
      ["md", "Download"],
      ["pdf", "Download PDF"],
    ]);
    expect(reportFileActions(ID)).toBe(REPORT_FILE_ACTIONS);
  });

  it("offers nothing for a card without a real id", () => {
    for (const bad of [undefined, null, "", "weekly", 42, `${ID}/../x`]) {
      expect(reportFileActions(bad), String(bad)).toEqual([]);
    }
  });
});

describe("downloadReportFile", () => {
  it("saves the formatted Markdown, rendered now", async () => {
    const d = deps();
    const name = await downloadReportFile(ID, "md", d);
    expect(d.render).toHaveBeenCalledWith(ID);
    expect(d.fetchImpl).not.toHaveBeenCalled();
    expect(name).toBe("Weekly delivery 2026-09-14 to 2026-09-20.md");
    expect(d.saved[0].filename).toBe(name);
    expect(d.saved[0].body.type).toContain("text/markdown");
    const text = await d.saved[0].body.text();
    expect(text.startsWith("# Weekly delivery\n")).toBe(true);
    expect(text).toContain("- Apollo: 4");
  });

  it("posts the formatted HTML as text/html and saves the PDF bytes", async () => {
    const d = deps();
    const name = await downloadReportFile(ID, "pdf", d);
    expect(name).toBe("Weekly delivery 2026-09-14 to 2026-09-20.pdf");
    const [url, init] = d.fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(pdfEndpoint(name));
    expect(url.startsWith("/api/documents/pdf?filename=")).toBe(true);
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe(
      "text/html; charset=utf-8",
    );
    expect(String(init.body)).toContain("<h2>Weekly delivery</h2>");
    expect(String(init.body)).toContain("<li>Apollo: 4</li>");
    expect(await d.saved[0].body.text()).toBe("%PDF-1.7 bytes");
  });

  it("says the gateway's reason when the PDF is refused, and saves nothing", async () => {
    const d = deps(
      new Response(JSON.stringify({ detail: "The document is too large for a PDF." }), {
        status: 413,
        headers: { "content-type": "application/json" },
      }),
    );
    await expect(downloadReportFile(ID, "pdf", d)).rejects.toThrow(
      "The document is too large for a PDF.",
    );
    expect(d.save).not.toHaveBeenCalled();
  });

  it("names the status when the refusal is not JSON", async () => {
    const d = deps(new Response("Bad Gateway", { status: 502 }));
    await expect(downloadReportFile(ID, "pdf", d)).rejects.toThrow("HTTP 502");
  });
});

const read = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

describe("the two surfaces", () => {
  it("the chat's reportCard hands the card's reportId to the buttons", () => {
    const src = read("../../../components/genUITemplates.tsx");
    const card = src.slice(src.indexOf("function ReportCard("));
    const body = card.slice(0, card.indexOf("\n}\n"));
    expect(body).toContain("<ReportFileButtons reportId={data.reportId} />");
  });

  it("the Reports app draws the same buttons for the selected report", () => {
    expect(read("../components/ReportsView.tsx")).toContain(
      "<ReportFileButtons reportId={selected} />",
    );
  });

  it("the buttons call the one download path and no formatter of their own", () => {
    const src = read("../components/ReportFileButtons.tsx");
    expect(src).toContain("downloadReportFile(");
    expect(src).toContain("reportFileActions(reportId)");
    expect(src).not.toContain("reportDocument");
  });
});
