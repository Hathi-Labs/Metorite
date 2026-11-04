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
 * Nothing hides. The summary names each ticked To in full. Each notice shows.
 * The text box grows to hold the whole text, so no line sits under an inner
 * scroll. Fences: `src/components/newMailCard.test.ts`, `src/lib/replyCard.test.ts`.
 */

import { useLayoutEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import { AddressRow } from "@/components/ReplyCardForm";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import { Input, Textarea } from "@/components/ui/Input";
import {
  bodyCanGo,
  leftOutText,
  lineCount,
  MAX_REPLY_BODY,
  MAX_SUBJECT,
  newMailAnswer,
  newMailHint,
  newMailSummary,
  NUDGE_CHIPS,
  NUDGE_EDITED,
  NUDGE_LEAD,
  NUDGE_TEXT,
  newMailEdited,
  recipientsFor,
  sendsCount,
  startFileTicks,
  startTicks,
  toggleFileTick,
  subjectCanGo,
  tickedFor,
  toggleTick,
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
}

/**
 * The nudge line under the text (owner, 2026-11-03): the member can also ask
 * the chat for a change. Each chip puts its words in the chat box through
 * `onSuggest`, and that is all it does: it sends nothing, and it answers no
 * card. With no `onSuggest`, the line shows with no chips.
 *
 * `edited` (fix round 1, P1): the member changed the subject or the text. A
 * request in the chat would start again from the assistant's draft and drop
 * those edits, so the chips hide and the line says so (`NUDGE_EDITED`).
 */
export function NudgeLine({
  onSuggest,
  disabled = false,
  edited = false,
}: {
  onSuggest?: (text: string) => void;
  disabled?: boolean;
  edited?: boolean;
}) {
  return (
    <div
      className="flex flex-wrap items-center gap-1.5 rounded-md bg-muted/40 px-2.5 py-1.5 text-xs glass:rounded-[10px] glass:bg-secondary glass:px-3 glass:py-2"
      data-new-mail-nudge={edited ? "edited" : ""}
    >
      <Icon name="Sparkles" size={12} className="shrink-0 text-muted-foreground" />
      {edited ? (
        <span className="text-foreground">{NUDGE_EDITED}</span>
      ) : (
        <span className="text-foreground">
          <span className="font-medium">{NUDGE_LEAD}</span>{" "}
          <span className="text-muted-foreground">{NUDGE_TEXT}</span>
        </span>
      )}
      {onSuggest &&
        !edited &&
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
}: NewMailCardFormProps) {
  const [subject, setSubject] = useState(form.subject);
  const [body, setBody] = useState(form.body);
  const [ticks, setTicks] = useState<ReplyTicks>(() => startTicks(form));
  // The file rows of a held new mail (fix round 1): a ticked file goes.
  const [fileTicks, setFileTicks] = useState<string[]>(() => startFileTicks(form));
  const box = useRef<HTMLTextAreaElement>(null);
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
  const ok = bodyCanGo(body) && subjectCanGo(subject) && picked.to.length > 0;
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
        <p className="break-words text-xs font-medium leading-relaxed text-foreground glass:text-label" data-reply-summary="">
          {newMailSummary(form, ticks)}
        </p>
        {detail && <p className="mt-0.5 break-words text-[11px] text-muted-foreground">{detail}</p>}
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
        maxLength={MAX_REPLY_BODY}
        onChange={(e) => setBody(e.target.value)}
        disabled={disabled}
        className="w-full resize-none overflow-hidden text-xs"
      />
      <p className="text-[10px] text-muted-foreground" data-reply-lines="">
        {lines === 1 ? "1 line" : `${lines} lines`}. The whole text shows above.
      </p>

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

      <NudgeLine onSuggest={onSuggest} disabled={disabled} edited={newMailEdited(form, subject, body)} />

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
