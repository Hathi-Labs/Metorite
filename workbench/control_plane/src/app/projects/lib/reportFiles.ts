/**
 * A saved report as a FILE — Markdown or PDF (WS-27bm S8, spec
 * `projects_ai_chat.md` §14).
 *
 * Two surfaces offer the same two buttons: the Reports app's rendered body and
 * the chat's `reportCard`. Both call this module, so the buttons, the file
 * names and the path to the numbers are one decision.
 *
 * The path is fixed and it has three steps:
 *
 * 1. The numbers come from `GET /projects/reports/{id}/render`, which re-runs
 *    the Reports app's own SQL. Nothing here computes.
 * 2. `reportDocument` in `lib/reportEmail.ts` formats them. That file is the
 *    ONE formatter: the email, the Markdown and the PDF are its three drawings
 *    of one layout.
 * 3. The PDF is the gateway's `POST /documents/pdf`, which lays out that HTML
 *    through `gateway/pdf_render.py` and formats nothing of its own.
 *
 * The browser calls are injected so a node-env test drives every decision
 * (`reportFiles.test.ts`). `saveBlob` needs a DOM and is the one untested line.
 */
import { saveBlob } from "@/lib/export";
import { type RenderedReport, reportDocument } from "@/lib/reportEmail";

import { projectsApi } from "./api";

export type ReportFileFormat = "md" | "pdf";

export interface ReportFileAction {
  format: ReportFileFormat;
  label: string;
  /** An `Icon` name, which `Button`'s `icon` prop takes. */
  icon: string;
  title: string;
}

/** The two buttons, in order. One vocabulary for both surfaces. */
export const REPORT_FILE_ACTIONS: readonly ReportFileAction[] = [
  {
    format: "md",
    label: "Download",
    icon: "Download",
    title: "Download this report as a Markdown file",
  },
  {
    format: "pdf",
    label: "Download PDF",
    icon: "FileDown",
    title: "Download this report as a PDF",
  },
];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * The buttons a surface draws for a report id, or none.
 *
 * The chat's card takes its `reportId` from a model's tool call, so the value
 * is checked here: a card with no id, or with an id that is not one, draws no
 * button rather than one that fails on a click.
 */
export function reportFileActions(reportId: unknown): readonly ReportFileAction[] {
  return typeof reportId === "string" && UUID.test(reportId)
    ? REPORT_FILE_ACTIONS
    : [];
}

export interface ReportFileDeps {
  render: (id: string) => Promise<RenderedReport>;
  fetchImpl: typeof fetch;
  save: (body: Blob, filename: string) => void;
}

const DEFAULT_DEPS: ReportFileDeps = {
  render: (id) => projectsApi.renderReport(id),
  fetchImpl: (...args) => fetch(...args),
  save: saveBlob,
};

/** Where the PDF is made. The Next proxy adds the session. */
export function pdfEndpoint(filename: string): string {
  return `/api/documents/pdf?filename=${encodeURIComponent(filename)}`;
}

async function refusal(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  try {
    const body = JSON.parse(text) as { detail?: unknown; error?: unknown };
    const said = body.detail ?? body.error;
    if (typeof said === "string" && said) return said;
  } catch {
    // Not JSON. The status line below says enough.
  }
  return `The PDF could not be made (HTTP ${res.status}).`;
}

/**
 * Render one saved report and hand it to the browser as a file.
 *
 * ⚠️ It renders NOW, every time. A file made from a cached render would carry
 * yesterday's numbers under today's name.
 */
export async function downloadReportFile(
  reportId: string,
  format: ReportFileFormat,
  deps: ReportFileDeps = DEFAULT_DEPS,
): Promise<string> {
  const rendered = await deps.render(reportId);
  const doc = reportDocument(rendered);
  if (format === "md") {
    const filename = `${doc.basename}.md`;
    deps.save(new Blob([doc.markdown], { type: "text/markdown;charset=utf-8" }), filename);
    return filename;
  }
  const filename = `${doc.basename}.pdf`;
  const res = await deps.fetchImpl(pdfEndpoint(filename), {
    method: "POST",
    headers: { "Content-Type": "text/html; charset=utf-8" },
    body: doc.html,
  });
  if (!res.ok) throw new Error(await refusal(res));
  // ⚠️ `blob()`, never `text()`: a PDF is binary (lib/export.ts).
  deps.save(await res.blob(), filename);
  return filename;
}
