"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { Skeleton } from "@/components/ui/Skeleton";
import { useEffect, useState } from "react";
import { Attachment } from "../lib/types";
import { getAttachmentDownloadUrl } from "../lib/api";
import { formatBytes } from "../lib/utils";
import { chipsInRow } from "../lib/attachmentRow";
import ArtifactViewerModal from "@/components/ArtifactViewerModal";
import type { FileEntry } from "@/components/ArtifactSidebar";

/**
 * The attachment "download card" list — shared by the single-message reader
 * (EmailDetail) and each message card in a conversation (ConversationView), so
 * a thread's earlier messages surface their files the same way as the open one.
 *
 * Each attachment is a compact card: a small thumbnail (image attachments — the
 * thumbnail opens full-size in a new tab) or a file icon, the filename + size,
 * and a download link. The image bytes come through the same authenticated Next
 * proxy as the download, so no extra endpoint is needed for the preview.
 */
/** A synthetic FileEntry so an email attachment can be rendered by the shared
 *  ArtifactViewerModal. The modal keys its type detection off `name`/`mime_type`
 *  and fetches bytes from the `downloadUrl` override, so no workspace path or
 *  session is needed. */
function toFileEntry(att: Attachment): FileEntry {
  return {
    path: att.filename,
    name: att.filename,
    size: att.sizeBytes,
    modified_at: "",
    mime_type: att.mimeType || "application/octet-stream",
  };
}

/** Runs one Try again. The busy flag holds while it runs and drops after, also
 *  on a failure. The parent keeps "failed" then, so the same sentence shows. */
export async function runRetry(
  retry: () => Promise<unknown> | void,
  setBusy: (busy: boolean) => void,
): Promise<void> {
  setBusy(true);
  try {
    await retry();
  } catch {
    // Nothing to do: the status stays "failed" and the slot says so again.
  } finally {
    setBusy(false);
  }
}

/** The slot of a mail whose files could not load: what happened, and Try again. */
export function FilesFailed({
  busy = false,
  onRetry,
  className = "",
}: {
  busy?: boolean;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div className={className} data-message-files-failed="" role="group" aria-label="Attachments">
      <div className="flex min-h-14 items-center gap-3 rounded-md border border-border px-3 py-2">
        <Icon name="AlertTriangle" size={16} className="flex-shrink-0 text-destructive" />
        <div className="min-w-0 flex-1 text-xs" role="status" aria-live="polite">
          <div className="text-destructive">Could not load the files of this mail.</div>
          <div className="text-muted-foreground">The mail service did not answer. Try again in a moment.</div>
        </div>
        {onRetry && (
          <Button
            variant="secondary"
            size="none"
            layout="flex items-center justify-center"
            type="button"
            onClick={onRetry}
            disabled={busy}
            aria-busy={busy}
            className="flex-shrink-0 gap-1 px-3 py-1 text-xs"
          >
            {busy && <Icon name="Loader2" size={12} className="animate-spin" />}
            {busy ? "Trying again…" : "Try again"}
          </Button>
        )}
      </div>
    </div>
  );
}

export function AttachmentList({
  attachments,
  pending = false,
  status,
  onRetry,
  className = "",
}: {
  attachments?: Attachment[];
  /** The list of files can still arrive. The list sits above the body, so a
   *  late list would push the body down while the member reads it. The shape
   *  of one chip holds the space until then (owner, 2026-11-06). */
  pending?: boolean;
  /** "failed" when the gateway could not get the files from the provider.
   *  An empty list alone says nothing: a mail with inline pictures only has
   *  none (task 1868). */
  status?: "ok" | "failed";
  /** Fetches the files of this one message again. It settles when done. */
  onRetry?: () => Promise<unknown> | void;
  className?: string;
}) {
  const [retrying, setRetrying] = useState(false);
  const [expanded, setExpanded] = useState(false);
  // The attachment currently open in the pop-up viewer (null = closed). Images,
  // PDFs and documents all open in the same ArtifactViewerModal — never a new
  // browser tab or the side panel.
  const [viewing, setViewing] = useState<Attachment | null>(null);
  // The width of the row, for the chips that fit in it (`lib/attachmentRow.ts`).
  const [rowEl, setRowEl] = useState<HTMLDivElement | null>(null);
  const [rowWidth, setRowWidth] = useState(0);
  useEffect(() => {
    if (!rowEl || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setRowWidth(entry.contentRect.width));
    observer.observe(rowEl);
    return () => observer.disconnect();
  }, [rowEl]);
  if (!attachments || attachments.length === 0) {
    if (status === "failed" && !pending) {
      return (
        <FilesFailed
          className={className}
          busy={retrying}
          onRetry={onRetry ? () => void runRetry(onRetry, setRetrying) : undefined}
        />
      );
    }
    if (!pending) return null;
    // The shape has the height of the closed list: one row of chips.
    return (
      <div className={className} data-message-files-pending="" role="status">
        <span className="sr-only">Loading the files of this mail…</span>
        <div className="w-[220px] overflow-hidden rounded-md border border-border">
          <Skeleton className="h-14 w-full" />
        </div>
      </div>
    );
  }

  const total = attachments.length;
  // Closed, the list keeps to one row, so its height never changes when the
  // files arrive (UI review, 2026-11-06). Open, it wraps.
  const fit = expanded ? total : chipsInRow(rowWidth, total);
  const shown = attachments.slice(0, fit);
  const hidden = total - fit;

  return (
    // The chips are part of the message, as in Outlook: no heading and no
    // divider (owner, 2026-11-06). The name stays for a screen reader.
    <div className={className} data-message-files="" role="group" aria-label={`Attachments (${total})`}>
      <div ref={setRowEl} className={expanded ? "flex flex-wrap gap-2" : "flex flex-nowrap gap-2 overflow-hidden"}>
        {shown.map((att) => {
          // Always go through the authenticated Next proxy — the absolute
          // gateway downloadUrl can't carry the internal token from a browser,
          // so it would 401.
          const url = getAttachmentDownloadUrl(att.id);
          const isImage = att.mimeType.startsWith("image/");
          return (
            <div
              key={att.id}
              className="flex w-[220px] flex-shrink-0 items-center border border-border rounded-md bg-secondary overflow-hidden"
            >
              {/* Thumbnail / icon — opens the pop-up viewer. */}
              <button
                type="button"
                onClick={() => setViewing(att)}
                title={`Preview ${att.filename}`}
                className="flex-shrink-0 group relative"
              >
                {isImage ? (
                  <>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={url}
                      alt={att.filename}
                      loading="lazy"
                      className="w-14 h-14 object-cover bg-background block"
                    />
                    <span className="absolute inset-0 hidden group-hover:flex items-center justify-center bg-black/40">
                      <Icon name="Eye" size={14} className="text-white" />
                    </span>
                  </>
                ) : (
                  <span className="w-14 h-14 bg-background flex items-center justify-center group-hover:bg-secondary/60 transition-colors">
                    <Icon name="FileText" size={18} className="text-muted-foreground group-hover:text-foreground" />
                  </span>
                )}
              </button>
              {/* Filename / size — also opens the viewer. */}
              <button
                type="button"
                onClick={() => setViewing(att)}
                title={`Preview ${att.filename}`}
                className="min-w-0 flex-1 px-2.5 py-1 text-left"
              >
                <div
                  className="text-xs text-foreground truncate"
                  title={att.filename}
                >
                  {att.filename}
                </div>
                {att.sizeBytes > 0 && (
                  <div className="text-[10px] text-muted-foreground">
                    {formatBytes(att.sizeBytes)}
                  </div>
                )}
              </button>
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                download={att.filename}
                title={`Download ${att.filename}`}
                className="px-2 self-stretch flex items-center text-muted-foreground hover:text-foreground hover:bg-secondary/60 transition-colors flex-shrink-0"
              >
                <Icon name="Download" size={13} />
              </a>
            </div>
          );
        })}
        {hidden > 0 && (
          <Button
            variant="secondary"
            size="none"
            layout="flex items-center justify-center"
            type="button"
            onClick={() => setExpanded(true)}
            title={`Show all ${total} attachments`}
            className="w-[72px] flex-shrink-0 text-xs"
          >
            +{hidden} more
          </Button>
        )}
      </div>
      {expanded && (
        <Button variant="text" size="none" layout="inline-flex items-center" type="button" onClick={() => setExpanded(false)} className="mt-2 gap-1 text-[11px]">
          <Icon name="ChevronUp" size={12} />
          Show fewer
        </Button>
      )}

      {/* Pop-up viewer — images, PDFs and documents all render here. Read-only:
          attachments aren't workspace files, so no edit/save/delete. */}
      {viewing && (
        <ArtifactViewerModal
          sessionId=""
          entry={toFileEntry(viewing)}
          downloadUrl={getAttachmentDownloadUrl(viewing.id)}
          readOnly
          onClose={() => setViewing(null)}
        />
      )}
    </div>
  );
}
