"use client";

/**
 * DocumentPane — renders ONE open file inside the side-panel editor, with a
 * rendered ↔ edit toggle for Markdown/HTML and live preview.
 *
 * Rendering matrix (mirrors ArtifactViewerModal, adapted to a resizable pane):
 *   .md / .mdx  → react-markdown (rendered) ⇄ textarea (edit) with live preview
 *   .html/.htm  → SandboxedHtml (rendered, interactive) ⇄ textarea (edit) w/ live preview
 *   .jsx/.tsx   → SandboxedReact — a full-page React artifact — but ONLY under
 *                 outputs/; elsewhere they stay source code (see isArtifactPath)
 *   code exts   → syntax-highlighted text (edit = plain textarea)
 *   images      → <img>
 *   other       → download prompt
 *
 * Edits PUT back to the same workspace file endpoint the modal uses, so a doc
 * the user tweaks here is the same file the agent sees.  When the agent is
 * actively writing the file (`live`), the pane polls the content so the user
 * watches it stream in.
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import SandboxedHtml from "@/components/SandboxedHtml";
import SandboxedReact from "@/components/SandboxedReact";
import { iconsUsedIn } from "@/lib/iconSvg";
import {
  canDownloadPdf,
  classifyArtifact,
  isRenderable,
  pdfNameFor,
  workspaceFileUrl,
  type ArtifactKind,
} from "@/lib/artifactKind";

/** PDFs and Word docs have no renderer here — offer the download instead. */
const UNDISPLAYABLE = new Set<ArtifactKind>(["pdf", "docx", "binary"]);

interface DocumentPaneProps {
  sessionId: string;
  path: string;
  name: string;
  /** True while the agent is actively writing this file — poll for updates. */
  live?: boolean;
}

export default function DocumentPane({ sessionId, path, name, live }: DocumentPaneProps) {
  const raw = classifyArtifact(name, path);
  // Collapse what this pane cannot render into one "download it" state.
  const kind: ArtifactKind | "binary" = UNDISPLAYABLE.has(raw) ? "binary" : raw;
  const editable =
    kind === "markdown" || isRenderable(kind) || kind === "code" || kind === "text";
  const previewable = kind === "markdown" || isRenderable(kind);


  const fileUrl = workspaceFileUrl(sessionId, path);

  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // "preview" = rendered view; "edit" = textarea. Non-previewable kinds ignore this.
  const [mode, setMode] = useState<"preview" | "edit">("preview");
  const [draft, setDraft] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const lastLoadedRef = useRef<string>("");

  // Full-page artifacts have no props.icons to declare with, so the icons
  // come from what the source actually references (see iconsUsedIn).
  const icons = useMemo(() => iconsUsedIn(content), [content]);
  const draftIcons = useMemo(() => iconsUsedIn(draft), [draft]);

  const loadText = useCallback(
    async (opts?: { silent?: boolean }) => {
      if (!opts?.silent) {
        setLoading(true);
        setError(null);
      }
      try {
        const res = await fetch(fileUrl, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const text = await res.text();
        lastLoadedRef.current = text;
        setContent(text);
        // While the user is actively editing, don't clobber their draft with a
        // live-poll refresh; only sync the draft when not dirty.
        setDirty((d) => {
          if (!d) setDraft(text);
          return d;
        });
      } catch (e) {
        if (!opts?.silent) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!opts?.silent) setLoading(false);
      }
    },
    [fileUrl],
  );

  // Initial load (and reset view state when the target file changes). The
  // synchronous setState is a deliberate reset-on-prop-change — the tab now
  // points at a different file, so mode/dirty/loading must snap back before the
  // fetch resolves. (Same pattern the file tree uses; the lint rule targets
  // sync→external cascades, which this isn't.)
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    setMode("preview");
    setDirty(false);
    if (kind === "image" || kind === "binary") {
      setLoading(false);
      return;
    }
    loadText();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileUrl, kind]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Live polling while the agent streams this file — silent refresh every 1.2s
  // until the live flag clears. Skipped when the user has unsaved edits.
  useEffect(() => {
    if (!live || kind === "image" || kind === "binary") return;
    const id = setInterval(() => {
      if (!dirty) loadText({ silent: true });
    }, 1200);
    return () => clearInterval(id);
  }, [live, kind, dirty, loadText]);

  const startEdit = () => {
    setDraft(content);
    setMode("edit");
  };
  const cancelEdit = () => {
    setDraft(content);
    setDirty(false);
    setMode("preview");
  };
  const onDraftChange = (v: string) => {
    setDraft(v);
    setDirty(v !== lastLoadedRef.current);
  };

  const save = async () => {
    setSaving(true);
    try {
      const res = await fetch(fileUrl, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: draft, encoding: "utf-8" }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
      lastLoadedRef.current = draft;
      setContent(draft);
      setDirty(false);
      if (previewable) setMode("preview");
    } catch (e) {
      alert(`Failed to save: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  // ── Toolbar ───────────────────────────────────────────────────────────────
  const toolbar = (
    <div className="flex items-center gap-1.5 border-b border-border bg-card/60 px-3 py-1.5 shrink-0">
      <span className="truncate text-xs font-medium text-foreground">{name}</span>
      {live && (
        <span className="flex items-center gap-1 rounded-full bg-primary/15 px-1.5 py-0.5 text-[10px] font-medium text-primary">
          <Icon name="Loader2" size={9} className="animate-spin" />
          writing
        </span>
      )}
      {dirty && <span className="text-[10px] text-accent">● unsaved</span>}
      <div className="ml-auto flex items-center gap-1">
        {previewable && mode === "edit" && (
          <Button variant="ghost" size="none" radius="keep" layout="flex items-center" onClick={() => setMode("preview")} title="Rendered preview" className="gap-1 rounded px-2 py-1 text-[11px]">
            <Icon name="Eye" size={12} /> Preview
          </Button>
        )}
        {editable && mode === "preview" && (
          <Button variant="ghost" size="none" radius="keep" layout="flex items-center" onClick={startEdit} title="Edit" className="gap-1 rounded px-2 py-1 text-[11px]">
            <Icon name="Pencil" size={12} /> Edit
          </Button>
        )}
        {mode === "edit" && (
          <>
            <button
              onClick={save}
              disabled={saving || !dirty}
              className="flex items-center gap-1 rounded px-2 py-1 text-[11px] text-primary hover:bg-primary/10 transition-colors disabled:opacity-40"
              title="Save"
            >
              <Icon name="Save" size={12} /> {saving ? "Saving…" : "Save"}
            </button>
            <Button variant="ghost" size="none" radius="keep" layout="flex items-center" onClick={cancelEdit} disabled={saving} title="Discard changes" className="gap-1 rounded px-2 py-1 text-[11px]">
              <Icon name="RotateCcw" size={12} />
            </Button>
          </>
        )}
        <a
          href={fileUrl}
          download={name}
          className="flex items-center gap-1 rounded px-2 py-1 text-[11px] text-muted-foreground hover:bg-secondary hover:text-foreground transition-colors"
          title={`Download ${name}`}
        >
          <Icon name="Download" size={12} />
        </a>
        {canDownloadPdf(kind) && (
          <a
            href={workspaceFileUrl(sessionId, path, { pdf: true })}
            download={pdfNameFor(name)}
            className="flex items-center gap-1 rounded px-2 py-1 text-[11px] text-muted-foreground hover:bg-secondary hover:text-foreground transition-colors"
            title={`Download ${name} as PDF`}
          >
            <Icon name="FileDown" size={12} /> PDF
          </a>
        )}
      </div>
    </div>
  );

  // ── Body ────────────────────────────────────────────────────────────────
  let body: React.ReactNode;
  if (loading) {
    body = (
      <div className="flex flex-1 items-center justify-center text-xs text-muted-foreground">
        <Icon name="Loader2" size={14} className="mr-2 animate-spin" /> Loading…
      </div>
    );
  } else if (error) {
    body = (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 p-4 text-center">
        <p className="text-xs text-destructive">Couldn’t load this file.</p>
        <p className="text-[11px] text-muted-foreground">{error}</p>
      </div>
    );
  } else if (kind === "image") {
    body = (
      <div className="flex flex-1 items-center justify-center overflow-auto p-4">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={fileUrl} alt={name} className="max-h-full max-w-full object-contain" />
      </div>
    );
  } else if (kind === "binary") {
    body = (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-xs text-muted-foreground">This file can’t be previewed.</p>
        <a
          href={fileUrl}
          download={name}
          className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 transition"
        >
          <Icon name="Download" size={13} /> Download {name}
        </a>
      </div>
    );
  } else if (mode === "edit") {
    body = (
      <div className="flex flex-1 min-h-0 flex-col">
        <textarea
          value={draft}
          onChange={(e) => onDraftChange(e.target.value)}
          spellCheck={false}
          className="flex-1 min-h-0 w-full resize-none bg-background p-3 font-mono text-[13px] leading-relaxed text-foreground outline-none"
          placeholder="Empty file"
        />
        {previewable && (
          <div className="max-h-[45%] shrink-0 overflow-auto border-t border-border bg-card/40 p-3">
            <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground/70">
              Live preview
            </div>
            {kind === "markdown" ? (
              <MarkdownBody content={draft} />
            ) : kind === "react" ? (
              <SandboxedReact code={draft} iconNames={draftIcons} />
            ) : (
              <SandboxedHtml html={draft} iconNames={draftIcons} />
            )}
          </div>
        )}
      </div>
    );
  } else if (kind === "markdown") {
    body = (
      <div className="flex-1 overflow-auto p-4">
        <MarkdownBody content={content} />
      </div>
    );
  } else if (kind === "html") {
    body = (
      <div className="flex-1 min-h-0">
        <SandboxedHtml html={content} iconNames={icons} chromeless />
      </div>
    );
  } else if (kind === "react") {
    // While the agent is still writing the file, the source is incomplete by
    // definition — compiling each 1.2s poll would flash syntax errors and burn a
    // build per poll. Show it streaming in, then render once the writing stops.
    body = live ? (
      <div className="flex-1 overflow-auto">
        <div className="flex items-center gap-1.5 border-b border-border/60 bg-card/40 px-3 py-1 text-[10px] text-muted-foreground">
          <Icon name="Loader2" size={9} className="animate-spin" />
          building — renders when the agent finishes writing
        </div>
        <pre className="whitespace-pre-wrap break-words p-3 font-mono text-[12px] leading-relaxed text-muted-foreground">
          {content}
        </pre>
      </div>
    ) : (
      <div className="flex-1 min-h-0">
        <SandboxedReact code={content} iconNames={icons} chromeless />
      </div>
    );
  } else {
    // code / text — rendered read-only as preformatted text
    body = (
      <div className="flex-1 overflow-auto">
        <pre className="whitespace-pre-wrap break-words p-3 font-mono text-[13px] leading-relaxed text-foreground">
          {content}
        </pre>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {toolbar}
      {body}
    </div>
  );
}

/** Themed markdown body — same prose styling the app uses elsewhere. */
function MarkdownBody({ content }: { content: string }) {
  return (
    <div className="prose prose-sm prose-invert max-w-none prose-headings:font-semibold prose-a:text-primary prose-code:text-accent">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
