"use client";

/**
 * Projects · look at an attachment without leaving the task.
 *
 * Before this, every attachment was a link that opened a new tab. That is
 * fine for a spreadsheet and wrong for the common case: a screenshot pasted
 * onto a bug, which you want to glance at beside the description you are
 * reading — not in a tab that loses the task.
 *
 * ## What it will render, and what it refuses
 *
 * `previewKind` owns that, and it is a SAFELIST for a security reason rather
 * than a design one: previewing means rendering member-uploaded bytes on this
 * origin with this session attached. `image/svg+xml` is excluded even though
 * the server calls it an image, because an SVG may carry `<script>`.
 *
 * An un-previewable file still opens this dialog. It gets the same header and
 * the same actions, and says plainly that it will download — which is a
 * better answer than a link that silently does something different depending
 * on the type.
 *
 * ## The PDF is the browser's own viewer
 *
 * An `<iframe>` pointed at the attachment URL, which works only because the
 * server now sends `Content-Disposition: inline` for safelisted types. Under
 * `attachment` the frame renders nothing at all. We do not ship a PDF
 * renderer; the browser has one.
 */

import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";

import type { AttachmentRow } from "../lib/api";
import { previewKind, readableSize } from "../lib/preview";

interface Props {
  /** The attachment to show. `null` closes the dialog. */
  file: AttachmentRow | null;
  onClose: () => void;
  /** Detach it from this task. The panel owns the call and the refresh. */
  onRemove?: (attachmentId: string) => void;
  busy?: boolean;
}

export function AttachmentViewer({ file, onClose, onRemove, busy }: Props) {
  if (!file) return null;

  const kind = previewKind(file.mime);
  const size = readableSize(file.size);

  return (
    <Modal
      open
      onClose={onClose}
      title={file.name}
      icon={kind === "image" ? "Image" : kind === "document" ? "FileText" : "Paperclip"}
      size="lg"
      description={[file.mime, size].filter(Boolean).join(" · ")}
    >
      <div className="p-3">
        {kind === "image" ? (
          // `max-h` in viewport units so a tall screenshot scrolls the page
          // rather than the dialog growing past the window. `object-contain`
          // because cropping somebody's screenshot loses the part they
          // attached it for.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={file.url}
            alt={file.name}
            className="mx-auto max-h-[60vh] w-auto max-w-full rounded-md border border-border object-contain"
          />
        ) : kind === "document" ? (
          <iframe
            src={file.url}
            title={file.name}
            className="h-[60vh] w-full rounded-md border border-border bg-card"
          />
        ) : (
          <p className="rounded-md border border-border bg-card p-4 text-xs text-muted-foreground">
            This kind of file cannot be shown here, so Open downloads it.
            {file.mime === "image/svg+xml"
              ? " An SVG can carry a script, so it is never rendered in the app."
              : ""}
          </p>
        )}
      </div>

      <div className="flex flex-wrap justify-end gap-2 px-3 pb-3">
        {onRemove ? (
          <Button
            variant="destructive"
            size="sm"
            icon="Trash2"
            disabled={busy}
            // Detaches from THIS task. The file itself survives, which is why
            // the word is Remove and not Delete — the same wording the row
            // in the panel uses.
            title="Removes it from this task; the file itself is kept"
            onClick={() => onRemove(file.attachment_id)}
          >
            Remove
          </Button>
        ) : null}
        <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>
          Close
        </Button>
        <Button
          size="sm"
          icon="ExternalLink"
          onClick={() => window.open(file.url, "_blank", "noopener,noreferrer")}
        >
          Open
        </Button>
      </div>
    </Modal>
  );
}

export default AttachmentViewer;
