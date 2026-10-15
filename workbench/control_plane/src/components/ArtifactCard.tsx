"use client";

/**
 * ArtifactCard — inline chat card for agent-generated files (images, markdown,
 * PDFs, CSVs, and other artifacts).
 *
 * Rendered inside the message thread whenever an agent emits an
 * artifact_created / artifact_updated custom event.  Clicking the card opens
 * the ArtifactViewerModal for full fidelity; images render inline as
 * thumbnails; downloadable files show an icon + size + download link.
 *
 * The proxy URL is constructed as:
 *   /api/agent/workspace/{sessionId}/file?path={rel_path}
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { categoricalAccent } from "@/lib/categorical";
import { useCallback, useEffect, useRef, useState } from "react";
import { useViewMode } from "@/components/ViewModeProvider";
import { archiveNotice, fetchArchive, saveBlob } from "@/lib/archiveDownload";
import {
  canDownloadPdf,
  classifyArtifact,
  isArchive,
  opensRendered,
  pdfNameFor,
  workspaceFileUrl,
} from "@/lib/artifactKind";
import { baseName, companionPdfPath } from "@/lib/report";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface ArtifactMeta {
  /** Relative path within the agent workspace, e.g. "outputs/chart.png". */
  path: string;
  /** Display name (basename of path). */
  name: string;
  /** File size in bytes (optional — shown when available). */
  size?: number;
  /** MIME type (optional — used for icon selection and rendering). */
  mimeType?: string;
  /** SHA-256 hex digest from the write_artifact tool (optional). */
  sha256?: string;
  /** The registry row (WS-60 AM-1). Absent for a card older than the registry. */
  artifact_id?: string | null;
}

interface ArtifactCardProps {
  artifact: ArtifactMeta;
  sessionId: string;
  /** Called when the user clicks "open" to view in the full modal. */
  onOpen?: (entry: import("./ArtifactSidebar").FileEntry) => void;
  /** Called to open the file in the side-panel editor (documents: md/html). */
  onOpenInSidePanel?: (entry: import("./ArtifactSidebar").FileEntry) => void;
}

/** Documents get a first-class "Open in side panel" action (editable + live). */
/**
 * Which artifacts have a rendered view worth opening. Derived from the shared
 * classifier rather than its own extension list, so a React artifact
 * (outputs/*.jsx) gets the same treatment as HTML — it previously got no Open
 * button at all, because the hard-coded list here only knew about md/html.
 */
function hasRenderedView(name: string, path: string): boolean {
  return opensRendered(classifyArtifact(name, path));
}

/**
 * The PDF a card offers, and its name. A Markdown or HTML document converts
 * on the gateway. A report (`.report.json`) has its own PDF beside it, which
 * `make_report` wrote, so the card downloads that file as it is.
 */
export function cardPdf(
  sessionId: string,
  artifact: Pick<ArtifactMeta, "name" | "path" | "mimeType">,
): { url: string; name: string } | null {
  const kind = classifyArtifact(artifact.name, artifact.path, artifact.mimeType);
  if (kind === "report") {
    const pdf = companionPdfPath(artifact.path);
    return pdf ? { url: workspaceFileUrl(sessionId, pdf), name: baseName(pdf) } : null;
  }
  return canDownloadPdf(kind)
    ? { url: workspaceFileUrl(sessionId, artifact.path, { pdf: true }), name: pdfNameFor(artifact.name) }
    : null;
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

const IMAGE_EXTS = new Set([
  "png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp", "tiff",
]);

const CODE_EXTS = new Set([
  "py", "ts", "tsx", "js", "jsx", "sh", "bash", "yaml", "yml",
  "toml", "json", "sql", "rs", "go", "java", "c", "cpp", "cs",
  "rb", "php", "swift", "kt", "html", "css", "scss", "xml",
]);

function getExt(name: string): string {
  return (name.split(".").pop() ?? "").toLowerCase();
}

function isImage(artifact: ArtifactMeta): boolean {
  const ext = getExt(artifact.name);
  if (IMAGE_EXTS.has(ext)) return true;
  if (artifact.mimeType?.startsWith("image/")) return true;
  return false;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * The icon of a file, and its hue. A file kind is an IDENTITY, so its hue is
 * a slot of the categorical ramp, picked by the kind's name
 * (`lib/categorical.ts`, AGENTS.md rule 7). It drew five raw Tailwind
 * palette classes until 2026-10-13, which no token, mode or accent reached.
 */
function fileIcon(artifact: ArtifactMeta): React.ReactNode {
  const ext = getExt(artifact.name);
  const mime = artifact.mimeType ?? "";
  const kind = (name: string, icon: string) => (
    <Icon name={icon} size={15} className={`shrink-0 ${categoricalAccent(name).text}`} />
  );
  if (classifyArtifact(artifact.name, artifact.path, mime) === "report") return kind("report", "FileChartColumn");
  if (IMAGE_EXTS.has(ext) || mime.startsWith("image/")) return kind("image", "FileImage");
  if (CODE_EXTS.has(ext)) return kind("code", "FileCode");
  if (["md", "txt", "log", "rst", "csv"].includes(ext) || mime.startsWith("text/")) return kind("text", "FileText");
  if (["pdf"].includes(ext) || mime === "application/pdf") return kind("pdf", "FileText");
  if (["xlsx", "xls"].includes(ext)) return kind("sheet", "FileSpreadsheet");
  if (isArchive(artifact.name, mime))
    return <Icon name="FileArchive" size={15} className="shrink-0 text-muted-foreground" />;
  return <Icon name="File" size={15} className="shrink-0 text-muted-foreground" />;
}

// ─── Component ─────────────────────────────────────────────────────────────────

export default function ArtifactCard({
  artifact,
  sessionId,
  onOpen,
  onOpenInSidePanel,
}: ArtifactCardProps) {
  const fileUrl = workspaceFileUrl(sessionId, artifact.path);
  // WS-27bm S8: a Markdown or HTML document also downloads as a PDF, which the
  // gateway lays out from the same file (`gateway/pdf_render.py`). A report
  // downloads the PDF that was written beside it.
  const pdf = cardPdf(sessionId, artifact);
  const image = isImage(artifact);
  const { isMobile } = useViewMode();
  const isDoc = hasRenderedView(artifact.name, artifact.path);
  // A zip has nothing to preview, so its card offers the download alone.
  const archive = isArchive(artifact.name, artifact.mimeType);
  // A zip lives on disk only, so its download can answer 410 (expired). The
  // card fetches it first and says so, instead of a failed browser download.
  const [notice, setNotice] = useState<string | null>(null);
  const downloadArchive = async (e: React.MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    e.stopPropagation();
    const result = await fetchArchive(fileUrl);
    setNotice(archiveNotice(result));
    if (result.kind === "ok") saveBlob(result.blob, artifact.name);
  };
  const [imgError, setImgError] = useState(false);

  // Build a FileEntry-compatible object for the ArtifactViewerModal
  const buildFileEntry = useCallback((): import("./ArtifactSidebar").FileEntry => ({
    path: artifact.path,
    name: artifact.name,
    size: artifact.size ?? 0,
    modified_at: new Date().toISOString(),
    mime_type: artifact.mimeType ?? "application/octet-stream",
  }), [artifact]);

  const handleOpen = () => {
    onOpen?.(buildFileEntry());
  };

  const handleOpenPanel = () => {
    onOpenInSidePanel?.(buildFileEntry());
  };

  /**
   * The one thing a click on this card should do: show the artifact RENDERED.
   * Desktop puts it in the side panel (resizable, sits beside the chat); mobile
   * has no side panel, so it opens as a full-bleed sheet. Same intent, different
   * surface — the user should never have to know which.
   */
  const openRendered = () => {
    if (!isMobile && onOpenInSidePanel) handleOpenPanel();
    else handleOpen();
  };

  // ── Image artifact: render inline thumbnail ─────────────────────────────
  if (image && !imgError) {
    return (
      <div className="mt-3 rounded-lg overflow-hidden border border-border bg-card/40 group/card">
        {/* Image */}
        <div className="relative">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={fileUrl}
            alt={artifact.name}
            className="w-full max-h-80 object-contain bg-background/50"
            loading="lazy"
            onError={() => setImgError(true)}
          />
          {/* Hover overlay with actions */}
          <div className="absolute inset-0 bg-black/0 group-hover/card:bg-black/40 transition-colors flex items-center justify-center gap-2 opacity-0 group-hover/card:opacity-100">
            <button
              onClick={handleOpen}
              className="rounded-lg bg-secondary/90 px-3 py-1.5 text-xs text-foreground hover:bg-secondary transition-colors flex items-center gap-1.5"
              title="Open full size"
            >
              <Icon name="Maximize2" size={13} />
              Open
            </button>
            <a
              href={fileUrl}
              download={artifact.name}
              className="rounded-lg bg-secondary/90 px-3 py-1.5 text-xs text-foreground hover:bg-secondary transition-colors flex items-center gap-1.5"
            >
              <Icon name="Download" size={13} />
              Download
            </a>
          </div>
        </div>
        {/* Caption bar */}
        <div className="flex items-center gap-2 px-2.5 py-1.5 border-t border-border">
          {fileIcon(artifact)}
          <span className="text-xs text-foreground truncate flex-1 min-w-0 font-mono">
            {artifact.name}
          </span>
          {artifact.size != null && (
            <span className="text-[10px] text-muted-foreground shrink-0">{formatBytes(artifact.size)}</span>
          )}
        </div>
      </div>
    );
  }

  // ── Non-image artifact: file card ───────────────────────────────────────
  return (
    <div
      onClick={isDoc ? openRendered : undefined}
      onKeyDown={
        isDoc
          ? (e) => {
              if (e.key !== "Enter" && e.key !== " ") return;
              e.preventDefault();
              openRendered();
            }
          : undefined
      }
      role={isDoc ? "button" : undefined}
      tabIndex={isDoc ? 0 : undefined}
      aria-label={isDoc ? `Open ${artifact.name}` : undefined}
      className={`@container mt-3 rounded-lg border border-border bg-card/40 px-2.5 py-2 flex items-center gap-2.5 group/card transition-colors hover:border-primary/30 ${
        isDoc
          ? "cursor-pointer hover:bg-card focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          : ""
      }`}
    >
      {/* Icon */}
      <div className="shrink-0 w-8 h-8 rounded-md bg-secondary flex items-center justify-center">
        {fileIcon(artifact)}
      </div>

      {/* File info. The card is its own size container: in the narrow rail
          (below @md) the name wraps instead of truncating, the folder and the
          hash hide, and the size stays on one line (S8 visual review: the rail
          showed "output.." and a size broken over two lines). */}
      <div className="flex-1 min-w-0">
        <div className="text-xs font-medium text-foreground font-mono break-all line-clamp-2 @md:truncate @md:break-normal" title={artifact.path}>
          {artifact.name}
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="hidden @md:inline text-[10px] text-muted-foreground truncate">{artifact.path}</span>
          {artifact.size != null && (
            <span className="shrink-0 whitespace-nowrap text-[10px] text-muted-foreground">{formatBytes(artifact.size)}</span>
          )}
          {artifact.sha256 && (
            <span className="hidden @md:inline text-[10px] text-muted-foreground/70 font-mono" title={`sha256:${artifact.sha256}`}>
              #{artifact.sha256.slice(0, 7)}
            </span>
          )}
        </div>
        {notice && (
          <p role="status" className="mt-1 text-[11px] text-muted-foreground">
            {notice}
          </p>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1 shrink-0">
        {isDoc && (isMobile || onOpenInSidePanel || onOpen) && (
          <Button
            variant="secondary"
            size="sm"
            icon={isMobile || !onOpenInSidePanel ? "Maximize2" : "PanelLeft"}
            onClick={(e) => { e.stopPropagation(); openRendered(); }}
            title={isMobile || !onOpenInSidePanel ? "Open the rendered artifact" : "Open in side panel — edit + live preview"}
          >
            Open
          </Button>
        )}
        {!isMobile && !archive && (
          <Button variant="ghost" size="icon-sm" layout="" onClick={(e) => { e.stopPropagation(); handleOpen(); }} title={isDoc ? "Open in modal viewer" : "Open in viewer"}>
            <Icon name="Maximize2" size={14} />
          </Button>
        )}
        <a
          href={fileUrl}
          download={artifact.name}
          onClick={archive ? downloadArchive : (e) => e.stopPropagation()}
          className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
          title="Download"
          aria-label={`Download ${artifact.name}`}
        >
          <Icon name="Download" size={14} />
        </a>
        {pdf && (
          <a
            href={pdf.url}
            download={pdf.name}
            onClick={(e) => e.stopPropagation()}
            className="flex items-center gap-1 rounded-lg p-1.5 text-[10px] font-medium text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
            title="Download PDF"
            aria-label={`Download ${artifact.name} as PDF`}
          >
            <Icon name="FileDown" size={14} />
            PDF
          </a>
        )}
      </div>
    </div>
  );
}
