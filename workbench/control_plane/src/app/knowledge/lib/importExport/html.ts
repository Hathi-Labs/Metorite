// ── HTML in and out, through the editor's own schema (WS-61 KB-2m) ───────────
//
// Browser only: it needs a DOM. The dialogs load this file with a dynamic
// import, and the pure files here never import it.
//
// IN. The HTML is parsed in an inert document (`DOMParser`, never the live
// page), its images become text, then the ONE policy of `src/lib/untrustedHtml.ts`
// cleans it. `generateJSON` reads it with `knowledgeExtensions`, and
// `writeBody` writes what the editor writes, so the stored form is the same as
// a page typed by hand.
//
// OUT. `generateHTML` draws a body with the same schema, and the file policy
// of `untrustedHtml.ts` removes anything that runs.

import { generateHTML, generateJSON } from "@tiptap/core";

import { sanitizeDocxHtml, sanitizeFileHtml } from "@/lib/untrustedHtml";

import { knowledgeExtensions } from "../editor/schema";
import { parseBody, wrapInlineRuns, writeBody } from "../editor/markdown";
import { imageName } from "./names";
import type { HtmlResult } from "./plan";

/** HTML as a title and a Markdown body. */
export function htmlToMarkdown(html: string): HtmlResult {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const said = doc.querySelector("title")?.textContent?.replace(/\s+/g, " ").trim() ?? "";
  let images = 0;
  for (const img of Array.from(doc.body.querySelectorAll("img"))) {
    images += 1;
    const alt = (img.getAttribute("alt") ?? "").replace(/\s+/g, " ").trim();
    const em = doc.createElement("em");
    em.textContent = alt || `[image: ${imageName(img.getAttribute("src") ?? "")}]`;
    img.replaceWith(em);
  }
  // The title is `<title>`, else the first `<h1>`. The `<h1>` leaves the body
  // when it is the source, or when it repeats `<title>` (an export writes both).
  const h1 = doc.body.querySelector("h1");
  let title: string | null = said || null;
  if (h1) {
    const text = (h1.textContent ?? "").replace(/\s+/g, " ").trim();
    if (!said) {
      title = text || null;
      h1.remove();
    } else if (text === said) h1.remove();
  }
  const clean = sanitizeDocxHtml(doc.body.innerHTML).html;
  const json = generateJSON(clean, knowledgeExtensions());
  return { title, markdown: writeBody(wrapInlineRuns(json)), images };
}

/** A Markdown body as HTML for an exported file. */
export function markdownToHtml(md: string): string {
  return sanitizeFileHtml(generateHTML(parseBody(md), knowledgeExtensions()));
}
