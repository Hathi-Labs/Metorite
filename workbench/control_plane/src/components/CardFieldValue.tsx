"use client";

/**
 * One value on a confirmation card, drawn by its kind (`lib/cardFields.ts`).
 *
 * A tag is the tag pill (the `--cat` ramp), a status is the shared status
 * chip, a task is the task pill with its number muted, a person is a person
 * pill, a project is a project pill, and a date is a formatted date. The
 * pills are the chat's own (`ui/EntityPill.tsx`), so a card and an answer
 * draw a name the same way. A card pill never links: the member is deciding,
 * and a click must not leave the card.
 *
 * A value that does not fit its kind (a phrase, two names with words
 * between) draws through `FencedText`, so it still never shows a mark.
 * A change draws as the old value, muted, an arrow, and the new value.
 */

import { Fragment } from "react";

import FencedText from "@/components/FencedText";
import Icon from "@/components/Icon";
import EntityPill from "@/components/ui/EntityPill";
import {
  type FieldKind,
  type ValueItem,
  formatCardDate,
  formatMinutes,
  formatPlain,
  formatWords,
  parseFieldValue,
} from "@/lib/cardFields";
import { statusAccent } from "@/lib/statusAccent";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** One element of a value, by kind. Exported for its test. */
export function ValueElement({ item, kind }: { item: ValueItem; kind: FieldKind }) {
  const text = item.text;
  if (!item.named) {
    if (kind === "date") return <>{formatCardDate(formatPlain(text))}</>;
    if (kind === "minutes") return <>{formatMinutes(text)}</>;
    if (kind === "flag") return <>{formatPlain(text)}</>;
    const plain = formatPlain(text);
    return plain === "none" ? <span className="text-muted-foreground">none</span> : <FencedText text={text} pills={false} />;
  }
  switch (kind) {
    case "task":
      return <EntityPill kind="task" label={text} number={item.number} />;
    case "tag":
      return <EntityPill kind="tag" label={text} />;
    case "status":
      // A phrase in the place of a name ("the default") is words, not a lane.
      if (/^the\s/i.test(text)) return <span className="font-medium text-foreground">{text}</span>;
      return <EntityPill kind="status" label={text} accent={statusAccent({ name: text })} />;
    case "category":
      return (
        <EntityPill kind="status" label={formatWords(text)} accent={statusAccent({ category: text, name: text })} />
      );
    case "person": {
      if (text.startsWith("agent:")) return <EntityPill kind="agent" label={text.slice(6)} />;
      return <EntityPill kind="person" label={text} email={EMAIL.test(text) ? text : undefined} />;
    }
    case "project":
      return <EntityPill kind="project" label={text} />;
    case "date":
      return <span className="font-medium text-foreground">{formatCardDate(text)}</span>;
    case "minutes":
      return <span className="font-medium text-foreground">{formatMinutes(text)}</span>;
    case "flag":
      return <span className="font-medium text-foreground">{formatPlain(text)}</span>;
    case "words":
      return <span className="font-medium text-foreground">{formatWords(text)}</span>;
    default:
      return (
        <span data-fenced-name="" className="font-medium text-foreground">
          {item.number && <span className="text-muted-foreground">{item.number} </span>}
          {text}
        </span>
      );
  }
}

function Items({ items, kind }: { items: ValueItem[]; kind: FieldKind }) {
  return (
    <>
      {items.map((item, i) => (
        <Fragment key={i}>
          {i > 0 && kind === "text" && ", "}
          <ValueElement item={item} kind={kind} />
        </Fragment>
      ))}
    </>
  );
}

export default function CardFieldValue({
  value,
  kind = "text",
  many = false,
}: {
  value: string;
  kind?: FieldKind;
  many?: boolean;
}) {
  const parsed = parseFieldValue(value, many);
  return (
    <span className="inline-flex min-w-0 flex-wrap items-center gap-1" title={parsed.plain}>
      {parsed.before && (
        <>
          <span className="inline-flex min-w-0 flex-wrap items-center gap-1 text-muted-foreground opacity-80">
            <Items items={parsed.before} kind={kind} />
          </span>
          <Icon name="ArrowRight" size={12} className="shrink-0 text-muted-foreground" aria-label="changes to" />
        </>
      )}
      <Items items={parsed.after} kind={kind} />
    </span>
  );
}
