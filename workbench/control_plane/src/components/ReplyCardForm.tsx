"use client";

/**
 * ReplyCardForm — the body of a reply card (EM-CR, `email_app_master_plan.md` §18).
 *
 * It draws inside `ConfirmationCard`, under its shared header, so a reply
 * card is the ONE confirmation card with a form, not a second card. It shows
 * the mail it answers, the recipients the gateway derived, a Reply or Reply
 * all toggle, the editable body, and Send, Save to Drafts and Discard. The
 * logic is `lib/replyCard.ts`. This file holds the member's edits only.
 *
 * The client derives no recipient. The toggle switches between the server's
 * two lists, and each list shows in full.
 */

import { useState } from "react";

import Button from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Input";
import {
  bodyCanGo,
  MAX_REPLY_BODY,
  receivedText,
  recipientsFor,
  replyAllAddsSomeone,
  replyAnswer,
  replyHint,
  type ReplyAction,
  type ReplyApproval,
  type ReplyForm,
} from "@/lib/replyCard";

interface ReplyCardFormProps {
  /** The form, or null when the card could not read it. */
  form: ReplyForm | null;
  onAnswer: (answer: ReplyApproval) => void;
  onDiscard: () => void;
  /** False for the first moments of a new card (`ARM_MS`): clicks do nothing. */
  armed: boolean;
  disabled?: boolean;
}

function AddressRow({ label, list }: { label: string; list: string[] }) {
  return (
    <div className="flex gap-3 text-xs" data-reply-recipients={label.toLowerCase()}>
      <dt className="w-10 shrink-0 pt-px text-muted-foreground">{label}</dt>
      <dd className="min-w-0 flex-1 break-words text-foreground">{list.join(", ")}</dd>
    </div>
  );
}

export default function ReplyCardForm({ form, onAnswer, onDiscard, armed, disabled = false }: ReplyCardFormProps) {
  const [replyAll, setReplyAll] = useState(form?.replyAll ?? false);
  const [body, setBody] = useState(form?.body ?? "");
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
  const ok = bodyCanGo(body);
  const answer = (action: ReplyAction) => {
    if (!armed) return;
    const a = replyAnswer(form, { action, replyAll, body });
    if (a) onAnswer(a);
  };
  const when = receivedText(form.original.received);
  return (
    <div className="space-y-2.5" data-reply-card="">
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
          selected={!replyAll}
          onClick={() => setReplyAll(false)}
          disabled={disabled}
          data-reply-mode="reply"
        >
          Reply
        </Button>
        <Button
          variant="secondary"
          size="sm"
          icon="ReplyAll"
          selected={replyAll}
          onClick={() => setReplyAll(true)}
          disabled={disabled || (!widens && !replyAll)}
          data-reply-mode="reply-all"
        >
          Reply all
        </Button>
        {!widens && <span className="text-[11px] text-muted-foreground">No one else is on this email.</span>}
      </div>

      <dl className="space-y-1" aria-label="Recipients">
        <AddressRow label="To" list={shown.to} />
        {shown.cc.length > 0 && <AddressRow label="Cc" list={shown.cc} />}
      </dl>

      {form.note && <p className="text-[11px] text-muted-foreground">{form.note}</p>}
      <Textarea
        aria-label="Reply text"
        rows={8}
        value={body}
        maxLength={MAX_REPLY_BODY}
        onChange={(e) => setBody(e.target.value)}
        disabled={disabled}
        className="w-full text-xs"
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
          Save to Drafts
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
      <p className="text-[10px] text-muted-foreground">{replyHint(form)}</p>
    </div>
  );
}
