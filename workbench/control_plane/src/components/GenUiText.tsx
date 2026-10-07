"use client";

/**
 * One string field of a generative-UI node, as SAFE inline Markdown (owner
 * report, 2026-10-07: a list drew `**bold**` and `«name»` as raw marks).
 *
 * Bold, italic, inline code and links render. A «name» is a pill inside a
 * Projects turn and a quiet emphasis elsewhere. HTML stays text, an image is
 * dropped, and a link keeps the chat's link rules (`MarkdownBody` `inline`).
 * This is the one chat renderer, never a second one: `GenerativeUINode` and
 * the templates in `genUITemplates.tsx` both use it.
 *
 * Fence: `src/components/genUiInlineText.test.ts`.
 */

import { useContext } from "react";

import { EntityIndexContext } from "@/components/ChatEntityPill";
import { MarkdownBody } from "@/components/MarkdownMessage";

export default function GenUiText({ text }: { text: string }) {
  const index = useContext(EntityIndexContext);
  if (!text) return null;
  return (
    <MarkdownBody
      content={text}
      inline
      entityPills={index !== null}
      entityIndex={index ?? undefined}
      fences
    />
  );
}
