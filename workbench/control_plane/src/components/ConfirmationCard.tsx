"use client";

/**
 * ConfirmationCard — the card a gated tool parks on until the member answers.
 *
 * The agent emits `confirmation_requested` (`request_confirmation` in
 * `acb_skills/ask_tools.py`) with a `title`, a one-line `detail` and a
 * `context` body. This card draws them inline in the chat with Approve and
 * Reject.
 *
 * The look (owner, 2026-10-06: the amber and the warning sign "look a little
 * odd"): a neutral card surface, Approve in the primary action colour, a
 * quiet Reject, and no emoji. A summary line comes first. The `context`
 * body draws as labelled rows in the UI font when it is the `key: value`
 * lines the Projects and CRM tools write, and a field that only holds a
 * UUID is hidden, because a member cannot read one.
 *
 * ⚠️ There is no destructive tone. The event names no tool and carries no
 * risk annotation (`acb_skills.tool_annotations` holds one per tool, and it
 * does not reach the card), so the card cannot tell a send from a create.
 * Do not guess it from the title.
 *
 * The words (owner report, 2026-10-07). On a card the server fenced
 * (`fenced`), no text shows a «mark»: the title, the detail, the notes and
 * the rows draw through `FencedText`. A free-text body (an email) is always
 * drawn exactly as it will be sent.
 * A field's key reads in product words, and its value draws by its kind
 * (`lib/cardFields.ts`, `CardFieldValue.tsx`): a tag pill, a status chip, a
 * task pill, a person, a date. Numbered keys (`task 1`, `task 2`) are one
 * list. A field that only repeats the detail is not drawn twice.
 *
 * Fences: `src/lib/confirmationQueue.test.ts` (the parse, the hidden ids and
 * the paint) and `src/lib/cardFields.test.ts` (the words and the kinds).
 */

import { useEffect, useRef, useState } from "react";

import CardFieldValue from "@/components/CardFieldValue";
import FencedText from "@/components/FencedText";
import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import Icon from "@/components/Icon";
import { cardKey, fieldSpec, repeatsLine } from "@/lib/cardFields";
import { canApprove, rowsSummary, type ConfirmationRow } from "@/lib/confirmationQueue";
import { unfenced } from "@/lib/fencedText";

/**
 * How long a new card ignores Approve and Reject.
 *
 * In a queue, the next card mounts in the place of the one just answered,
 * and cards of one kind put Approve at the same pixel. So the second click
 * of a double-click would sign a card the member never saw. The buttons do
 * not grey out, so the card does not flash. They only ignore the click.
 * Fence: `src/lib/confirmationQueue.test.ts` ("a new card is not armed").
 */
export const ARM_MS = 400;

export interface CardField {
  /** The key as the tool printed it (`due_at`, `task 1`). */
  key: string;
  label: string;
  /** The value as the tool printed it, marks and all. The card draws it. */
  value: string;
  /** A numbered run (`task 1`, `task 2`, …) as one field: every value. */
  values?: string[];
}

export interface CardBody {
  /** Lines before the first field: the tool's fixed note. */
  notes: string[];
  fields: CardField[];
  /** Lines after the fields: a truncation marker. */
  trailing: string[];
  /** The whole body as text, when it is not `key: value` lines (an email). */
  text?: string;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** `key: value`. The key holds no colon and no fence, so a fenced title
 *  that holds a colon stays one value. */
const FIELD = /^([^:«»]{1,48}):\s(.*)$/;
/** The tools write the truncation marker last. */
const TRUNCATED = /^…\s*\[truncated/;
/** The mark of a list row at the start of a key: `- From` reads `From`. */
const LIST_MARK = /^-\s+/;

/** `«Fix the extruder»` → `Fix the extruder`. A value with more than one
 *  fence (`«a» → «b»`) keeps them, because they mark where each part ends. */
function unfence(value: string): string {
  const m = /^«([^«»]*)»$/.exec(value.trim());
  return m ? m[1] : value.trim();
}

/** A `*_id` field whose value is a UUID, or a change from one UUID to
 *  another. The member reads names, and the payload sends the id anyway. */
export function isHiddenField(key: string, value: string): boolean {
  if (!/(^|_)id$/i.test(key.trim())) return false;
  return value
    .split("→")
    .map((part) => unfence(part))
    .every((part) => UUID.test(part));
}

/** The fields, less any field whose value only repeats a line the card shows. */
export function withoutRepeats(fields: CardField[], shown: (string | undefined)[]): CardField[] {
  return fields.filter((f) => f.values !== undefined || !repeatsLine(f.value, shown));
}

/**
 * The run a numbered key belongs to: `task 3` → `task`. Only a word then a
 * number makes a run, and a custom field never does: `field Q3 target`
 * after `field Budget` are two fields, each with its own label (review
 * round 1: they merged into one row and the Budget label was lost).
 * Exported for its test.
 */
export function runKey(key: string): string {
  const k = key.trim();
  if (/^field\s/i.test(k)) return "";
  const m = /^([a-z][a-z ]*?)\s+\d+$/i.exec(k);
  return m ? m[1].toLowerCase() : "";
}

/** A note that leads into the rows below it: it ends with a colon. */
export function isLeadIn(note: string): boolean {
  return /:\s*$/.test(note);
}

/**
 * The notes of a body, split by where they draw. A lead-in ("Each recipient
 * of this draft:") draws above the rows. Every other note draws after them.
 * With no rows, every note draws after the text.
 */
export function splitNotes(body: CardBody): { leadIn: string[]; after: string[] } {
  if (body.fields.length === 0) return { leadIn: [], after: body.notes };
  return { leadIn: body.notes.filter(isLeadIn), after: body.notes.filter((n) => !isLeadIn(n)) };
}

/** The plural of a numbered field's label: "Task" → "Tasks". */
function plural(label: string): string {
  return /s$/.test(label) ? label : `${label}s`;
}

/** The `context` body as rows, or as text when it is not rows. */
export function parseCardBody(context: string | undefined): CardBody {
  const lines = (context ?? "").split("\n").map((l) => l.trimEnd()).filter((l) => l.trim());
  const first = lines.findIndex((l) => FIELD.test(l));
  const rest = first < 0 ? [] : lines.slice(first);
  // Rows only when the body IS rows: at most one fixed note, then every line
  // a field (or the truncation marker). An email body that happens to hold
  // a colon, or a field whose value spans lines, stays text, and loses no
  // word.
  const rows =
    first >= 0 && first <= 1 && rest.every((l) => FIELD.test(l) || TRUNCATED.test(l.trim()));
  if (!rows) {
    return { notes: [], fields: [], trailing: [], text: lines.length ? (context ?? "").trim() : undefined };
  }
  const fields: CardField[] = [];
  const trailing: string[] = [];
  for (const line of rest) {
    const m = FIELD.exec(line);
    if (!m) {
      trailing.push(line.trim());
      continue;
    }
    // A list row (`- From: …`, the send and forward cards of the email
    // assistant) reads by its key, not by the hyphen before it.
    const key = m[1].trim().replace(LIST_MARK, "");
    if (isHiddenField(key, m[2])) continue;
    const value = m[2].trim();
    const numbered = runKey(key);
    const last = fields[fields.length - 1];
    if (numbered && last && runKey(last.key) === numbered) {
      last.values = [...(last.values ?? [last.value]), value];
      last.label = plural(fieldSpec(key).label);
      continue;
    }
    fields.push({ key, label: fieldSpec(key).label, value });
  }
  return { notes: lines.slice(0, first), fields, trailing };
}

/**
 * The one-line summary, and the rest of the detail line.
 *
 * The tools write `title` as a question about "this" thing ("Create this
 * task?") and put the thing's fenced name first in `detail`. Those join
 * into one plain line: `Create task «Fix the extruder»`. Any other card
 * keeps its title, and its whole detail goes under it.
 */
export function cardSummary(title: string, detail?: string): { summary: string; rest?: string } {
  const parts = (detail ?? "").split(" · ").map((p) => p.trim()).filter(Boolean);
  const m = /^(.+?) this (.+?)\?$/.exec(title.trim());
  if (m && parts.length > 0 && parts[0].includes("«")) {
    const rest = parts.slice(1).join(" · ");
    return { summary: `${m[1]} ${m[2]} ${parts[0]}`, rest: rest || undefined };
  }
  return { summary: title, rest: detail?.trim() || undefined };
}

export interface CardPosition {
  index: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
}

interface ConfirmationCardProps {
  title: string;
  detail?: string;
  /** The body: `key: value` rows, or free text. */
  context?: string;
  /**
   * WS-46 P13 one-card: one checkbox per row, ticked as the tool asks. The
   * summary counts the ticked rows, Approve stays off with none ticked, and
   * Approve hands the ticked ids, in the card's order, to `onApprove`. With
   * no rows the card is as before, and `onApprove` gets no ids.
   */
  rows?: ConfirmationRow[];
  /** A tick or an untick of one row. The queue keeps the ticks (`rowTicksReducer`). */
  onToggle?: (id: string, on: boolean) => void;
  /** Approve. For a card with rows, the queue sends the ticks it holds. */
  onApprove: () => void;
  onReject: () => void;
  /** Disable buttons after a choice is made. */
  disabled?: boolean;
  /** Where this card sits in the queue, when more than one is waiting. */
  position?: CardPosition;
  /**
   * The server fenced member values in «marks» (`ask_tools` `fenced`). Only
   * then does the card draw them as tokens. Any other card (an email to
   * send) shows its text exactly as it will be sent, marks included
   * (review round 1: a body's « oui » lost its quotes on the consent card).
   */
  fenced?: boolean;
}

/** A line of card text: fenced names as tokens, or the text as sent. */
function CardText({ text, fenced }: { text: string; fenced: boolean }) {
  return fenced ? <FencedText text={text} pills={false} /> : <>{text}</>;
}

export default function ConfirmationCard({
  title,
  detail,
  context,
  rows,
  onToggle,
  onApprove,
  onReject,
  disabled = false,
  position,
  fenced = false,
}: ConfirmationCardProps) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setArmed(true), ARM_MS);
    return () => clearTimeout(t);
  }, []);
  // The rows come ticked as the member left them (`rowsView` in the queue).
  // The card holds no tick state and no tick logic (PR #691 review): a tick
  // goes up through `onToggle`, and Approve sends what the queue computed.
  const hasRows = rows !== undefined;
  const base = cardSummary(title, detail);
  const summary = rowsSummary(base.summary, rows);
  const rest = base.rest;
  const approvable = canApprove(rows);
  const parsed = parseCardBody(context);
  // A field that repeats the detail or the summary is drawn once (owner
  // report, 2026-10-07: "Impact" restated the detail word for word).
  const body = { ...parsed, fields: withoutRepeats(parsed.fields, [rest, summary]) };
  // A note that ends with a colon leads into the rows ("Each recipient of
  // this draft:"), so it draws above them. Any other note draws after them
  // (the screenshots of follow-up 5 of #766).
  const { leadIn, after: afterNotes } = splitNotes(body);
  // The text box scrolls at `max-h-40`. A long email card hid its files and
  // its note below that line with no sign, so a box that scrolls says so.
  const textRef = useRef<HTMLParagraphElement>(null);
  const [clipped, setClipped] = useState(false);
  useEffect(() => {
    const el = textRef.current;
    setClipped(!!el && el.scrollHeight > el.clientHeight + 1);
  }, [body.text]);
  const many = position && position.total > 1;
  const hasBody =
    hasRows || body.fields.length > 0 || !!body.text || body.notes.length + body.trailing.length > 0;
  const approve = () => {
    if (armed && approvable) onApprove();
  };
  return (
    <section
      aria-label={fenced ? unfenced(summary) : summary}
      data-confirmation-card=""
      className="my-3 rounded-xl border border-border bg-card overflow-hidden"
    >
      <div className={`flex items-start gap-2 px-4 pt-3 ${hasBody ? "" : "pb-3"}`}>
        <Icon name="ShieldCheck" size={14} className="mt-0.5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground break-words">
            <CardText text={summary} fenced={fenced} />
          </p>
          {rest && (
            <p className="mt-0.5 text-xs text-muted-foreground break-words">
              <CardText text={rest} fenced={fenced} />
            </p>
          )}
        </div>
        {many && (
          <div className="flex shrink-0 items-center gap-0.5" data-confirmation-pager="">
            <Button
              variant="ghost"
              size="icon-xs"
              icon="ChevronLeft"
              aria-label="Previous card"
              onClick={position.onPrev}
              disabled={position.index === 0}
            />
            <span className="text-[11px] tabular-nums text-muted-foreground" aria-live="polite">
              {position.index + 1} of {position.total}
            </span>
            <Button
              variant="ghost"
              size="icon-xs"
              icon="ChevronRight"
              aria-label="Next card"
              onClick={position.onNext}
              disabled={position.index >= position.total - 1}
            />
          </div>
        )}
      </div>

      {hasBody && (
        <div className="space-y-2 px-4 py-3">
          {hasRows && (
            <ul className="space-y-1.5" data-confirmation-rows="" aria-label="Rows this approval covers">
              {rows.length === 0 && (
                <li className="text-xs text-muted-foreground">
                  The rows of this card could not be read, so it cannot be approved.
                </li>
              )}
              {rows.map((r) => (
                <li key={r.id}>
                  <label className="flex min-w-0 cursor-pointer items-start gap-2">
                    <Checkbox
                      size="sm"
                      className="mt-0.5"
                      checked={r.checked}
                      disabled={disabled}
                      onChange={(e) => onToggle?.(r.id, e.target.checked)}
                      data-confirmation-row={r.id}
                    />
                    <span className="flex min-w-0 flex-col gap-0.5">
                      <span className="break-words text-xs text-foreground">
                        <CardText text={r.label} fenced={fenced} />
                      </span>
                      {r.hint && (
                        <span className="whitespace-pre-wrap break-words text-[11px] text-muted-foreground">
                          <CardText text={r.hint} fenced={fenced} />
                        </span>
                      )}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          {leadIn.map((note, i) => (
            <p key={`lead-${i}`} className="text-[11px] text-muted-foreground">
              <CardText text={note} fenced={fenced} />
            </p>
          ))}
          {body.fields.length > 0 && (
            <dl className="space-y-1">
              {body.fields.map((f, i) => {
                const spec = fieldSpec(f.key);
                return (
                  <div key={`${f.key}-${i}`} className="flex gap-3 text-xs" data-card-field={cardKey(f.key)}>
                    <dt className="w-24 shrink-0 pt-px text-muted-foreground">{f.label}</dt>
                    <dd className="min-w-0 flex-1 whitespace-pre-wrap break-words text-foreground">
                      {f.values ? (
                        <ul className="space-y-1">
                          {f.values.map((v, k) => (
                            <li key={k} className="min-w-0">
                              <CardFieldValue value={v} kind={spec.kind} many={spec.many} fenced={fenced} />
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <CardFieldValue value={f.value} kind={spec.kind} many={spec.many} fenced={fenced} />
                      )}
                    </dd>
                  </div>
                );
              })}
            </dl>
          )}
          {body.text && (
            <p
              ref={textRef}
              className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words text-xs text-foreground"
            >
              {body.text}
            </p>
          )}
          {body.text && clipped && (
            <p className="flex items-center gap-1 text-[11px] text-muted-foreground" data-card-more="">
              <Icon name="ChevronsDown" size={12} />
              Scroll the text above to read all of it.
            </p>
          )}
          {[...afterNotes, ...body.trailing].map((note, i) => (
            <p key={i} className="text-[11px] text-muted-foreground">
              <CardText text={note} fenced={fenced} />
            </p>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-2.5">
        <Button
          size="sm"
          icon="Check"
          onClick={approve}
          disabled={disabled || !approvable}
          aria-disabled={!armed || undefined}
          data-confirmation-approve=""
        >
          Approve
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => armed && onReject()}
          disabled={disabled}
          aria-disabled={!armed || undefined}
        >
          Reject
        </Button>
        <span className="ml-auto text-[10px] text-muted-foreground">
          Agent is waiting for your decision
        </span>
      </div>
    </section>
  );
}
