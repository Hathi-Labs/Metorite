"use client";

/**
 * Readout — a read's result as UI, not as a text dump.
 *
 * It draws the Projects reads, and since the follow-up of #716 and #735 the
 * email and CRM reads too (`EmailToolCards`, `crm/CrmEvidence.tsx`). It
 * moved out of `components/projects/` for that. There is one Readout, and
 * no card file draws the text of a read as it is.
 *
 * `lib/readout.ts` turns the tool's text into blocks with no ids and no
 * `[key]` marks. This file draws them in the chat's own parts:
 *
 * - a heading is a small label with its count;
 * - a `key: value` fact is a labelled row, drawn by kind like a card field
 *   (`CardFieldValue`): a status chip, person pills, tag pills, a date;
 * - a row of the vocabulary is its element: a status is the status chip in
 *   its colour with "default" as a muted mark, a tag is the tag pill, a type
 *   is a badge, a custom field is its name and its type;
 * - any other row is its name with its facts after it, split by a quiet
 *   dot instead of a typed "·".
 *
 * Owner report, 2026-10-07 (the Vocabulary card). The tool result keeps
 * every id, because the model needs them. Only the card drops them.
 */

import { Fragment } from "react";

import CardFieldValue from "@/components/CardFieldValue";
import FencedText from "@/components/FencedText";
import Badge from "@/components/ui/Badge";
import EntityPill from "@/components/ui/EntityPill";
import { fieldSpec, formatWords } from "@/lib/cardFields";
import { unfenced } from "@/lib/fencedText";
import { type ReadoutBlock, parseReadout, statusRow } from "@/lib/readout";
import { statusAccent } from "@/lib/statusAccent";
import { CATEGORY_LABEL } from "@/lib/statusCategory";

type Item = Extract<ReadoutBlock, { kind: "item" }>;

/** The quiet dot between the facts of a row. */
function Sep() {
  return <span aria-hidden className="inline-block size-1 shrink-0 rounded-full bg-muted-foreground/40" />;
}

function Facts({ parts }: { parts: string[] }) {
  return (
    <>
      {parts.map((p, i) => (
        <Fragment key={i}>
          <Sep />
          <span className="text-muted-foreground">
            <FencedText text={p} pills={false} />
          </span>
        </Fragment>
      ))}
    </>
  );
}

function ItemView({ item }: { item: Item }) {
  const section = item.section.toLowerCase();
  const [head = item.text, ...facts] = item.parts;
  if (section.startsWith("statuses")) {
    const s = statusRow(item);
    const name = unfenced(s.name);
    return (
      <span className="inline-flex flex-wrap items-center gap-1.5">
        <EntityPill kind="status" label={name} accent={statusAccent({ category: s.category, name })} />
        {s.isDefault && <span className="text-[10px] text-muted-foreground">default</span>}
      </span>
    );
  }
  if (section.startsWith("tags")) {
    return (
      <span className="inline-flex flex-wrap items-center gap-1.5">
        <EntityPill kind="tag" label={unfenced(head)} />
        {facts.length > 0 && <span className="text-[10px] text-muted-foreground">{facts.join(", ")}</span>}
      </span>
    );
  }
  if (section.startsWith("types")) {
    return (
      <span className="inline-flex flex-wrap items-center gap-1.5">
        <Badge icon="Shapes">{unfenced(head)}</Badge>
        {facts.length > 0 && <span className="text-[10px] text-muted-foreground">{facts.join(", ")}</span>}
      </span>
    );
  }
  if (section.startsWith("custom fields")) {
    // `key <field_key>` is the model's handle, not a word for a person.
    const type = facts.find((f) => f.startsWith("type "))?.slice(5);
    return (
      <span className="inline-flex flex-wrap items-center gap-1.5">
        <span className="font-medium text-foreground">{unfenced(head)}</span>
        {type && <span className="text-[10px] text-muted-foreground">{type.replace(/_/g, " ")}</span>}
      </span>
    );
  }
  return (
    <span className="inline-flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5">
      <span className="text-foreground">
        <FencedText text={head} pills={false} />
      </span>
      {item.tag && <KindChip tag={item.tag} />}
      <Facts parts={facts} />
    </span>
  );
}

/**
 * A row's or a line's `[kind]` ("space", "pending", a CRM stage's "open") as
 * a chip in words, never as the key (follow-up of #716 and #735). The chip
 * is the shared status chip, so a kind with a stage's meaning takes its hue.
 */
/** A stage key reads as its label ("todo" is "To do"), any other kind as words. */
function kindLabel(tag: string): string {
  return Object.hasOwn(CATEGORY_LABEL, tag) ? CATEGORY_LABEL[tag] : formatWords(tag);
}

function KindChip({ tag }: { tag: string }) {
  return (
    <span className="inline-flex align-middle">
      <EntityPill fit kind="status" label={kindLabel(tag)} accent={statusAccent({ category: tag, name: tag })} />
    </span>
  );
}

/** The blocks, grouped: the rows under one heading are one list. */
export default function Readout({ result, legend }: { result: string; legend?: string }) {
  const blocks = parseReadout(result, legend);
  if (blocks.length === 0) return <div className="text-muted-foreground">(no result)</div>;
  const out: React.ReactNode[] = [];
  let items: Item[] = [];
  const flush = () => {
    if (items.length === 0) return;
    const section = items[0].section.toLowerCase();
    const inline = section.startsWith("statuses") || section.startsWith("tags") || section.startsWith("types");
    out.push(
      inline ? (
        <div key={`l${out.length}`} className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          {items.map((it, i) => (
            <ItemView key={i} item={it} />
          ))}
        </div>
      ) : (
        <ul key={`l${out.length}`} className="space-y-1">
          {items.map((it, i) => (
            <li key={i} className="min-w-0">
              <ItemView item={it} />
            </li>
          ))}
        </ul>
      ),
    );
    items = [];
  };
  blocks.forEach((b, i) => {
    if (b.kind === "item") {
      items.push(b);
      return;
    }
    flush();
    if (b.kind === "heading") {
      out.push(
        <div key={i} className="pt-1 text-[11px] font-medium text-foreground first:pt-0">
          <FencedText text={b.text} pills={false} />
          {b.count !== undefined && <span className="ml-1 text-muted-foreground">{b.count}</span>}
        </div>,
      );
    } else if (b.kind === "field") {
      const spec = fieldSpec(b.key);
      out.push(
        <div key={i} className="flex gap-3">
          <span className="w-24 shrink-0 text-muted-foreground">{spec.label}</span>
          <span className="min-w-0 flex-1 break-words text-foreground">
            <CardFieldValue value={b.value} kind={spec.kind} many={spec.many} />
          </span>
        </div>,
      );
    } else {
      out.push(
        <p key={i} className="break-words text-muted-foreground">
          <FencedText text={b.text} pills={false} />
          {b.tag && (
            <span className="ml-1.5">
              <KindChip tag={b.tag} />
            </span>
          )}
        </p>,
      );
    }
  });
  flush();
  return <div className="space-y-1.5 text-[11px]">{out}</div>;
}
