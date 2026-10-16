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
  | "report"
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

/**
 * A report the agent made (`make_report`, owner directive 2026-10-16). It is
 * JSON, so the code list below would take it. It is tested first, by its
 * whole ending, and every viewer draws it with `ReportView`.
 *
 * ⚠️ Only a WORKSPACE output is a report: the host says `origin`
 * "workspace", and the path is under `outputs/` (`isArtifactPath`). The
 * name decides nothing on its own. A sender may name an email attachment
 * `outputs/q3.report.json`, because the mail providers keep the raw file
 * name, so an attachment with that name and a metorite.report body stays
 * "code" (re-review of #1065). An upload under `inputs/` stays "code" too.
 * A file under `outputs/` that is not a Metorite report (a Lighthouse
 * `site.report.json`) fails `parseReportFile`, and the viewers show it as
 * JSON, which a member can still edit.
 */
export const REPORT_SUFFIX = ".report.json";

/**
 * Where a file comes from, as its HOST says, never as its name says. A
 * "workspace" file is an agent's session workspace or the artifacts
 * registry. Anything else ("other") is outside data: an email attachment, a
 * read-only file behind an external URL. The default is "other", so a host
 * that forgets to say draws no report (re-review of #1065).
 */
export type FileOrigin = "workspace" | "other";

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
  origin: FileOrigin = "other",
): ArtifactKind {
  const ext = extOf(name);
  if (origin === "workspace" && name.toLowerCase().endsWith(REPORT_SUFFIX) && isArtifactPath(path)) return "report";
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

/**
 * The archive extensions and types (2026-10-11, `package_artifacts`). An
 * archive classifies as "binary", and it has NO preview: a viewer shows a
 * download, never a hex dump of the bytes. The gateway serves `.zip` as
 * `application/zip`, and a Windows box may name it `x-zip-compressed`.
 */
const ARCHIVE_EXTS = new Set(["zip"]);
const ARCHIVE_MIMES = new Set(["application/zip", "application/x-zip-compressed"]);

export function isArchive(name: string, mimeType = ""): boolean {
  return ARCHIVE_EXTS.has(extOf(name)) || ARCHIVE_MIMES.has(mimeType);
}

/** Kinds with a rendered view distinct from their source. */
export function isRenderable(kind: ArtifactKind): boolean {
  return kind === "html" || kind === "react";
}

/**
 * Kinds that open RENDERED by a click and by themselves: a document, a
 * full-page artifact and a report. Anything else stays a file card. The file
 * card, the auto-open rule and the chat's report card all ask this.
 */
export function opensRendered(kind: ArtifactKind): boolean {
  return kind === "markdown" || kind === "report" || isRenderable(kind);
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
 *
 * `download` marks the address of a Download button (owner report,
 * 2026-10-16). The proxy route sends the raw bytes for it, and never the
 * rendered page of {@link rendersAsPage}.
 */
export function workspaceFileUrl(
  sessionId: string,
  path: string,
  opts: { pdf?: boolean; download?: boolean } = {},
): string {
  const base = `/api/agent/workspace/${sessionId}/file?path=${encodeURIComponent(path)}`;
  if (opts.pdf) return `${base}&format=pdf`;
  return opts.download ? `${base}&download=1` : base;
}

// ─── A link to a workspace file (owner report, 2026-10-16) ────────────────
//
// The owner asked for a report. The agent wrote a Markdown file and pasted
// its `download_url` into the reply. The chat opened that link in a new tab,
// and the browser showed the raw Markdown. So a link to a workspace file now
// opens the app's own viewer, and a direct open of a Markdown address goes to
// the rendered view. Fences: `artifactLink.test.ts` and
// `artifactLinkRoute.test.ts`.

/** The proxy route of one workspace file. */
const FILE_ROUTE = /^\/api\/agent\/workspace\/([^/]+)\/file$/;
/** A session id: a UUID or a slug. Never a dot, a slash or a space. */
const SESSION_ID = /^[A-Za-z0-9_-]+$/;
/** A backslash, or a control mark such as a tab, CR or LF. */
const OFF_PATH_MARKS = /[\\\u0000-\u001f\u007f]/;
/** The origin a link is read against on the server, where no page exists. */
const SERVER_ORIGIN = "https://in-app.invalid";

/** The session and the path that a workspace file link names. */
export interface WorkspaceFileRef {
  sessionId: string;
  path: string;
  name: string;
}

/**
 * The workspace file that `href` names, else `null`.
 *
 * It reads the address that {@link workspaceFileUrl} builds, and the
 * `download_url` that the agent tools return. That is the same address with
 * the path not encoded. A PDF link (`format=pdf`) is not a file to view, so it
 * gives `null`. A link to another origin gives `null` too. So do a backslash
 * and a control mark, which each URL parser reads in its own way.
 *
 * `origin` is the app's origin. It defaults to the page's own.
 */
export function workspaceFileOfHref(
  href: string | undefined,
  origin?: string,
): WorkspaceFileRef | null {
  if (!href || OFF_PATH_MARKS.test(href)) return null;
  const base = origin
    ?? (typeof window !== "undefined" ? window.location.origin : SERVER_ORIGIN);
  let url: URL;
  try {
    url = new URL(href, base);
  } catch {
    return null;
  }
  if (url.origin !== new URL(base).origin) return null;
  const match = FILE_ROUTE.exec(url.pathname);
  if (!match) return null;
  let sessionId: string;
  try {
    sessionId = decodeURIComponent(match[1]);
  } catch {
    return null;
  }
  if (!SESSION_ID.test(sessionId)) return null;
  const path = url.searchParams.get("path");
  if (!path || url.searchParams.has("format")) return null;
  return { sessionId, path, name: path.split("/").pop() || path };
}

/**
 * The address of the rendered view of one workspace file: the Artifacts app,
 * with the file open in its viewer. The proxy route sends a direct open of a
 * Markdown file here, and a chat link carries it as its `href`.
 */
export function artifactViewUrl(sessionId: string, path: string): string {
  return `/artifacts?session=${encodeURIComponent(sessionId)}&file=${encodeURIComponent(path)}`;
}

/** The request facts that {@link rendersAsPage} reads. */
export interface FileRequestFacts {
  /** The `Sec-Fetch-Dest` header, or `null` when the browser sends none. */
  dest: string | null;
  /** The `Accept` header. */
  accept: string | null;
  /** True when the address carries `download`, from a Download button. */
  download: boolean;
  /** The `format` value, such as `pdf`. */
  format: string | null;
}

/**
 * True when a GET of a workspace file is a member who opens the address
 * itself, in a tab, and the file is Markdown. The proxy route then sends the
 * rendered view ({@link artifactViewUrl}) and not the raw text.
 *
 * A viewer's `fetch`, an `<img>` and a Download button each get the raw
 * bytes. A fetch sends `Sec-Fetch-Dest: empty`, and a Download button names
 * `download`. Only a Markdown file changes. An HTML file keeps its old answer,
 * so this adds nothing to HANDOFF H-238. A browser that sends no
 * `Sec-Fetch-Dest` is read by its `Accept` header.
 */
export function rendersAsPage(path: string, req: FileRequestFacts): boolean {
  if (req.download || req.format) return false;
  const name = path.split("/").pop() ?? path;
  if (classifyArtifact(name, path) !== "markdown") return false;
  if (req.dest) return req.dest === "document";
  return (req.accept ?? "").includes("text/html");
}

/**
 * True when a file has a rendered view worth opening: Markdown, HTML, a
 * React artifact or a Metorite report. Moved here from `ArtifactCard` on
 * 2026-10-16, so the card, a link in the chat text and auto-open read one
 * rule. Each of them names a chat's workspace file, so the origin is
 * "workspace": a report draws as a report only there.
 */
export function hasRenderedView(name: string, path: string): boolean {
  return opensRendered(classifyArtifact(name, path, "", "workspace"));
}

/** Where a chat opens a file: the side panel or the full viewer. */
export type ArtifactSurface = "panel" | "modal";

/**
 * Where a click on a chat file opens it. A document goes to the side panel
 * on a desktop, when the chat has a panel that fits. A phone has no side
 * panel, and every other kind of file has no panel view, so those open in
 * the full viewer (`ArtifactViewerModal`). The file card and a file link in
 * the reply both read this one rule.
 */
export function artifactOpenSurface(
  name: string,
  path: string,
  opts: { isMobile: boolean; panel: boolean },
): ArtifactSurface {
  return hasRenderedView(name, path) && !opts.isMobile && opts.panel ? "panel" : "modal";
}

/** `status.md` → `status.pdf`, the name the gateway also sends. */
export function pdfNameFor(name: string): string {
  const dot = name.lastIndexOf(".");
  return `${dot > 0 ? name.slice(0, dot) : name || "document"}.pdf`;
}
