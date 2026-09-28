"use client";

/**
 * FileUploadButton — upload files to the agent workspace.
 *
 * Usage:
 *   <FileUploadButton
 *     sessionId={activeSessionId}
 *     onUploadComplete={(files) => { /* send context message to agent * / }}
 *   />
 *
 * The button shows a paperclip icon.  Clicking it opens the native file
 * picker.  Selected files are uploaded to the session's .tmp/ directory
 * via POST /api/agent/workspace/[sessionId]/upload.
 *
 * While uploading, the button shows a spinner.  On success it pulses green
 * briefly.  On error it flashes red.
 */

import Icon from "@/components/Icon";
import { useRef, useState, useCallback } from "react";
import type { FileEntry } from "@/components/ArtifactSidebar";

type UploadState =
  | { phase: "idle" }
  | { phase: "uploading"; count: number }
  | { phase: "success"; files: FileEntry[] }
  | { phase: "error"; message: string };

interface Props {
  sessionId: string;
  onUploadComplete?: (files: FileEntry[]) => void;
  className?: string;
  /** If true, renders as a full drop-zone instead of just an icon button. */
  dropZone?: boolean;
  /** Extra children rendered inside the drop zone (e.g. instructional text). */
  children?: React.ReactNode;
}

export default function FileUploadButton({
  sessionId,
  onUploadComplete,
  className = "",
  dropZone = false,
  children,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<UploadState>({ phase: "idle" });
  const [dragOver, setDragOver] = useState(false);

  const handleFiles = useCallback(
    async (fileList: FileList | File[]) => {
      const files = Array.from(fileList);
      if (files.length === 0) return;

      setState({ phase: "uploading", count: files.length });

      try {
        const formData = new FormData();
        for (const f of files) {
          formData.append("files", f);
        }

        const res = await fetch(
          `/api/agent/workspace/${sessionId}/upload`,
          { method: "POST", body: formData }
        );

        if (!res.ok) {
          const err = await res.json().catch(() => ({ error: "Upload failed" }));
          throw new Error(err.error ?? `HTTP ${res.status}`);
        }

        const uploaded: FileEntry[] = await res.json();
        setState({ phase: "success", files: uploaded });
        onUploadComplete?.(uploaded);

        // Reset to idle after a brief success glow
        setTimeout(() => setState({ phase: "idle" }), 2000);
      } catch (err) {
        setState({
          phase: "error",
          message: err instanceof Error ? err.message : "Upload failed",
        });
        setTimeout(() => setState({ phase: "idle" }), 3000);
      }
    },
    [sessionId, onUploadComplete]
  );

  const handleClick = () => inputRef.current?.click();

  // ── Icon-only button ────────────────────────────────────────────────────
  if (!dropZone) {
    return (
      <>
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) handleFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <button
          onClick={handleClick}
          disabled={state.phase === "uploading"}
          title="Upload files to agent"
          className={`rounded-lg p-2 transition-colors ${className} ${
            state.phase === "uploading"
              ? "text-warning cursor-wait"
              : state.phase === "success"
                ? "text-emerald-400"
                : state.phase === "error"
                  ? "text-red-400"
                  : "text-muted-foreground hover:text-foreground hover:bg-secondary"
          }`}
        >
          {state.phase === "uploading" ? (
            <Icon name="Loader2" size={18} className="animate-spin" />
          ) : state.phase === "success" ? (
            <Icon name="CheckCircle" size={18} />
          ) : state.phase === "error" ? (
            <Icon name="AlertCircle" size={18} />
          ) : (
            <Icon name="Paperclip" size={18} />
          )}
        </button>
      </>
    );
  }

  // ── Full drop zone ──────────────────────────────────────────────────────

  return (
    <div
      className={`relative rounded-lg border-2 border-dashed transition-colors ${
        dragOver
          ? "border-blue-500 bg-blue-500/10"
          : state.phase === "error"
            ? "border-red-700/50 bg-red-950/20"
            : "border-border/60 hover:border-border"
      } ${className}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        if (e.dataTransfer.files) handleFiles(e.dataTransfer.files);
      }}
      onClick={handleClick}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files) handleFiles(e.target.files);
          e.target.value = "";
        }}
      />

      {state.phase === "uploading" ? (
        <div className="flex items-center justify-center gap-2 py-3 px-4 text-sm text-warning">
          <Icon name="Loader2" size={16} className="animate-spin" />
          Uploading {state.count} file{state.count > 1 ? "s" : ""}…
        </div>
      ) : state.phase === "success" ? (
        <div className="flex items-center justify-center gap-2 py-3 px-4 text-sm text-emerald-400">
          <Icon name="CheckCircle" size={16} />
          Uploaded {state.files.length} file{state.files.length > 1 ? "s" : ""}
        </div>
      ) : state.phase === "error" ? (
        <div className="flex items-center justify-center gap-2 py-3 px-4 text-sm text-red-400">
          <Icon name="AlertCircle" size={16} />
          {state.message}
        </div>
      ) : (
        children ?? (
          <div className="flex items-center justify-center gap-2 py-3 px-4 text-sm text-muted-foreground">
            <Icon name="Paperclip" size={16} />
            Drop files here or click to upload
          </div>
        )
      )}
    </div>
  );
}
