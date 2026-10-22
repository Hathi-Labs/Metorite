// ── Word to HTML (WS-61 KB-AI-1) ─────────────────────────────────────────────
//
// `mammoth` turns the `.docx` into HTML, and this file only chooses how. The
// result is NOT trusted: `html.ts` (`htmlToMarkdown`) parses it in an inert
// document, then the one policy of `src/lib/untrustedHtml.ts` cleans it. The
// HTML never reaches the live page.
//
// The style map makes Heading 1 to 6 become `h1` to `h6`, and a Title
// paragraph become an `h1`, so the first of them is the page title. Lists and
// tables stay, as mammoth keeps them. An embedded picture is not read. It
// becomes an `img` with its alt text and no source, and `htmlToMarkdown`
// turns that into italic text. Attachments wait for KB-2c.

import { DocumentRefusal, listParts } from "./ooxml";

/** The style map: names, as Word writes them. Mammoth's own defaults stay on. */
export const DOCX_STYLE_MAP: readonly string[] = [
  "p[style-name='Title'] => h1:fresh",
  "p[style-name='Heading 1'] => h1:fresh",
  "p[style-name='Heading 2'] => h2:fresh",
  "p[style-name='Heading 3'] => h3:fresh",
  "p[style-name='Heading 4'] => h4:fresh",
  "p[style-name='Heading 5'] => h5:fresh",
  "p[style-name='Heading 6'] => h6:fresh",
];

/** A `.docx` as untrusted HTML. Browser only. */
export async function docxToHtml(data: Uint8Array): Promise<string> {
  // mammoth inflates the zip itself, so the declared sizes are checked first.
  await listParts(data);
  const mammoth = await import("mammoth");
  const buffer = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer;
  try {
    const result = await mammoth.convertToHtml(
      { arrayBuffer: buffer },
      {
        styleMap: [...DOCX_STYLE_MAP],
        convertImage: // Mammoth adds the alt text itself. The source is a placeholder
        // that is never fetched, so no picture is read or embedded.
        mammoth.images.imgElement(async () => ({ src: "image" })),
      },
    );
    return result.value;
  } catch {
    throw new DocumentRefusal("This Word file cannot be read. It may be damaged or not a .docx file.");
  }
}
