"use client";

/**
 * The images of an agent question or line (WS-55 CIV-14b,
 * `external_agent_access.md` §12.9).
 *
 * An outside agent uploads a capture to the task and puts its id on a
 * question. This strip draws each image as a small picture. A click opens it
 * at full size in the one attachment viewer (`AttachmentViewer`, which is a
 * `Modal`), the same viewer that the Files tab of a task opens.
 *
 * ⚠️ **The bytes come from the cookie serve route** (`agentImageUrl`), so the
 * member's own session decides who sees them. The strip never reads the bytes
 * path of the MCP, and it never draws an SVG (`activityImages`).
 *
 * The thumbnail is a BUTTON with a border on the picture, as in the Files tab,
 * so it stays a clickable card and not a hand-rolled control.
 *
 * **A removed image (fix round 1).** The question keeps the id of each image,
 * and a member can remove the file in the Files tab later. The serve route
 * then answers 404, and the picture fails to load. The strip draws a
 * "Removed" tile of the same 64 px in its place, and that button is
 * inactive. The other images still open.
 *
 * `AgentImageStripView` holds no hook, so a test can read its element tree
 * and press a button (`agentQuestionCard.test.ts`). `AgentImageStrip` holds
 * the two states: the open image and the ids that failed to load.
 *
 * Fence: `agentQuestionCard.test.ts`.
 */

import { useState } from "react";

import Icon from "@/components/Icon";

import type { AttachmentRow } from "../lib/api";
import { AttachmentViewer } from "./AttachmentViewer";

/** The label of the strip and of each button. */
export function imageLabel(index: number, total: number, name: string): string {
  return `Open image ${index + 1} of ${total} at full size: ${name}`;
}

/** The label of the tile of an image that is no longer on the task. */
export function removedLabel(index: number, total: number, name: string): string {
  return `Image ${index + 1} of ${total} was removed from the task: ${name}`;
}

/** The words of the tile of a removed image. */
export const REMOVED_TEXT = "Removed";
export const REMOVED_TIP = "This image is no longer on the task.";

export interface AgentImageStripProps {
  /** The rows of `activityImages`. An empty list draws nothing. */
  images: readonly AttachmentRow[];
  /** The agent's name, for the label of the list. */
  agentLabel: string;
}

export interface AgentImageStripViewProps extends AgentImageStripProps {
  /** The image that the viewer shows, or `null` for a closed viewer. */
  opened: AttachmentRow | null;
  /** The ids whose picture failed to load: each draws the "Removed" tile. */
  broken: ReadonlySet<string>;
  onOpen: (file: AttachmentRow) => void;
  onBroken: (attachmentId: string) => void;
  onClose: () => void;
}

/** The strip with no state of its own. */
export function AgentImageStripView({
  images,
  agentLabel,
  opened,
  broken,
  onOpen,
  onBroken,
  onClose,
}: AgentImageStripViewProps) {
  if (images.length === 0) return null;
  const shown = opened && !broken.has(opened.attachment_id) ? opened : null;
  return (
    <>
      <ul
        className="flex flex-wrap gap-1.5"
        aria-label={`Images from ${agentLabel}`}
        data-agent-images={images.length}
      >
        {images.map((file, index) => {
          const gone = broken.has(file.attachment_id);
          return (
            <li key={file.attachment_id}>
              {gone ? (
                <button
                  type="button"
                  disabled
                  aria-label={removedLabel(index, images.length, file.name)}
                  title={REMOVED_TIP}
                  className="block shrink-0 cursor-default"
                  data-agent-image-removed={file.attachment_id}
                >
                  <span className="flex h-16 w-16 flex-col items-center justify-center gap-0.5 rounded border border-border bg-muted text-muted-foreground">
                    <Icon name="ImageOff" size={16} />
                    <span className="text-[10px]">{REMOVED_TEXT}</span>
                  </span>
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => onOpen(file)}
                  aria-label={imageLabel(index, images.length, file.name)}
                  title={file.name}
                  className="block shrink-0"
                  data-agent-image-open={file.attachment_id}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={file.url}
                    alt=""
                    loading="lazy"
                    onError={() => onBroken(file.attachment_id)}
                    className="h-16 w-16 rounded border border-border bg-muted object-cover"
                    data-agent-image={file.attachment_id}
                  />
                </button>
              )}
            </li>
          );
        })}
      </ul>
      <AttachmentViewer file={shown} onClose={onClose} />
    </>
  );
}

export function AgentImageStrip({ images, agentLabel }: AgentImageStripProps) {
  // The one open image. `Modal` is strictly controlled, so the strip holds it.
  const [opened, setOpened] = useState<AttachmentRow | null>(null);
  // The ids whose picture failed to load. The set only grows, because the
  // question keeps its ids and a removed file never comes back.
  const [broken, setBroken] = useState<ReadonlySet<string>>(() => new Set());
  return (
    <AgentImageStripView
      images={images}
      agentLabel={agentLabel}
      opened={opened}
      broken={broken}
      onOpen={setOpened}
      onClose={() => setOpened(null)}
      onBroken={(id) => setBroken((was) => (was.has(id) ? was : new Set([...was, id])))}
    />
  );
}

export default AgentImageStrip;
