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
 * Fences: `src/lib/confirmationQueue.test.ts` (the parse, the hidden ids and
 * the paint).
 */

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";

export interface CardField {
  label: string;
  value: string;
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

function label(key: string): string {
  const words = key.trim().replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
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
    } else if (!isHiddenField(m[1], m[2])) {
      fields.push({ label: label(m[1]), value: unfence(m[2]) });
    }
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
  onApprove: () => void;
  onReject: () => void;
  /** Disable buttons after a choice is made. */
  disabled?: boolean;
  /** Where this card sits in the queue, when more than one is waiting. */
  position?: CardPosition;
}

export default function ConfirmationCard({
  title,
  detail,
  context,
  onApprove,
  onReject,
  disabled = false,
  position,
}: ConfirmationCardProps) {
  const { summary, rest } = cardSummary(title, detail);
  const body = parseCardBody(context);
  const many = position && position.total > 1;
  const hasBody = body.fields.length > 0 || !!body.text || body.notes.length + body.trailing.length > 0;
  return (
    <section
      aria-label={summary}
      data-confirmation-card=""
      className="my-3 rounded-xl border border-border bg-card overflow-hidden"
    >
      <div className={`flex items-start gap-2 px-4 pt-3 ${hasBody ? "" : "pb-3"}`}>
        <Icon name="ShieldCheck" size={14} className="mt-0.5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground break-words">{summary}</p>
          {rest && <p className="mt-0.5 text-xs text-muted-foreground break-words">{rest}</p>}
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
          {body.fields.length > 0 && (
            <dl className="space-y-1">
              {body.fields.map((f, i) => (
                <div key={`${f.label}-${i}`} className="flex gap-3 text-xs">
                  <dt className="w-24 shrink-0 text-muted-foreground">{f.label}</dt>
                  <dd className="min-w-0 flex-1 whitespace-pre-wrap break-words text-foreground">
                    {f.value}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {body.text && (
            <p className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words text-xs text-foreground">
              {body.text}
            </p>
          )}
          {[...body.notes, ...body.trailing].map((note, i) => (
            <p key={i} className="text-[11px] text-muted-foreground">
              {note}
            </p>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-2.5">
        <Button size="sm" icon="Check" onClick={onApprove} disabled={disabled} data-confirmation-approve="">
          Approve
        </Button>
        <Button variant="secondary" size="sm" onClick={onReject} disabled={disabled}>
          Reject
        </Button>
        <span className="ml-auto text-[10px] text-muted-foreground">
          Agent is waiting for your decision
        </span>
      </div>
    </section>
  );
}
