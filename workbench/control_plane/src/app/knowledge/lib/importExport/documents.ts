// ── Documents as pages (WS-61 KB-AI-1) ───────────────────────────────────────
//
// The one door for a Word, PDF, PowerPoint or Excel file. Each kind has its
// own module, and each loads on first use, so Knowledge pays nothing for them
// until a member imports one. A file that cannot make a page throws
// `DocumentRefusal` with words for the member. `prepare.ts` reports it for
// that file and goes on with the others.

import type { DocumentKind } from "./names";
import { DocumentRefusal } from "./ooxml";
import type { HtmlConverter } from "./plan";

export interface DocumentResult {
  /** The title the document says, or `null` for the file name. */
  title: string | null;
  markdown: string;
  /** Images turned into text. */
  images: number;
  notices: string[];
}

export const DATE_NOTICE = "Dates in Excel files stay as the numbers that Excel stores. They are not converted.";

export async function convertDocument(kind: DocumentKind, data: Uint8Array, html: HtmlConverter): Promise<DocumentResult> {
  let out: DocumentResult;
  switch (kind) {
    case "docx": {
      const { docxToHtml } = await import("./docx");
      const r = html(await docxToHtml(data));
      out = { title: r.title, markdown: r.markdown, images: r.images, notices: [] };
      break;
    }
    case "pdf": {
      const { pdfToMarkdown } = await import("./pdf");
      const r = await pdfToMarkdown(data);
      out = { title: r.title, markdown: r.markdown, images: 0, notices: r.notices };
      break;
    }
    case "pptx": {
      const { pptxToMarkdown } = await import("./pptx");
      const r = await pptxToMarkdown(data);
      out = { title: null, markdown: r.markdown, images: 0, notices: r.notices };
      break;
    }
    case "xlsx": {
      const { xlsxToMarkdown } = await import("./xlsx");
      const r = await xlsxToMarkdown(data);
      out = { title: null, markdown: r.markdown, images: 0, notices: [...r.notices, DATE_NOTICE] };
      break;
    }
  }
  if (!out.markdown.trim() && !out.title) throw new DocumentRefusal("This file holds no text.");
  return out;
}
