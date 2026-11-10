"use client";

/**
 * FencedText — draw a text that holds «fenced» names, without the marks.
 *
 * `lib/fencedText.ts` splits the text. This component draws each name:
 *
 * - **Inside a Projects turn** (an `EntityIndexContext` is provided), a name
 *   is the shared pill (`ChatEntityPill`), the same pill the answer draws.
 * - **Anywhere else** it is a quiet emphasis: foreground ink and medium
 *   weight, so the name reads as a name and its boundary stays visible. A
 *   task name with its number (`#5 «X»`) is the task pill once the lookup
 *   finds that task (`FencedName`).
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
import { FencedTaskRef } from "@/components/ChatTaskRef";
import { splitFenced } from "@/lib/fencedText";

/** The quiet emphasis of a name that no pill draws. Exported for its test. */
export const FENCED_NAME_CLASS = "font-medium text-foreground";

/** A task number as the skills print it before a name: `#247`. */
const TASK_NUMBER = /^#[1-9][0-9]{0,8}$/;

/**
 * One fenced name with no pill index. A name with a task number before it
 * (`#247 «Build the memory store»`) is how every skill names a task
 * (`_ref` in `skill_projects`), so it draws as the task pill that opens the
 * task, when one visible task carries that number AND that title. Until the
 * lookup answers, and when it finds no single task, it is the quiet emphasis
 * (owner report, 2026-10-19: the From and To of a "Link these tasks?" card
 * did not open the tasks). `live` is false while an answer streams, and
 * `scope` is the chat session that keys the lookup's cache.
 */
export function FencedName({
  text,
  number,
  live = true,
  scope,
}: {
  text: string;
  number?: string;
  live?: boolean;
  scope?: string;
}) {
  const plain = (
    <span data-fenced-name="" className={FENCED_NAME_CLASS}>
      {number && <span className="text-muted-foreground">{number} </span>}
      {text}
    </span>
  );
  if (!number || !TASK_NUMBER.test(number)) return plain;
  return <FencedTaskRef refText={number} title={text} live={live} scope={scope} fallback={plain} />;
}

export default function FencedText({
  text,
  pills,
  links = true,
}: {
  text: string;
  /** Force the pills off (`false`). Absent: pills when a Projects turn provides an index. */
  pills?: boolean;
  /** False inside a control (a fold's toggle is a `<button>`): no task pill
   *  link, because a link inside a button is two controls in one. */
  links?: boolean;
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
          <FencedName key={i} text={p.text} number={p.number} live={links} />
        ),
      )}
    </>
  );
}
