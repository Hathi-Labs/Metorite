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
 * Fence: `agentQuestionCard.test.ts`.
 */

import { useState } from "react";

import type { AttachmentRow } from "../lib/api";
import { AttachmentViewer } from "./AttachmentViewer";

/** The label of the strip and of each button. */
export function imageLabel(index: number, total: number, name: string): string {
  return `Open image ${index + 1} of ${total} at full size: ${name}`;
}

export interface AgentImageStripProps {
  /** The rows of `activityImages`. An empty list draws nothing. */
  images: readonly AttachmentRow[];
  /** The agent's name, for the label of the list. */
  agentLabel: string;
}

export function AgentImageStrip({ images, agentLabel }: AgentImageStripProps) {
  // The one open image. `Modal` is strictly controlled, so the strip holds it.
  const [opened, setOpened] = useState<AttachmentRow | null>(null);
  if (images.length === 0) return null;
  return (
    <>
      <ul
        className="flex flex-wrap gap-1.5"
        aria-label={`Images from ${agentLabel}`}
        data-agent-images={images.length}
      >
        {images.map((file, index) => (
          <li key={file.attachment_id}>
            <button
              type="button"
              onClick={() => setOpened(file)}
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
                className="h-16 w-16 rounded border border-border bg-muted object-cover"
                data-agent-image={file.attachment_id}
              />
            </button>
          </li>
        ))}
      </ul>
      <AttachmentViewer file={opened} onClose={() => setOpened(null)} />
    </>
  );
}

export default AgentImageStrip;
