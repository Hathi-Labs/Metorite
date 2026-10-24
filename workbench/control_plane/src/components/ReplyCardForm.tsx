"use client";

/**
 * ReplyCardForm — the body of a reply card (EM-CR, `email_app_master_plan.md` §18).
 *
 * It draws inside `ConfirmationCard`, under its shared header, so a reply
 * card is the ONE confirmation card with a form, not a second card. It shows
 * who gets the reply, the mail it answers, every person the gateway derived
 * or added, the editable body, and Send, Save to Drafts and Discard. The
 * logic is `lib/replyCard.ts`. This file holds the member's edits only.
 *
 * The client derives no recipient. The card shows ONE list with a tick box
 * for each person (owner, 2026-10-24). A hidden second list once sent a
 * reply to one person while the member ticked more. So the Reply and Reply
 * all switch changes the ticks in view, and never the list: Reply ticks the
 * sender alone, Reply all ticks each person but a `new` one. The reply goes
 * only to the ticked people, the card counts them ("Sends to 3 of 5"), and
 * it names each person who will not get it.
 *
 * A Bcc row (§21.15) lists the Bcc that the server recorded for the card,
 * under "Bcc (hidden from the others)". The member can type one more. The
 * server classes it and adds it to the record of THIS card, so the act
 * still takes a subset of the server's list only.
 *
 * Nothing hides (security review of #1191). The summary names each address
 * the reply goes to, in full. Each notice of the server shows: a Reply-To
 * that is not the sender, and the text the send adds. The text box grows
 * to hold the whole body, so no line sits under an inner scroll, and the
 * card counts the lines.
 */

import { useLayoutEffect, useRef, useState } from "react";

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Checkbox from "@/components/ui/Checkbox";
import { Input, Textarea } from "@/components/ui/Input";
import {
  addBcc,
  BCC_LABEL,
  bodyCanGo,
  classOfAddress,
  discardReplyDraft,
  leftOutText,
  lineCount,
  markFor,
  MAX_REPLY_BODY,
  onlySenderTicks,
  receivedText,
  recipientsFor,
  replyAllAddsSomeone,
  replyAnswer,
  replyHint,
  replySummary,
  sendsCount,
  startTicks,
  replyAllTicks,
  tickedFor,
  toggleTick,
  withBcc,
  type ReplyAction,
  type ReplyApproval,
  type ReplyForm,
  type ReplyTicks,
} from "@/lib/replyCard";
import { rowMark } from "@/lib/sendCard";

interface ReplyCardFormProps {
  /** The form, or null when the card could not read it. */
  form: ReplyForm | null;
  /** The tool's one line under the summary: the mailbox and the subject. */
  detail?: string;
  onAnswer: (answer: ReplyApproval) => void;
  onDiscard: () => void;
  /** False for the first moments of a new card (`ARM_MS`): clicks do nothing. */
  armed: boolean;
  disabled?: boolean;
}

function AddressRow({
  label,
  list,
  form,
  ticked,
  onTick,
  disabled,
}: {
  label: string;
  list: string[];
  form: ReplyForm;
  ticked: Set<string>;
  onTick: (address: string) => void;
  disabled: boolean;
}) {
  // A held reply card (§21) marks each person that the server classed as
  // new or as the person of the thread. Each person has a tick box.
  return (
    <div className="flex gap-3 text-xs" data-reply-recipients={label.toLowerCase()}>
      <dt className="w-10 shrink-0 pt-px text-muted-foreground" title={label === "Bcc" ? BCC_LABEL : undefined}>
        {label}
      </dt>
      <dd className="min-w-0 flex-1 space-y-0.5 break-words text-foreground">
        {list.map((a) => {
          const cls = classOfAddress(form, a);
          // The server's own words win (item 8): "on Dr Sagar's original email".
          const mark = markFor(form, a) || (cls ? rowMark(cls) : "");
          const on = ticked.has(a.toLowerCase());
          return (
            <label key={a} className="flex cursor-pointer flex-wrap items-center gap-1.5" data-reply-person={a}>
              <Checkbox
                size="sm"
                checked={on}
                onChange={() => onTick(a)}
                disabled={disabled}
                aria-label={`Send to ${a}`}
                data-reply-tick={a}
              />
              <span className="min-w-0 break-words">{a}</span>
              {mark && (
                <span data-reply-mark={cls}>
                  <Badge tone={cls === "new" ? "warning" : "neutral"} size="xs">
                    {mark}
                  </Badge>
                </span>
              )}
            </label>
          );
        })}
      </dd>
    </div>
  );
}

export default function ReplyCardForm({ form: given, detail, onAnswer, onDiscard, armed, disabled = false }: ReplyCardFormProps) {
  // §21.15: the form grows by each Bcc that the server took for this card.
  const [form, setForm] = useState<ReplyForm | null>(given);
  const [bccDraft, setBccDraft] = useState("");
  const [bccBusy, setBccBusy] = useState(false);
  const [bccError, setBccError] = useState("");
  // ONE list (item 11): the card always shows every person, so the answer
  // names the people of that list that the member ticked. The Reply and
  // Reply all switch (owner amendment, 2026-10-24) only changes the ticks,
  // so the ticks of THIS card are always what goes.
  const replyAll = true;
  const [mode, setMode] = useState<"reply" | "reply-all">(form?.replyAll === false ? "reply" : "reply-all");
  const [body, setBody] = useState(form?.body ?? "");
  const [ticks, setTicks] = useState<ReplyTicks>(() =>
    form ? startTicks(form) : { reply: [], reply_all: [] },
  );
  const box = useRef<HTMLTextAreaElement>(null);
  // The box is as tall as its text, so no line hides under an inner scroll.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`;
  }, [body]);
  if (!form) {
    return (
      <div className="space-y-2">
        <p className="text-xs text-muted-foreground">
          This reply card could not be read, so it cannot send or save anything. Discard it and ask
          for the reply again.
        </p>
        <Button variant="secondary" size="sm" onClick={() => armed && onDiscard()} disabled={disabled}>
          Discard
        </Button>
      </div>
    );
  }
  const shown = recipientsFor(form, replyAll);
  const widens = replyAllAddsSomeone(form);
  const picked = tickedFor(form, replyAll, ticks);
  const ticked = new Set(replyAll ? ticks.reply_all : ticks.reply);
  const onTick = (a: string) => setTicks((t) => toggleTick(t, replyAll, a));
  // A reply needs one ticked To at least.
  const ok = bodyCanGo(body) && picked.to.length > 0;
  const lines = lineCount(body);
  const answer = (action: ReplyAction) => {
    if (!armed) return;
    const a = replyAnswer(form, { action, replyAll, body, ticks });
    if (a) onAnswer(a);
  };
  const when = receivedText(form.original.received);
  const bccList = shown.bcc ?? [];
  const submitBcc = async () => {
    const address = bccDraft.trim();
    if (!address || bccBusy || !armed) return;
    setBccBusy(true);
    setBccError("");
    const res = await addBcc(form, address);
    setBccBusy(false);
    if (!res.ok) {
      setBccError(res.error);
      return;
    }
    setForm((f) => (f ? withBcc(f, res.address, res.mark, res.cls) : f));
    // The member typed it, so it starts ticked.
    setTicks((t) => (t.reply_all.includes(res.address.toLowerCase()) ? t : toggleTick(t, replyAll, res.address)));
    setBccDraft("");
  };
  return (
    <div className="space-y-2.5" data-reply-card="">
      <div className="min-w-0">
        <p className="break-words text-xs font-medium leading-relaxed text-foreground" data-reply-summary="">
          {replySummary(form, replyAll, ticks)}
        </p>
        {detail && <p className="mt-0.5 break-words text-[11px] text-muted-foreground">{detail}</p>}
      </div>

      {form.notices.length > 0 && (
        <ul className="space-y-1 rounded-md border border-border px-2.5 py-1.5" aria-label="Before you send">
          {form.notices.map((n, i) => (
            <li key={i} className="break-words text-xs text-foreground" data-reply-notice="">
              {n}
            </li>
          ))}
        </ul>
      )}

      <figure className="space-y-0.5 rounded-md border border-border bg-muted/40 px-2.5 py-2" aria-label="The email">
        <p className="flex min-w-0 flex-wrap gap-x-2 text-[11px] text-muted-foreground">
          <span className="min-w-0 break-words font-medium text-foreground">{form.original.from}</span>
          {when && <span>{when}</span>}
        </p>
        {form.original.subject && (
          <p className="break-words text-xs font-medium text-foreground">{form.original.subject}</p>
        )}
        {form.original.excerpt && (
          <p className="line-clamp-3 break-words text-xs text-muted-foreground" data-reply-excerpt="">
            {form.original.excerpt}
          </p>
        )}
      </figure>

      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Who gets the reply">
        <Button
          variant="secondary"
          size="sm"
          icon="Reply"
          selected={mode === "reply"}
          onClick={() => {
            setMode("reply");
            setTicks((t) => onlySenderTicks(form, t));
          }}
          disabled={disabled}
          data-reply-mode="reply"
        >
          Reply
        </Button>
        <Button
          variant="secondary"
          size="sm"
          icon="ReplyAll"
          selected={mode === "reply-all"}
          onClick={() => {
            setMode("reply-all");
            setTicks((t) => replyAllTicks(form, t));
          }}
          disabled={disabled || !widens}
          data-reply-mode="reply-all"
        >
          Reply all
        </Button>
        {!widens && <span className="text-[11px] text-muted-foreground">No one else is on this email.</span>}
      </div>

      <dl className="space-y-1" aria-label="Recipients">
        <AddressRow label="To" list={shown.to} form={form} ticked={ticked} onTick={onTick} disabled={disabled} />
        {shown.cc.length > 0 && (
          <AddressRow label="Cc" list={shown.cc} form={form} ticked={ticked} onTick={onTick} disabled={disabled} />
        )}
        {bccList.length > 0 && (
          <AddressRow label="Bcc" list={bccList} form={form} ticked={ticked} onTick={onTick} disabled={disabled} />
        )}
      </dl>
      {bccList.length > 0 && (
        <p className="text-[11px] text-muted-foreground" data-reply-bcc-note="">
          {BCC_LABEL}: the other people do not see who is on Bcc.
        </p>
      )}
      {form.requestId && (
        <div className="space-y-1" data-reply-bcc-add="">
          <div className="flex items-center gap-1.5">
            <Input
              inputSize="sm"
              type="email"
              aria-label="Add a Bcc address"
              placeholder="Add a Bcc, for example name@company.com"
              value={bccDraft}
              onChange={(e) => setBccDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void submitBcc();
                }
              }}
              disabled={disabled || bccBusy}
              className="min-w-0 flex-1"
            />
            <Button
              variant="secondary"
              size="sm"
              icon="Plus"
              onClick={() => void submitBcc()}
              disabled={disabled || bccBusy || !bccDraft.trim()}
              data-reply-bcc-submit=""
            >
              {bccBusy ? "Adding…" : "Add Bcc"}
            </Button>
          </div>
          {bccError && (
            <p className="break-words text-[11px] text-destructive" role="alert" data-reply-bcc-error="">
              {bccError}
            </p>
          )}
        </div>
      )}
      <p className="text-[11px] text-muted-foreground" data-reply-count="">
        {sendsCount(form, replyAll, ticks)}. Only a ticked person gets the reply.
      </p>
      {leftOutText(form, ticks) && (
        <p className="break-words text-[11px] text-foreground" data-reply-left-out="">
          {leftOutText(form, ticks)}
        </p>
      )}

      {form.note && <p className="text-[11px] text-muted-foreground">{form.note}</p>}
      <Textarea
        ref={box}
        aria-label="Reply text"
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
          Save to Drafts
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            if (!armed) return;
            // The member's own Discard also trashes the card's draft.
            void discardReplyDraft(form);
            onDiscard();
          }}
          disabled={disabled}
          aria-disabled={!armed || undefined}
          data-reply-discard=""
        >
          Discard
        </Button>
      </div>
      <p className="text-[10px] text-muted-foreground">{replyHint(form)}</p>
    </div>
  );
}
