/**
 * How a workspace file should be PRESENTED — one definition, shared by every
 * viewer.
 *
 * This used to live twice: once in DocumentPane (the desktop side panel) and
 * once in ArtifactViewerModal (the file viewer, and the ONLY artifact viewer on
 * mobile). They drifted. The panel learned to render `.html` and `.jsx`; the
 * modal kept classifying them as code because `html`/`jsx`/`tsx` also appear in
 * its CODE_EXTS list. Net effect: a full-page artifact rendered on desktop and
 * showed as syntax-highlighted source on a phone.
 *
 * Hence one function. A viewer may still decline to handle a kind (DocumentPane
 * has no PDF renderer and treats it as an undisplayable file), but the *naming*
 * of what a file is no longer depends on which viewer you opened it in.
 */

export type ArtifactKind =
  | "markdown"
  | "html"
  | "react"
  | "code"
  | "image"
  | "pdf"
  | "docx"
  | "text"
  | "binary";

const CODE_EXTS = new Set([
  "py", "ts", "tsx", "js", "jsx", "sh", "bash", "zsh", "fish",
  "yaml", "yml", "toml", "json", "sql", "rs", "go", "java", "c",
  "cpp", "cs", "rb", "php", "swift", "kt", "scala", "r", "lua",
  "html", "css", "scss", "less", "xml", "graphql", "proto", "csv", "tsv",
]);

const IMAGE_EXTS = new Set([
  "png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp", "tiff",
]);

const TEXT_EXTS = new Set([
  "txt", "log", "csv", "tsv", "rst", "ini", "cfg", "conf", "env",
]);

const DOCX_MIME =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

/**
 * The Markdown and HTML extensions. WS-27bm S8: together they are exactly the
 * gateway's `pdf_render.SOURCE_KINDS`, the files it converts to a PDF.
 * `artifactKind.test.ts` reads that Python dict and fails if the two differ.
 */
export const MARKDOWN_EXTS: readonly string[] = ["md", "markdown", "mdx"];
export const HTML_EXTS: readonly string[] = ["html", "htm"];

export function extOf(name: string): string {
  return (name.split(".").pop() ?? "").toLowerCase();
}

/**
 * A `.jsx`/`.tsx` is a runnable React ARTIFACT only under `outputs/`, where
 * write_artifact puts generated deliverables. Everywhere else those extensions
 * are ordinary source an agent may be editing — opening one to read it must
 * never turn into building and running it.
 */
export function isArtifactPath(path: string): boolean {
  return path.replace(/^\/+/, "").startsWith("outputs/");
}

/**
 * Classify a workspace file for display.
 *
 * `html` and `react` are tested BEFORE the code-extension set, which also
 * contains html/jsx/tsx — that ordering is the whole fix, and reversing it
 * silently turns every artifact back into a wall of source.
 */
export function classifyArtifact(
  name: string,
  path: string,
  mimeType = "",
): ArtifactKind {
  const ext = extOf(name);
  if (MARKDOWN_EXTS.includes(ext)) return "markdown";
  if (HTML_EXTS.includes(ext)) return "html";
  if ((ext === "jsx" || ext === "tsx") && isArtifactPath(path)) return "react";
  if (ext === "pdf" || mimeType === "application/pdf") return "pdf";
  if (ext === "docx" || mimeType === DOCX_MIME) return "docx";
  if (CODE_EXTS.has(ext)) return "code";
  if (IMAGE_EXTS.has(ext)) return "image";
  if (TEXT_EXTS.has(ext) || mimeType.startsWith("text/")) return "text";
  return "binary";
}

/** Kinds with a rendered view distinct from their source. */
export function isRenderable(kind: ArtifactKind): boolean {
  return kind === "html" || kind === "react";
}

/**
 * WS-27bm S8 — which kinds the gateway can turn into a PDF. The gateway's
 * `pdf_render.SOURCE_KINDS` holds the same list by extension (md, markdown,
 * mdx, html, htm), and refuses any other file with a 415.
 */
export function canDownloadPdf(kind: ArtifactKind): boolean {
  return kind === "markdown" || kind === "html";
}

/**
 * The one address of a workspace file, raw or as a PDF. Every viewer builds
 * its links here, so the PDF link and the raw link cannot name two files.
 */
export function workspaceFileUrl(
  sessionId: string,
  path: string,
  opts: { pdf?: boolean } = {},
): string {
  const base = `/api/agent/workspace/${sessionId}/file?path=${encodeURIComponent(path)}`;
  return opts.pdf ? `${base}&format=pdf` : base;
}

/** `status.md` → `status.pdf`, the name the gateway also sends. */
export function pdfNameFor(name: string): string {
  const dot = name.lastIndexOf(".");
  return `${dot > 0 ? name.slice(0, dot) : name || "document"}.pdf`;
}
