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
      return <EntityPill fit kind="task" label={text} number={item.number} />;
    case "tag":
      return <EntityPill fit kind="tag" label={text} />;
    case "status":
      // A phrase in the place of a name ("the default") is words, not a lane.
      if (/^the\s/i.test(text)) return <span className="text-foreground">{text}</span>;
      return <EntityPill fit kind="status" label={text} accent={statusAccent({ name: text })} />;
    case "category":
      return (
        <EntityPill fit kind="status" label={formatWords(text)} accent={statusAccent({ category: text, name: text })} />
      );
    case "person": {
      if (text.startsWith("agent:")) return <EntityPill fit kind="agent" label={text.slice(6)} />;
      return <EntityPill fit kind="person" label={text} email={EMAIL.test(text) ? text : undefined} />;
    }
    case "project":
      return <EntityPill fit kind="project" label={text} />;
    // A value in the value column is already set apart by its label, so it
    // draws in the body weight. Only a name inside running text is emphasised.
    case "date":
      return <span className="text-foreground">{formatCardDate(text)}</span>;
    case "minutes":
      return <span className="text-foreground">{formatMinutes(text)}</span>;
    case "flag":
      return <span className="text-foreground">{formatPlain(text)}</span>;
    case "words":
      return <span className="text-foreground">{formatWords(text)}</span>;
    default:
      return (
        <span className="text-foreground">
          {item.number && <span className="text-muted-foreground">{item.number} </span>}
          {text}
        </span>
      );
  }
}

/** The kinds that draw as a pill or a chip. */
const CHIP_KINDS: ReadonlySet<FieldKind> = new Set(["task", "tag", "status", "category", "person", "project"]);

/**
 * One side of a value. Each item is ONE box, so a phrase with a name inside
 * it stays one run of text (a flex gap between its parts read as
 * "Priya ( priya@x.io )"). Several chips sit in a wrapping row with a gap.
 */
function Side({ items, kind, muted = false }: { items: ValueItem[]; kind: FieldKind; muted?: boolean }) {
  return (
    <span
      className={`inline-flex min-w-0 max-w-full flex-wrap items-center gap-1 ${muted ? "text-muted-foreground opacity-80" : ""}`}
    >
      {items.map((item, i) => (
        // `inline-flex max-w-full` lets a long pill shrink to the column and
        // truncate (its tooltip holds the whole name), at 390px too.
        <span key={i} className={CHIP_KINDS.has(kind) && item.named ? "inline-flex min-w-0 max-w-full" : "min-w-0"}>
          <ValueElement item={item} kind={kind} />
          {i < items.length - 1 && kind === "text" && ","}
        </span>
      ))}
    </span>
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
    <span className="inline-flex min-w-0 max-w-full flex-wrap items-center gap-1" title={parsed.plain}>
      {parsed.before && (
        <>
          <Side items={parsed.before} kind={kind} muted />
          <Icon name="ArrowRight" size={12} className="shrink-0 text-muted-foreground" aria-label="changes to" />
        </>
      )}
      <Side items={parsed.after} kind={kind} />
    </span>
  );
}
