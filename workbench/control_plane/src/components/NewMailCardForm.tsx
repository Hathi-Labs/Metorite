"use client";

/**
 * NewMailCardForm — the body of the one email card of a NEW mail
 * (`email_app_master_plan.md` §18.10, owner 2026-11-03).
 *
 * It draws inside `ConfirmationCard`, under the shared header, in the place of
 * `ReplyCardForm`. It is the same card with the same answer frame: the people
 * draw through `ReplyCardForm`'s `AddressRow`, and the rules live in
 * `lib/replyCard.ts`. This file holds the member's edits only.
 *
 * The member ticks the people, edits the subject and the text, and presses
 * Send, Save draft or Discard. The server took the people and the files from
 * its own record of this card. So a tick can take a person off and never add
 * one, and the files are fixed lines. A new person is added by asking in the
 * chat, never on the card (owner decision).
 *
 * One nudge line under the text says the member can also ask the chat. Its
 * two chips put their words in the chat box (`onSuggest`) and send nothing.
 * A member who then sends from the chat closes this card, and the assistant
 * shows a new one (`close_on_steer`).
 *
 * §18.11: the form reports the member's version after each edit
 * (`onDraftChange`, `lib/redraftCard.ts`), so a request in the chat carries
 * it. A card that revises another shows "Draft <n>" and a "Changed:" line,
 * which the client computes from its own copy of the member's last version.
 *
 * Nothing hides. The summary names each ticked To in full. Each notice shows.
 * The text box grows to hold the whole text, so no line sits under an inner
 * scroll. Fences: `src/components/newMailCard.test.ts`, `src/lib/replyCard.test.ts`.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import { AddressRow } from "@/components/ReplyCardForm";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import { Input, Textarea } from "@/components/ui/Input";
import { CHANGED_LEAD, cardDraftOf, changedText, changesOf, draftLabel, keptOutOf, keptOutText, linePieces, type CardDraft, type CardText, type Changes, type PeopleBefore } from "@/lib/redraftCard";
import {
  leftOutText,
  lineCount,
  newMailBodyCanGo,
  newMailLimitLine,
  newMailMaxBody,
  MAX_SUBJECT,
  newMailAnswer,
  newMailHint,
  newMailSummary,
  NUDGE_CHIPS,
  NUDGE_EDITED,
  NUDGE_EDITED_AT_REST,
  NUDGE_LEAD,
  NUDGE_TEXT,
  newMailEdited,
  newMailTicksMoved,
  recipientsFor,
  sendsCount,
  startFileTicks,
  startTicks,
  toggleFileTick,
  subjectCanGo,
  tickedFor,
  toggleTick,
  type NewMailEdits,
  type ReplyAction,
  type ReplyApproval,
  type ReplyForm,
  type ReplyTicks,
} from "@/lib/replyCard";

export interface NewMailCardFormProps {
  form: ReplyForm;
  /** The tool's one line under the summary: the mailbox and the subject. */
  detail?: string;
  onAnswer: (answer: ReplyApproval) => void;
  onDiscard: () => void;
  /**
   * Put words in the chat box, and focus it. It never sends. With none (a
   * surface with no chat box), the nudge line shows with no chips.
   */
  onSuggest?: (text: string) => void;
  /** False for the first moments of a new card (`ARM_MS`): clicks do nothing. */
  armed: boolean;
  disabled?: boolean;
  /**
   * §18.11: the member's version after each edit, or null when the card shows
   * its own start again. `AgentChat` keeps it for the next chat message.
   */
  onDraftChange?: (draft: CardDraft | null) => void;
  /** §18.11: the member's last version of the card that this card revises. */
  before?: CardText & { people?: PeopleBefore };
  /**
   * §18.11: a message in the chat goes as a steer now, the one path that
   * carries the member's version. False at rest, as after a Stop.
   */
  carriesEdits?: boolean;
  /**
   * The member's edits of this card, from before a change of tab. The queue
   * holds them, because a change of tab unmounts this form.
   */
  savedEdits?: NewMailEdits;
  /** Report the member's edits, for `savedEdits`. */
  onEdits?: (edits: NewMailEdits) => void;
}

/**
 * The "Changed:" line of a redrafted card (§18.11): the words that went, struck
 * through, and the words that came, marked. The client computed it from the
 * member's last version and the new text (`changesOf`). The line and its
 * accessible name draw the same pieces (`linePieces`), so they read alike,
 * with each change as a phrase and the people in sentences of their own.
 */
export function ChangedLine({ changes }: { changes: Changes }) {
  const [lead, ...rest] = linePieces(changes);
  return (
    <p className="break-words text-[11px] leading-relaxed text-muted-foreground" data-redraft-changed="" aria-label={changedText(changes)}>
      <span className="font-medium text-foreground">{"text" in lead ? lead.text.trimEnd() : CHANGED_LEAD.trimEnd()}</span>{" "}
      {rest.map((p, i) =>
        "removed" in p ? (
          <del key={i} className="text-muted-foreground line-through">{p.removed}</del>
        ) : "added" in p ? (
          <mark key={i} className="rounded-sm bg-primary/10 text-foreground">{p.added}</mark>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </p>
  );
}

/**
 * The note on the people that start unticked because the member unticked
 * them on the draft before (§18.11 rule 7). It reads the server's names on
 * the form, so it shows on every path: in a slot, with no slot, and after a
 * remount, where the card has no "Changed:" line.
 */
export function KeptOutLine({ names }: { names: readonly string[] }) {
  const text = keptOutText(names);
  if (!text) return null;
  return (
    <p className="break-words text-[11px] leading-relaxed text-muted-foreground" data-redraft-kept-out="">
      {text}
    </p>
  );
}

/**
 * The nudge line under the text (owner, 2026-11-03): the member can also ask
 * the chat for a change. Each chip puts its words in the chat box through
 * `onSuggest`, and that is all it does: it sends nothing, and it answers no
 * card. With no `onSuggest`, the line shows with no chips.
 *
 * `edited`: the member changed the card. While a run is live, a request in
 * the chat carries those edits (§18.11), so the line says so
 * (`NUDGE_EDITED`), and the chips stay. With `carriesEdits` false, as after a
 * Stop, a plain send carries none. The line says that
 * (`NUDGE_EDITED_AT_REST`), and the chips hide, so no chip leads the member
 * to lose the edits.
 */
export function NudgeLine({
  onSuggest,
  disabled = false,
  edited = false,
  carriesEdits = false,
}: {
  onSuggest?: (text: string) => void;
  disabled?: boolean;
  edited?: boolean;
  carriesEdits?: boolean;
}) {
  const lost = edited && !carriesEdits;
  return (
    <div
      className="flex flex-wrap items-center gap-1.5 rounded-md bg-muted/40 px-2.5 py-1.5 text-xs glass:rounded-[10px] glass:bg-secondary glass:px-3 glass:py-2"
      data-new-mail-nudge={lost ? "edited-at-rest" : edited ? "edited" : ""}
    >
      <Icon name="Sparkles" size={12} className="shrink-0 text-muted-foreground" />
      {edited ? (
        <span className={lost ? "min-w-0 flex-1 text-foreground" : "text-foreground"}>{lost ? NUDGE_EDITED_AT_REST : NUDGE_EDITED}</span>
      ) : (
        <span className="text-foreground">
          <span className="font-medium">{NUDGE_LEAD}</span>{" "}
          <span className="text-muted-foreground">{NUDGE_TEXT}</span>
        </span>
      )}
      {onSuggest && !lost &&
        NUDGE_CHIPS.map((chip) => (
          <Button
            key={chip}
            variant="secondary"
            size="sm"
            onClick={() => onSuggest(chip)}
            disabled={disabled}
            data-nudge-chip={chip}
          >
            {`“${chip}”`}
          </Button>
        ))}
    </div>
  );
}

export default function NewMailCardForm({
  form,
  detail,
  onAnswer,
  onDiscard,
  onSuggest,
  armed,
  disabled = false,
  onDraftChange,
  before,
  carriesEdits = false,
  savedEdits,
  onEdits,
}: NewMailCardFormProps) {
  // A change of tab unmounts this form. Each state starts from the member's
  // edits, or an untick comes back ticked, and Send goes to that person.
  const [subject, setSubject] = useState(savedEdits?.subject ?? form.subject);
  const [body, setBody] = useState(savedEdits?.body ?? form.body);
  const [ticks, setTicks] = useState<ReplyTicks>(() => savedEdits?.ticks ?? startTicks(form));
  // The file rows of a held new mail (fix round 1): a ticked file goes.
  const [fileTicks, setFileTicks] = useState<string[]>(() => savedEdits?.fileTicks ?? startFileTicks(form));
  const keepEdits = useRef(onEdits);
  useEffect(() => { keepEdits.current = onEdits; }, [onEdits]);
  useEffect(() => {
    keepEdits.current?.({ subject, body, ticks, fileTicks });
  }, [subject, body, ticks, fileTicks]);
  const box = useRef<HTMLTextAreaElement>(null);
  // §18.11 step 1: the member's version after each edit, for the next chat
  // message. A ref holds the callback, so a new callback runs no report.
  const report = useRef(onDraftChange);
  useEffect(() => { report.current = onDraftChange; }, [onDraftChange]);
  useEffect(() => {
    report.current?.(cardDraftOf(form, form.requestId ?? "", { subject, body, ticks }));
  }, [form, subject, body, ticks]);
  const label = draftLabel(form.draft);
  // The new text against the member's last version: never a claim of the model.
  const offered = recipientsFor(form, true);
  const changes = before
    ? changesOf(before, {
        subject: form.subject,
        body: form.body,
        people: {
          all: [...offered.to, ...offered.cc, ...(offered.bcc ?? [])].map((a) => a.toLowerCase()),
          ticked: startTicks(form).reply_all,
        },
      })
    : null;
  // The box is as tall as its text, so no line hides under an inner scroll.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`;
  }, [body]);
  const shown = recipientsFor(form, true);
  const picked = tickedFor(form, true, ticks);
  const ticked = new Set(ticks.reply_all);
  const onTick = (a: string) => setTicks((t) => toggleTick(t, true, a));
  // A new mail needs one ticked To, a text and a subject on one line.
  const ok = newMailBodyCanGo(form, body) && subjectCanGo(subject) && picked.to.length > 0;
  const limit = newMailLimitLine(form, body);
  const lines = lineCount(body);
  const answer = (action: ReplyAction) => {
    if (!armed) return;
    const a = newMailAnswer(form, { action, subject, body, ticks, fileTicks });
    if (a) onAnswer(a);
  };
  const files = form.files ?? [];
  return (
    <div className="space-y-2.5" data-reply-card="" data-new-mail-card="">
      <div className="min-w-0">
        <div className="flex min-w-0 items-start gap-2">
          <p className="min-w-0 flex-1 break-words text-xs font-medium leading-relaxed text-foreground glass:text-label" data-reply-summary="">
            {newMailSummary(form, ticks)}
          </p>
          {label && (
            <span className="shrink-0" data-redraft-label="">
              <Badge size="xs">{label}</Badge>
            </span>
          )}
        </div>
        {detail && <p className="mt-0.5 break-words text-[11px] text-muted-foreground">{detail}</p>}
        {changes && <ChangedLine changes={changes} />}
        <KeptOutLine names={keptOutOf(form)} />
      </div>

      {form.notices.length > 0 && (
        <ul className="space-y-1 rounded-md border border-border px-2.5 py-1.5 glass:rounded-[10px] glass:border-transparent glass:bg-secondary glass:px-3 glass:py-2" aria-label="Before you send">
          {form.notices.map((n, i) => (
            <li key={i} className="break-words text-xs text-foreground" data-reply-notice="">
              {n}
            </li>
          ))}
        </ul>
      )}

      <dl className="space-y-1" aria-label="Recipients">
        <AddressRow label="To" list={shown.to} form={form} ticked={ticked} onTick={onTick} disabled={disabled} />
        {shown.cc.length > 0 && (
          <AddressRow label="Cc" list={shown.cc} form={form} ticked={ticked} onTick={onTick} disabled={disabled} />
        )}
        {(shown.bcc ?? []).length > 0 && (
          <AddressRow label="Bcc" list={shown.bcc ?? []} form={form} ticked={ticked} onTick={onTick} disabled={disabled} />
        )}
      </dl>
      <p className="text-[11px] text-muted-foreground" data-reply-count="">
        {sendsCount(form, true, ticks)}. Only a ticked person gets the email. To add a person, ask in the chat.
      </p>
      {leftOutText(form, ticks) && (
        <p className="break-words text-[11px] text-foreground" data-reply-left-out="">
          {leftOutText(form, ticks)}
        </p>
      )}

      <label className="flex items-center gap-2 text-xs" data-new-mail-subject="">
        <span className="shrink-0 text-muted-foreground">Subject</span>
        <Input
          inputSize="sm"
          aria-label="Subject"
          value={subject}
          maxLength={MAX_SUBJECT}
          onChange={(e) => setSubject(e.target.value.replace(/[\r\n\u2028\u2029]+/g, " "))}
          disabled={disabled}
          className="min-w-0 flex-1"
        />
      </label>

      {form.note && <p className="text-[11px] text-muted-foreground">{form.note}</p>}
      <Textarea
        ref={box}
        aria-label="Email text"
        rows={Math.max(4, lines)}
        value={body}
        maxLength={newMailMaxBody(form)}
        onChange={(e) => setBody(e.target.value)}
        disabled={disabled}
        className="w-full resize-none overflow-hidden text-xs"
      />
      <p className="text-[10px] text-muted-foreground" data-reply-lines="">
        {lines === 1 ? "1 line" : `${lines} lines`}. The whole text shows above.
      </p>
      {limit && (
        <p className="text-[11px] text-muted-foreground" data-new-mail-limit="">
          {limit}
        </p>
      )}

      {form.fileRows && form.fileRows.length > 0 && (
        <ul className="space-y-1" aria-label="Files of the email" data-new-mail-file-rows="">
          {form.fileRows.map((r) => (
            <li key={r.id}>
              <label className="flex min-w-0 cursor-pointer items-start gap-1.5 text-xs text-foreground">
                <Checkbox
                  size="sm"
                  className="mt-0.5"
                  checked={fileTicks.includes(r.id)}
                  onChange={() => setFileTicks((t) => toggleFileTick(t, r.id))}
                  disabled={disabled}
                  aria-label={`Send the file ${r.label}`}
                  data-new-mail-file-tick={r.id}
                />
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="break-words">{r.label}</span>
                  {r.hint && <span className="break-words text-[11px] text-muted-foreground">{r.hint}</span>}
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}
      {!form.fileRows && files.length > 0 && (
        <ul className="space-y-0.5" aria-label="Files that go with the email" data-new-mail-files="">
          {files.map((f, i) => (
            <li key={i} className="flex min-w-0 items-center gap-1.5 break-words text-xs text-foreground">
              <Icon name="Paperclip" size={12} className="shrink-0 text-muted-foreground" />
              <span className="min-w-0 break-words">{f}</span>
            </li>
          ))}
        </ul>
      )}

      <NudgeLine
        onSuggest={onSuggest}
        disabled={disabled}
        edited={newMailEdited(form, subject, body) || newMailTicksMoved(form, ticks)}
        carriesEdits={carriesEdits}
      />

      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          icon="Send"
          onClick={() => answer("send")}
          disabled={disabled || !ok}
          aria-disabled={!armed || undefined}
          data-reply-send=""
        >
          Send
        </Button>
        <Button
          variant="secondary"
          size="sm"
          icon="Save"
          onClick={() => answer("save")}
          disabled={disabled || !ok}
          aria-disabled={!armed || undefined}
          data-reply-save=""
        >
          Save draft
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => armed && onDiscard()}
          disabled={disabled}
          aria-disabled={!armed || undefined}
          data-reply-discard=""
        >
          Discard
        </Button>
      </div>
      <p className="text-[10px] text-muted-foreground">{newMailHint(form)}</p>
    </div>
  );
}
