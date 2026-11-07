"use client";

/**
 * The chips of a chat file (projects_ai_chat §30.4, IMG-1). Two readers:
 *
 * - `PendingChips`: the composer's row above the textarea. Each chip shows a
 *   thumbnail (an image), the name, the size and a remove control. A failed
 *   chip shows the gateway's sentence and a retry.
 * - `SentChips`: the row under a member's words in a bubble, drawn from
 *   `parseUploadNote`. An image draws through the workspace file route, and a
 *   click opens it at full size.
 *
 * Tokens only: the chip is a `bg-card` box with a `border-border` edge.
 */
import { useState } from "react";
import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import Modal from "@/components/ui/Modal";
import type { PendingFile } from "@/lib/chatAttachments";
import { formatFileSize, isImageName, type NotedFile } from "@/lib/chatUpload";

const CHIP = "flex min-w-0 items-center gap-2 rounded-lg border border-border bg-card px-1.5 py-1 text-xs text-foreground";

export function workspaceFileUrl(sessionId: string, path: string): string {
  return `/api/agent/workspace/${encodeURIComponent(sessionId)}/file?path=${encodeURIComponent(path)}`;
}

function Thumb({ src, name }: { src?: string; name: string }) {
  return src ? (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={src} alt={name} className="h-9 w-9 shrink-0 rounded-md object-cover" />
  ) : (
    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-secondary text-muted-foreground">
      <Icon name="FileText" size={16} />
    </span>
  );
}

export function PendingChips({
  items,
  onRemove,
  onRetry,
}: {
  items: readonly PendingFile[];
  onRemove: (id: string) => void;
  onRetry: (id: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <ul data-chat-chips="composer" aria-label="Files in this message" className="flex flex-wrap gap-1.5 px-2.5 pt-2.5 glass:px-[18px] glass:pt-3.5">
      {items.map((p) => (
        <li
          key={p.id}
          data-chat-chip={p.state}
          className={`${CHIP} max-w-full sm:max-w-[16rem] ${p.state === "failed" ? "border-destructive/50" : ""}`}
        >
          <Thumb src={p.previewUrl} name={p.name} />
          <span className="min-w-0">
            <span className="block truncate font-medium" title={p.name}>{p.name}</span>
            {p.state === "failed" ? (
              <span role="alert" className="block text-[11px] text-destructive">{p.error}</span>
            ) : (
              <span className="block text-[11px] text-muted-foreground">
                {p.state === "uploading" ? "Uploading…" : formatFileSize(p.size)}
              </span>
            )}
          </span>
          {p.state === "uploading" && <Icon name="Loader2" size={14} className="shrink-0 animate-spin text-muted-foreground" />}
          {p.state === "failed" && (
            <Button type="button" variant="ghost" size="icon" icon="RotateCw" aria-label={`Retry ${p.name}`} title="Retry" onClick={() => onRetry(p.id)} />
          )}
          <Button type="button" variant="ghost" size="icon" icon="X" aria-label={`Remove ${p.name}`} title="Remove from this message" onClick={() => onRemove(p.id)} />
        </li>
      ))}
    </ul>
  );
}

export function SentChips({ files, sessionId }: { files: readonly NotedFile[]; sessionId: string }) {
  const [open, setOpen] = useState<NotedFile | null>(null);
  return (
    <>
      <ul data-chat-chips="sent" className="mt-2 flex flex-wrap gap-1.5">
        {files.map((f) => {
          const src = isImageName(f.name) ? workspaceFileUrl(sessionId, f.path) : undefined;
          const inner = (
            <>
              <Thumb src={src} name={f.name} />
              <span className="min-w-0 truncate font-medium" title={f.name}>{f.name}</span>
            </>
          );
          return (
            <li key={f.path} data-chat-chip="sent" className={`${CHIP} max-w-full text-foreground sm:max-w-[16rem]`}>
              {src ? (
                <button type="button" aria-label={`Open ${f.name} at full size`} onClick={() => setOpen(f)} className="flex min-w-0 items-center gap-2 text-left">
                  {inner}
                </button>
              ) : (
                inner
              )}
            </li>
          );
        })}
      </ul>
      <Modal open={open !== null} onClose={() => setOpen(null)} title={open?.name} size="4xl">
        {open && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={workspaceFileUrl(sessionId, open.path)} alt={open.name} className="mx-auto max-h-[75vh] max-w-full object-contain" />
        )}
      </Modal>
    </>
  );
}
