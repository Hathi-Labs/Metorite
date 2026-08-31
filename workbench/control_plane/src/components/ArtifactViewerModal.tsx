"use client";

/**
 * ArtifactViewerModal — renders agent-generated files with type-appropriate fidelity.
 *
 * Rendering matrix:
 *   .md                 → react-markdown + remark-gfm
 *   .py .ts .js .sh … → shiki syntax highlighter
 *   .pdf                → react-pdf (pdfjs-dist)
 *   .docx               → mammoth.js (converted to HTML)
 *   .png .jpg .svg …   → <img> with zoom
 *   .csv .txt .log      → plain preformatted text
 *   other               → hex-dump excerpt + download button
 */

import Button from "@/components/ui/Button";
import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import SandboxedHtml from "@/components/SandboxedHtml";
import SandboxedReact from "@/components/SandboxedReact";
import { iconsUsedIn } from "@/lib/iconSvg";
import { useShikiTheme } from "@/lib/theme/surfaces";
import { classifyArtifact, extOf, isRenderable } from "@/lib/artifactKind";
import type { FileEntry } from "./ArtifactSidebar";

// ─── Types ────────────────────────────────────────────────────────────────────

interface ArtifactViewerModalProps {
  sessionId: string;
  entry: FileEntry;
  onClose: () => void;
  onDelete?: (entry: FileEntry) => void;
  /** Optional override for the file URL — used by the global artifacts
   *  browser where there is no session workspace. */
  downloadUrl?: string;
  /** Optional override for the save (PUT) URL. */
  saveUrl?: string;
  /** Optional override for the delete URL. */
  deleteUrl?: string;
  /** Read-only viewer: suppress Edit/Save/Delete. Used for external files that
   *  aren't workspace-editable (e.g. inbound email attachments), which only
   *  have a download URL — there is nothing to PUT/DELETE. */
  readOnly?: boolean;
}

type ViewerState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "text"; content: string; lang?: string }
  | { status: "markdown"; content: string }
  /** A runnable artifact: HTML for the sandbox, or React source to compile. */
  | { status: "rendered"; content: string; kind: "html" | "react" }
  | { status: "image"; url: string }
  | { status: "pdf"; url: string }
  | { status: "docx"; html: string }
  | { status: "binary"; hex: string; filename: string };

// ─── Helpers ──────────────────────────────────────────────────────────────────

function langFromExt(ext: string): string {
  const map: Record<string, string> = {
    py: "python", ts: "typescript", tsx: "tsx", js: "javascript",
    jsx: "jsx", sh: "bash", bash: "bash", zsh: "bash",
    yaml: "yaml", yml: "yaml", toml: "toml", json: "json",
    sql: "sql", rs: "rust", go: "go", java: "java", c: "c",
    cpp: "cpp", cs: "csharp", rb: "ruby", php: "php",
    html: "html", css: "css", scss: "scss", xml: "xml",
  };
  return map[ext] ?? "plaintext";
}

function toHex(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer.slice(0, 256));
  const lines: string[] = [];
  for (let i = 0; i < bytes.length; i += 16) {
    const chunk = Array.from(bytes.slice(i, i + 16));
    const hex = chunk.map((b) => b.toString(16).padStart(2, "0")).join(" ");
    const ascii = chunk.map((b) => (b >= 32 && b < 127 ? String.fromCharCode(b) : ".")).join("");
    lines.push(`${i.toString(16).padStart(6, "0")}  ${hex.padEnd(47)}  ${ascii}`);
  }
  return lines.join("\n");
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ─── Code renderer (shiki) ────────────────────────────────────────────────────

function CodeBlock({ code, lang }: { code: string; lang: string }) {
  const [html, setHtml] = useState<string | null>(null);
  // Shiki ships a closed set of named themes and cannot read our tokens, so a
  // code view names one. With the theming engine retired it follows the colour
  // mode alone — `lib/theme/surfaces.ts` owns which name that is.
  const shikiTheme = useShikiTheme();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { codeToHtml } = await import("shiki");
        const result = await codeToHtml(code, {
          lang,
          theme: shikiTheme,
        });
        if (!cancelled) setHtml(result);
      } catch {
        // Fallback: render plain
        if (!cancelled) setHtml(null);
      }
    })();
    return () => { cancelled = true; };
  }, [code, lang, shikiTheme]);

  if (html) {
    return (
      <div
        className="overflow-x-auto text-sm font-mono leading-relaxed"
        // shiki injects its own background + colors; we strip the wrapper
        // background so our modal background shows through
        style={{ background: "transparent" }}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    );
  }

  // plain fallback
  return (
    <pre className="overflow-x-auto whitespace-pre text-sm font-mono text-foreground leading-relaxed">
      {code}
    </pre>
  );
}

// ─── Markdown image path resolver ─────────────────────────────────────────────

/**
 * Rewrite an image src found inside a markdown file so it routes through the
 * gateway file proxy.
 *
 * Rules (in priority order):
 *  1. Already a full URL (http/https/data:) → pass through unchanged
 *  2. Absolute path starting with /          → treat as workspace-relative and proxy
 *  3. Relative path                          → resolve against the markdown file's
 *                                             directory, then proxy
 */
function resolveMediaSrc(src: string, sessionId: string, mdFilePath: string): string {
  // Full URLs and data URIs pass through unchanged
  if (/^(https?:|data:)/i.test(src)) return src;

  let workspacePath: string;
  if (src.startsWith("/")) {
    // Treat absolute paths as workspace-root-relative
    workspacePath = src.replace(/^\/+/, "");
  } else {
    // Relative: resolve against the directory containing the .md file
    const mdDir = mdFilePath.includes("/")
      ? mdFilePath.substring(0, mdFilePath.lastIndexOf("/"))
      : "";
    // Resolve ".." segments manually (URL has no filesystem resolve in browser)
    const parts = (mdDir ? `${mdDir}/${src}` : src).split("/");
    const resolved: string[] = [];
    for (const part of parts) {
      if (part === "..") resolved.pop();
      else if (part !== ".") resolved.push(part);
    }
    workspacePath = resolved.join("/");
  }

  return `/api/agent/workspace/${sessionId}/file?path=${encodeURIComponent(workspacePath)}`;
}

// ─── PDF renderer (react-pdf) ─────────────────────────────────────────────────

type PdfComponents = {
  Document: React.ComponentType<{
    file: string;
    onLoadSuccess: (p: { numPages: number }) => void;
    loading?: React.ReactNode;
    error?: React.ReactNode;
    children?: React.ReactNode;
  }>;
  Page: React.ComponentType<{
    pageNumber: number;
    width?: number;
    renderTextLayer?: boolean;
    renderAnnotationLayer?: boolean;
  }>;
};

function PdfViewer({ url }: { url: string }) {
  const [pdf, setPdf] = useState<PdfComponents | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [numPages, setNumPages] = useState<number>(0);
  const [page, setPage] = useState(1);

  // Dynamically import react-pdf and configure the worker once.
  // react-pdf 10.x bundles its OWN pdfjs-dist — we must point workerSrc
  // at the worker from THAT bundled copy, not the top-level pdfjs-dist.
  useEffect(() => {
    import("react-pdf")
      .then((mod) => {
        mod.pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
        setPdf({
          Document: mod.Document as PdfComponents["Document"],
          Page: mod.Page as PdfComponents["Page"],
        });
      })
      .catch((e) => setLoadError(String(e)));
  }, []);

  if (loadError) {
    return <div className="py-8 text-red-400 text-sm">Failed to load PDF renderer: {loadError}</div>;
  }

  if (!pdf) {
    return <div className="flex items-center justify-center h-32 text-muted-foreground text-sm animate-pulse">Loading PDF renderer…</div>;
  }

  const { Document, Page } = pdf;

  return (
    <div className="flex flex-col items-center gap-3">
      <Document
        file={url}
        onLoadSuccess={({ numPages: n }) => { setNumPages(n); setPage(1); }}
        loading={<div className="py-8 text-muted-foreground text-sm">Loading PDF…</div>}
        error={<div className="py-8 text-red-400 text-sm">Failed to load PDF. Try downloading instead.</div>}
      >
        <Page
          pageNumber={page}
          width={Math.min(700, typeof window !== "undefined" ? window.innerWidth - 120 : 700)}
          renderTextLayer={false}
          renderAnnotationLayer={false}
        />
      </Document>
      {numPages > 1 && (
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <button
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
            className="rounded px-2 py-1 bg-secondary hover:bg-secondary disabled:opacity-40 transition-colors"
          >
            ‹ Prev
          </button>
          <span>{page} / {numPages}</span>
          <button
            disabled={page >= numPages}
            onClick={() => setPage((p) => p + 1)}
            className="rounded px-2 py-1 bg-secondary hover:bg-secondary disabled:opacity-40 transition-colors"
          >
            Next ›
          </button>
        </div>
      )}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function ArtifactViewerModal({ sessionId, entry, onClose, onDelete, downloadUrl: externalDownloadUrl, saveUrl: externalSaveUrl, deleteUrl: externalDeleteUrl, readOnly = false }: ArtifactViewerModalProps) {
  const [state, setState] = useState<ViewerState>({ status: "loading" });
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const blobUrlRef = useRef<string | null>(null);

  // Determine if this file type supports inline editing.
  const kind = classifyArtifact(entry.name, entry.path, entry.mime_type);
  const editable =
    !readOnly &&
    (kind === "markdown" || kind === "html" || kind === "react" ||
     kind === "code" || kind === "text");
  const isMarkdown = kind === "markdown";
  /** Kinds that have a RENDERED view distinct from their source. */
  const renderable = isRenderable(kind);
  // Rendered artifacts open rendered — that is the whole point of opening one.
  // `showSource` flips to the code view without entering edit mode, which is the
  // only way to inspect an artifact on mobile (there is no side panel there).
  const [showSource, setShowSource] = useState(false);
  // Same reason as DocumentPane: a full-page artifact has no props.icons, so
  // resolve the icons its own source references or ccIcon() returns nothing.
  const source = state.status === "rendered" ? state.content : "";
  const icons = useMemo(() => iconsUsedIn(source), [source]);
  const draftIcons = useMemo(() => iconsUsedIn(editContent), [editContent]);

  // Build the file URL and load content
  useEffect(() => {
    let cancelled = false;
    const fileUrl = externalDownloadUrl
      ?? `/api/agent/workspace/${sessionId}/file?path=${encodeURIComponent(entry.path)}`;
    const kind = classifyArtifact(entry.name, entry.path, entry.mime_type);

    // Revoke any previous blob URL
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }

    setState({ status: "loading" });

    (async () => {
      try {
        const res = await fetch(fileUrl);
        if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);

        if (kind === "image") {
          const blob = await res.blob();
          const url = URL.createObjectURL(blob);
          blobUrlRef.current = url;
          if (!cancelled) setState({ status: "image", url });
          return;
        }

        if (kind === "pdf") {
          const blob = await res.blob();
          const url = URL.createObjectURL(blob);
          blobUrlRef.current = url;
          if (!cancelled) setState({ status: "pdf", url });
          return;
        }

        if (kind === "binary") {
          const buf = await res.arrayBuffer();
          if (!cancelled) setState({ status: "binary", hex: toHex(buf), filename: entry.name });
          return;
        }

        if (kind === "docx") {
          const buf = await res.arrayBuffer();
          try {
            const mammoth = await import("mammoth");
            const result = await mammoth.convertToHtml({ arrayBuffer: buf });
            if (!cancelled) setState({ status: "docx", html: result.value });
          } catch (convErr) {
            if (!cancelled) setState({ status: "error", message: `DOCX conversion failed: ${String(convErr)}` });
          }
          return;
        }

        const text = await res.text();
        if (!cancelled) {
          if (kind === "markdown") {
            setState({ status: "markdown", content: text });
          } else if (kind === "html" || kind === "react") {
            setState({ status: "rendered", content: text, kind });
          } else if (kind === "code") {
            setState({ status: "text", content: text, lang: langFromExt(extOf(entry.name)) });
          } else {
            setState({ status: "text", content: text });
          }
          // Pre-populate edit buffer with raw content.
          setEditContent(text);
        }
      } catch (err) {
        if (!cancelled) setState({ status: "error", message: String(err) });
      }
    })();

    return () => {
      cancelled = true;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, entry.path]);

  // Clean up blob URLs on unmount
  useEffect(() => {
    return () => {
      if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
    };
  }, []);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const downloadUrl = externalDownloadUrl
    ?? `/api/agent/workspace/${sessionId}/file?path=${encodeURIComponent(entry.path)}`;
  const deletable =
    !readOnly && !externalDownloadUrl && (
      entry.path.startsWith("inputs/") ||
      entry.path.startsWith("outputs/") ||
      entry.path.startsWith("agent-data/")
    );

  const handleDelete = async () => {
    if (!confirm(`Delete "${entry.name}"?\n\nThis cannot be undone.`)) return;
    try {
      const delUrl = externalDeleteUrl
        ?? `/api/agent/workspace/${sessionId}/file?path=${encodeURIComponent(entry.path)}`;
      const res = await fetch(delUrl, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      onDelete?.(entry);
      onClose();
    } catch (err) {
      alert(`Failed to delete: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleStartEdit = () => {
    // Grab raw content from current state.
    if (state.status === "markdown" || state.status === "text") {
      setEditContent(state.content);
    }
    setEditing(true);
    setShowPreview(false);
  };

  const handleCancelEdit = () => {
    setEditing(false);
    setShowPreview(false);
  };

  const handleTogglePreview = () => {
    setShowPreview((p) => !p);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const saveUrl = externalSaveUrl
        ?? `/api/agent/workspace/${sessionId}/file?path=${encodeURIComponent(entry.path)}`;
      const res = await fetch(saveUrl, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: editContent, encoding: "utf-8" }),
      });
      if (!res.ok) {
        const err = await res.text();
        throw new Error(`HTTP ${res.status}: ${err}`);
      }
      // Refresh state with new content.
      const kind = classifyArtifact(entry.name, entry.path, entry.mime_type);
      if (kind === "markdown") {
        setState({ status: "markdown", content: editContent });
      } else if (kind === "html" || kind === "react") {
        setState({ status: "rendered", content: editContent, kind });
      } else if (kind === "code") {
        setState({ status: "text", content: editContent, lang: langFromExt(extOf(entry.name)) });
      } else {
        setState({ status: "text", content: editContent });
      }
      setEditing(false);
    } catch (err) {
      alert(`Failed to save: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    /* Backdrop */
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      {/* Modal — full-width on mobile, constrained on desktop. pb-safe protects against iOS rounded corners. */}
      <div className="relative flex flex-col w-full max-w-4xl max-h-[90vh] sm:rounded-lg border-0 sm:border border-border bg-background shadow-2xl overflow-hidden sm:mx-4">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-4 py-3 shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-sm font-semibold text-foreground truncate">{entry.name}</span>
            <span className="text-xs text-muted-foreground truncate hidden sm:block">{entry.path}</span>
            <span className="text-xs text-muted-foreground/70">· {formatBytes(entry.size)}</span>
          </div>
          <div className="flex items-center gap-2 shrink-0 ml-3">
            {editing ? (
              <>
                {(isMarkdown || renderable) && (
                  <button
                    onClick={handleTogglePreview}
                    className={`rounded px-2 py-1 text-xs transition-colors ${
                      showPreview
                        ? "text-amber-400 bg-amber-900/40 hover:bg-amber-900/60"
                        : "text-purple-400 bg-secondary hover:bg-purple-900/40 hover:text-purple-300"
                    }`}
                    title={showPreview ? "Hide preview" : "Show live preview"}
                  >
                    {showPreview ? "Hide Preview" : "Preview"}
                  </button>
                )}
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="rounded px-2 py-1 text-xs text-emerald-400 bg-secondary hover:bg-emerald-900/60 hover:text-emerald-300 transition-colors disabled:opacity-50"
                >
                  {saving ? "Saving…" : "Save"}
                </button>
                <button
                  onClick={handleCancelEdit}
                  disabled={saving}
                  className="rounded px-2 py-1 text-xs text-muted-foreground bg-secondary hover:bg-secondary hover:text-foreground transition-colors"
                >
                  Cancel
                </button>
              </>
            ) : (
              <>
                {renderable && (
                  <button
                    onClick={() => setShowSource((v) => !v)}
                    className="rounded px-2 py-1 text-xs text-muted-foreground bg-secondary hover:bg-secondary hover:text-foreground transition-colors"
                    title={showSource ? "Show the rendered artifact" : "Show the source"}
                  >
                    {showSource ? "Preview" : "Code"}
                  </button>
                )}
                <a
                  href={downloadUrl}
                  download={entry.name}
                  className="rounded px-2 py-1 text-xs text-muted-foreground bg-secondary hover:bg-secondary hover:text-foreground transition-colors"
                >
                  Download
                </a>
                {editable && (
                  <button
                    onClick={handleStartEdit}
                    className="rounded px-2 py-1 text-xs text-blue-400 bg-secondary hover:bg-blue-900/60 hover:text-blue-300 transition-colors"
                    title="Edit file"
                  >
                    Edit
                  </button>
                )}
                {deletable && (
                  <button
                    onClick={handleDelete}
                    className="rounded px-2 py-1 text-xs text-red-400 bg-secondary hover:bg-red-900/60 hover:text-red-300 transition-colors"
                    title="Delete file"
                  >
                    Delete
                  </button>
                )}
              </>
            )}
            <Button variant="ghost" size="none" radius="keep" layout="" onClick={onClose} title="Close" className="rounded p-1 text-lg leading-none">
              ×
            </Button>
          </div>
        </div>

        {/* Body — pb-safe keeps content above iOS home indicator */}
        <div className="flex-1 overflow-auto p-6 min-h-0 pb-safe">
          {editing ? (
            (isMarkdown || renderable) && showPreview ? (
              /* ═══ Markdown split view: editor (left) + live preview (right) ═══ */
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 h-full min-h-[45vh]">
                {/* Left: raw markdown editor */}
                <div className="flex flex-col min-h-0">
                  <div className="text-[10px] text-muted-foreground mb-1.5 font-medium uppercase tracking-wide">
                    Markdown Source
                  </div>
                  <textarea
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    className="flex-1 min-h-[35vh] resize-none rounded border border-border bg-card p-4 text-sm font-mono text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50"
                    spellCheck={false}
                    placeholder="Edit markdown…"
                  />
                </div>
                {/* Right: live rendered preview */}
                <div className="flex flex-col min-h-0 overflow-auto">
                  <div className="text-[10px] text-muted-foreground mb-1.5 font-medium uppercase tracking-wide">
                    Rendered Preview
                  </div>
                  <div className="flex-1 rounded border border-border bg-card/60 p-4 overflow-auto prose prose-invert max-w-none
                    prose-headings:font-semibold prose-headings:text-foreground
                    prose-h1:text-xl prose-h2:text-lg prose-h3:text-base
                    prose-p:text-foreground prose-p:leading-7 prose-p:text-sm
                    prose-a:text-blue-400 prose-a:no-underline hover:prose-a:underline
                    prose-strong:text-foreground prose-em:text-foreground
                    prose-code:text-sky-300 prose-code:bg-secondary prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs prose-code:font-mono prose-code:before:content-none prose-code:after:content-none
                    prose-pre:bg-card prose-pre:border prose-pre:border-border prose-pre:text-foreground prose-pre:text-xs
                    prose-blockquote:border-border prose-blockquote:text-muted-foreground
                    prose-hr:border-border
                    prose-li:text-foreground prose-li:marker:text-muted-foreground prose-li:text-sm
                    prose-table:text-xs prose-thead:border-border prose-tbody:border-border
                    prose-th:text-foreground prose-td:text-muted-foreground
                    prose-img:rounded prose-img:mx-auto prose-img:max-w-full"
                  >
                    {renderable ? (
                      editContent.trim() ? (
                        state.status === "rendered" && state.kind === "react" ? (
                          <SandboxedReact code={editContent} iconNames={draftIcons} />
                        ) : (
                          <SandboxedHtml html={editContent} iconNames={draftIcons} />
                        )
                      ) : (
                        <p className="text-muted-foreground text-sm italic">
                          Start typing to see a live preview…
                        </p>
                      )
                    ) : editContent.trim() ? (
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        rehypePlugins={[rehypeRaw]}
                        components={{
                          img({ src, alt, ...rest }) {
                            const rawSrc = typeof src === "string" ? src : "";
                            const resolvedSrc = resolveMediaSrc(rawSrc, sessionId, entry.path);
                            // eslint-disable-next-line @next/next/no-img-element
                            return <img src={resolvedSrc} alt={alt ?? ""} {...rest} className="max-w-full rounded" />;
                          },
                          a({ href, children, ...rest }) {
                            const isExternal = href?.startsWith("http");
                            return (
                              <a
                                href={href}
                                target={isExternal ? "_blank" : undefined}
                                rel={isExternal ? "noreferrer noopener" : undefined}
                                {...rest}
                              >
                                {children}
                              </a>
                            );
                          },
                        }}
                      >
                        {editContent}
                      </ReactMarkdown>
                    ) : (
                      <p className="text-muted-foreground text-sm italic">
                        Start typing to see a live preview…
                      </p>
                    )}
                  </div>
                </div>
              </div>
            ) : (
              /* ═══ Plain textarea (non-markdown or preview off) ═══ */
              <textarea
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                className="w-full h-full min-h-[40vh] resize-y rounded border border-border bg-card p-4 text-sm font-mono text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50"
                spellCheck={false}
                placeholder={isMarkdown ? "Edit markdown…  Click Preview to see rendered output." : "Edit file content…"}
              />
            )
          ) : (
            <>
          {state.status === "loading" && (
            <div className="flex items-center justify-center h-32">
              <div className="text-sm text-muted-foreground animate-pulse">Loading…</div>
            </div>
          )}

          {state.status === "error" && (
            <div className="rounded border border-red-900/50 bg-red-950/30 p-4 text-sm text-red-400">
              {state.message}
            </div>
          )}

          {state.status === "rendered" && !showSource && (
            // -mx-6 -my-6 cancels the modal body padding: a full-page artifact
            // should use the whole sheet, which matters most on a phone.
            <div className="-mx-6 -my-6 h-full min-h-[60vh]">
              {state.kind === "html" ? (
                <SandboxedHtml html={state.content} iconNames={icons} chromeless />
              ) : (
                <SandboxedReact code={state.content} iconNames={icons} chromeless />
              )}
            </div>
          )}

          {state.status === "rendered" && showSource && (
            <CodeBlock
              code={state.content}
              lang={state.kind === "html" ? "html" : "tsx"}
            />
          )}

          {state.status === "markdown" && (
            <div className="prose prose-invert max-w-none
              prose-headings:font-semibold prose-headings:text-foreground
              prose-h1:text-2xl prose-h2:text-xl prose-h3:text-lg
              prose-p:text-foreground prose-p:leading-7
              prose-a:text-blue-400 prose-a:no-underline hover:prose-a:underline
              prose-strong:text-foreground prose-em:text-foreground
              prose-code:text-sky-300 prose-code:bg-secondary prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-sm prose-code:font-mono prose-code:before:content-none prose-code:after:content-none
              prose-pre:bg-card prose-pre:border prose-pre:border-border prose-pre:text-foreground prose-pre:text-sm
              prose-blockquote:border-border prose-blockquote:text-muted-foreground
              prose-hr:border-border
              prose-li:text-foreground prose-li:marker:text-muted-foreground
              prose-table:text-sm prose-thead:border-border prose-tbody:border-border
              prose-th:text-foreground prose-td:text-muted-foreground
              prose-img:rounded prose-img:mx-auto"
            >
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeRaw]}
                components={{
                  // Rewrite image src: relative paths → gateway file proxy
                  img({ src, alt, ...rest }) {
                    const rawSrc = typeof src === "string" ? src : "";
                    const resolvedSrc = resolveMediaSrc(rawSrc, sessionId, entry.path);
                    // eslint-disable-next-line @next/next/no-img-element
                    return <img src={resolvedSrc} alt={alt ?? ""} {...rest} className="max-w-full rounded" />;
                  },
                  // Open links in a new tab and guard external URLs
                  a({ href, children, ...rest }) {
                    const isExternal = href?.startsWith("http");
                    return (
                      <a
                        href={href}
                        target={isExternal ? "_blank" : undefined}
                        rel={isExternal ? "noreferrer noopener" : undefined}
                        {...rest}
                      >
                        {children}
                      </a>
                    );
                  },
                }}
              >
                {state.content}
              </ReactMarkdown>
            </div>
          )}

          {state.status === "text" && state.lang && (
            <CodeBlock code={state.content} lang={state.lang} />
          )}

          {state.status === "text" && !state.lang && (
            <pre className="whitespace-pre-wrap text-sm font-mono text-foreground leading-relaxed">
              {state.content}
            </pre>
          )}

          {state.status === "image" && (
            <div className="flex items-center justify-center">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={state.url}
                alt={entry.name}
                className="max-w-full max-h-[70vh] object-contain rounded"
              />
            </div>
          )}

          {state.status === "pdf" && (
            <PdfViewer url={state.url} />
          )}

          {state.status === "docx" && (
            <div
              className="prose prose-sm prose-invert max-w-none dark:prose-invert overflow-x-auto"
              dangerouslySetInnerHTML={{ __html: state.html }}
            />
          )}

          {state.status === "binary" && (
            <div className="flex flex-col gap-3">
              <p className="text-xs text-muted-foreground">
                Binary file — showing first 256 bytes as hex
              </p>
              <pre className="overflow-x-auto text-xs font-mono text-muted-foreground bg-card rounded p-3 leading-relaxed">
                {state.hex}
              </pre>
              <a
                href={downloadUrl}
                download={entry.name}
                className="self-start rounded px-3 py-1.5 text-sm bg-secondary hover:bg-secondary text-foreground transition-colors"
              >
                Download file
              </a>
            </div>
          )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
