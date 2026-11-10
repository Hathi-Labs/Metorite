"use client";

/**
 * The storage notice of one mailbox, below the reconnect banner (WS-17
 * EM-T6e, scope item 5, D2 to D5).
 *
 * Spec: `project-docs/specs/email_app_master_plan.md` §10.4.7, "EM-T6e".
 *
 * Two kinds, both decided by `storageNotice` in `lib/storage.ts`:
 * - `limit`: the meter is at the limit. The notice draws the `warning` tokens
 *   of the reconnect banner and offers "Remove older mail from Metorite".
 * - `gap`: the phase is `limit` under the limit (D2). One line, with no
 *   action, on the neutral card surface, because nothing is wrong now.
 *
 * The page decides WHICH mailbox through `storageMailbox` in `lib/mailbox.ts`
 * (D3). The notice decides nothing. With two or more mailboxes it draws the
 * chip and the address, and its words name the label. With one mailbox they
 * say "This mailbox".
 *
 * ⚠️ No close button (open point of EM-T6e). The notice goes when the meter
 * goes under the limit. The owner can reverse this.
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { mailboxLabel } from "../lib/mailbox";
import { storageNotice } from "../lib/storage";
import type { EmailAccount } from "../lib/types";
import { MailboxChip } from "./MailboxChip";

export type StorageNoticeAccount = Pick<
  EmailAccount,
  | "id"
  | "emailAddress"
  | "displayLabel"
  | "colorSlot"
  | "storedBytes"
  | "storageLimitBytes"
  | "importPhase"
  | "importReachedAt"
>;

export function StorageNotice({
  account,
  named,
  onRemove,
  now = new Date(),
  locale,
}: {
  account: StorageNoticeAccount;
  /** True with two or more mailboxes: the chip, the address and the label. */
  named: boolean;
  /** Opens the removal dialog for THIS mailbox (D3). */
  onRemove: () => void;
  now?: Date;
  locale?: string;
}) {
  const view = storageNotice(account, { name: named ? mailboxLabel(account) : null, now, locale });
  if (!view) return null;
  const limit = view.kind === "limit";
  return (
    <section
      aria-label={named ? `Mailbox storage, ${account.emailAddress}` : "Mailbox storage"}
      // `flex-wrap` with a basis on the text: on a phone the action drops below
      // the words instead of squeezing them into a narrow column (visual
      // review, mobile 390).
      className={`flex flex-wrap items-start gap-x-2 gap-y-1.5 px-3 py-2 border-b flex-shrink-0 ${
        limit ? "border-warning/30 bg-warning/10" : "border-border bg-card"
      }`}
    >
      <Icon
        name={limit ? "HardDrive" : "Info"}
        size={14}
        className={`mt-0.5 flex-shrink-0 ${limit ? "text-warning" : "text-muted-foreground"}`}
        aria-hidden
      />
      <div className="min-w-0 flex-1 basis-48">
        {named && (
          <div className="mb-0.5 flex min-w-0 items-center gap-1.5">
            <MailboxChip account={account} />
            <span className="truncate text-[11px] text-muted-foreground">{account.emailAddress}</span>
          </div>
        )}
        <p className={`text-xs ${limit ? "text-foreground" : "text-muted-foreground"}`}>{view.text}</p>
      </div>
      {view.kind === "limit" && (
        <Button variant="secondary" size="sm" className="ml-auto flex-shrink-0" onClick={onRemove}>
          {view.action}
        </Button>
      )}
    </section>
  );
}
