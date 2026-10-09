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
 * The button shows a paperclip icon. Clicking it opens the native file
 * picker, which offers only `CHAT_UPLOAD_ACCEPT` (`lib/chatUpload.ts`).
 * Selected files are uploaded to the session's attachment folder via
 * POST /api/agent/workspace/[sessionId]/upload.
 *
 * A file over 25 MB, or of a kind the picker does not offer, is refused here
 * before it is sent. When the gateway refuses an upload, an error toast shows
 * its `detail`, so the member reads what to change, not a red icon alone
 * (the incident of 2026-10-09). The rules are in `lib/chatUpload.ts`.
 */

import Icon from "@/components/Icon";
import { useRef, useState, useCallback } from "react";
import type { FileEntry } from "@/components/ArtifactSidebar";
import { useToast } from "@/components/ui/Toast";
import {
  CHAT_UPLOAD_ACCEPT_ATTR,
  postUpload,
  refuseUpload,
  uploadErrorMessage,
} from "@/lib/chatUpload";

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

/** Thrown with the sentence the member reads. */
class UploadRefused extends Error {}

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
  const toast = useToast();

  const handleFiles = useCallback(
    async (fileList: FileList | File[]) => {
      const files = Array.from(fileList);
      if (files.length === 0) return;

      setState({ phase: "uploading", count: files.length });

      try {
        const refused = refuseUpload(files);
        if (refused) throw new UploadRefused(refused);

        const formData = new FormData();
        for (const f of files) {
          formData.append("files", f);
        }

        // One 404 is retried: the chat's session row may still be on its way.
        const res = await postUpload(sessionId, formData);

        if (!res.ok) {
          const body = await res.json().catch(() => null);
          throw new UploadRefused(uploadErrorMessage(res.status, body));
        }

        const uploaded: FileEntry[] = await res.json();
        setState({ phase: "success", files: uploaded });
        onUploadComplete?.(uploaded);

        // Reset to idle after a brief success glow
        setTimeout(() => setState({ phase: "idle" }), 2000);
      } catch (err) {
        const message =
          err instanceof UploadRefused
            ? err.message
            : "The upload failed. Check your connection and try again.";
        setState({ phase: "error", message });
        toast.show({
          key: `chat-upload:${sessionId}`,
          variant: "error",
          title: "The file was not attached",
          description: message,
        });
        setTimeout(() => setState({ phase: "idle" }), 3000);
      }
    },
    [sessionId, onUploadComplete, toast]
  );

  const handleClick = () => inputRef.current?.click();

  const fileInput = (
    <input
      ref={inputRef}
      type="file"
      multiple
      accept={CHAT_UPLOAD_ACCEPT_ATTR}
      className="hidden"
      onChange={(e) => {
        if (e.target.files) handleFiles(e.target.files);
        e.target.value = "";
      }}
    />
  );

  // ── Icon-only button ────────────────────────────────────────────────────
  if (!dropZone) {
    return (
      <>
        {fileInput}
        <button
          onClick={handleClick}
          disabled={state.phase === "uploading"}
          title={state.phase === "error" ? state.message : "Attach files"}
          aria-label="Attach files"
          className={`rounded-lg p-2 transition-colors ${className} ${
            state.phase === "uploading"
              ? "text-warning cursor-wait"
              : state.phase === "success"
                ? "text-success"
                : state.phase === "error"
                  ? "text-destructive"
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
          ? "border-primary bg-primary/10"
          : state.phase === "error"
            ? "border-destructive/50 bg-destructive/10"
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
      {fileInput}

      {state.phase === "uploading" ? (
        <div className="flex items-center justify-center gap-2 py-3 px-4 text-sm text-warning">
          <Icon name="Loader2" size={16} className="animate-spin" />
          Uploading {state.count} file{state.count > 1 ? "s" : ""}…
        </div>
      ) : state.phase === "success" ? (
        <div className="flex items-center justify-center gap-2 py-3 px-4 text-sm text-success">
          <Icon name="CheckCircle" size={16} />
          Uploaded {state.files.length} file{state.files.length > 1 ? "s" : ""}
        </div>
      ) : state.phase === "error" ? (
        <div role="alert" className="flex items-center justify-center gap-2 py-3 px-4 text-sm text-destructive">
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
