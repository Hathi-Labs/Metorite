"use client";

/**
 * FencedText — draw a text that holds «fenced» names, without the marks.
 *
 * `lib/fencedText.ts` splits the text. This component draws each name:
 *
 * - **Inside a Projects turn** (an `EntityIndexContext` is provided), a name
 *   is the shared pill (`ChatEntityPill`), the same pill the answer draws.
 * - **Anywhere else** it is a quiet emphasis: foreground ink and medium
 *   weight, so the name reads as a name and its boundary stays visible.
 *
 * It never shows a guillemet, and it builds no HTML: every part is a React
 * string child. Use it on every surface where server text reaches the
 * member and that is not Markdown — a confirmation card, a receipt, a
 * generative-UI field. Markdown has its own path (`remarkEntityPills`).
 *
 * Fence: `src/lib/fencedText.test.ts`.
 */

import { Fragment, useContext } from "react";

import ChatEntityPill, { EntityIndexContext } from "@/components/ChatEntityPill";
import { splitFenced } from "@/lib/fencedText";

/** The quiet emphasis of a name that no pill draws. Exported for its test. */
export const FENCED_NAME_CLASS = "font-medium text-foreground";

export function FencedName({ text, number }: { text: string; number?: string }) {
  return (
    <span data-fenced-name="" className={FENCED_NAME_CLASS}>
      {number && <span className="text-muted-foreground">{number} </span>}
      {text}
    </span>
  );
}

export default function FencedText({
  text,
  pills,
}: {
  text: string;
  /** Force the pills off (`false`). Absent: pills when a Projects turn provides an index. */
  pills?: boolean;
}) {
  const index = useContext(EntityIndexContext);
  const parts = splitFenced(text);
  const asPills = pills !== false && index !== null;
  return (
    <>
      {parts.map((p, i) =>
        p.kind === "text" ? (
          <Fragment key={i}>{p.text}</Fragment>
        ) : asPills ? (
          <ChatEntityPill key={i} text={p.text} number={p.number} />
        ) : (
          <FencedName key={i} text={p.text} number={p.number} />
        ),
      )}
    </>
  );
}
