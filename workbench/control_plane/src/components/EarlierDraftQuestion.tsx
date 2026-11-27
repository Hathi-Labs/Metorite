"use client";

/**
 * EarlierDraftQuestion — "Keep the earlier draft, or delete it?" on the card
 * itself (task 3978, `email_app_master_plan.md` §18.13.1).
 *
 * ONE question for two cards. The new-mail form asks it at Discard, and the
 * plain card of `edit_draft` ("Save these changes to the draft?") asks it at
 * Reject. Each asks only when the server named a draft that the chat saved,
 * and the card can carry the answer (`declineStep`). The question takes the
 * place of the card's buttons and of the card's hint line, so no line still
 * says what the old button did.
 *
 * - Keep is the plain decline of before (`REJECT`). The draft stays in Drafts.
 * - Delete it sends `DELETE_EARLIER_ANSWER`, a decline too. The tool then
 *   trashes that one draft, only for the member's own answer.
 * - Back shows the buttons again, and sends nothing.
 *
 * The hint names what Keep does, then what Delete does at the mailbox's
 * provider, from the server (`EARLIER_EFFECTS`). With no known provider it
 * promises nothing about the delete. Keep takes the focus when the question
 * opens, because the button that the member pressed is gone. Back (or
 * Escape) shows the buttons again, and the card gives the focus back to its
 * Discard or Reject button (`autoFocus` after a Back).
 *
 * Fence: `src/components/newMailCardEarlier.test.ts`.
 */

import Button from "@/components/ui/Button";
import { EARLIER_QUESTION, earlierHint } from "@/lib/replyCard";
import { FRESH_KEY_PROPS } from "@/lib/waitingDock";

/**
 * What a Discard or a Reject does: `ask` opens the question, `decline` is the
 * decline of before, and `none` is a click before the card is armed.
 */
export function declineStep(armed: boolean, line: string | undefined, onDiscardEarlier?: () => void): "ask" | "decline" | "none" {
  if (!armed) return "none";
  return line && onDiscardEarlier ? "ask" : "decline";
}

export default function EarlierDraftQuestion({
  line,
  effect,
  onKeep,
  onDelete,
  onBack,
  disabled = false,
  freshKeys = false,
}: {
  line: string;
  effect?: string;
  onKeep: () => void;
  onDelete: () => void;
  onBack: () => void;
  disabled?: boolean;
  /** The waiting dock (24.11): a held key (`repeat`) answers nothing. */
  freshKeys?: boolean;
}) {
  const fresh = freshKeys ? FRESH_KEY_PROPS : {};
  return (
    <div
      role="group"
      aria-label={EARLIER_QUESTION}
      className="space-y-1.5 rounded-md border border-border px-2.5 py-2 glass:rounded-[10px] glass:border-transparent glass:bg-secondary glass:px-3"
      data-earlier-question=""
      // Escape is Back: the member leaves the question with no answer.
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onBack();
        }
      }}
    >
      <p className="text-xs font-medium text-foreground">{EARLIER_QUESTION}</p>
      <p className="break-words text-[11px] text-muted-foreground" data-earlier-draft="">
        {line}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {/* The pressed button is gone, so the focus moves here (task 3978). */}
        <Button variant="secondary" size="sm" onClick={onKeep} disabled={disabled} autoFocus data-earlier-keep="" {...fresh}>
          Keep it
        </Button>
        <Button variant="destructive" size="sm" icon="Trash2" onClick={onDelete} disabled={disabled} data-earlier-delete="" {...fresh}>
          Delete it
        </Button>
        <Button variant="ghost" size="sm" onClick={onBack} disabled={disabled} data-earlier-back="">
          Back
        </Button>
      </div>
      <p className="text-[10px] text-muted-foreground" data-earlier-hint="">
        {earlierHint(effect)}
      </p>
    </div>
  );
}
