"use client";

/**
 * "Connected as <address>" while the first sync of a new mailbox runs
 * (EM-T3b). The page polls the accounts while any account is pending and
 * hides this banner when the gateway reports `initial_sync_done`.
 *
 * `role="status"` with `aria-live="polite"`, so a screen reader hears the
 * change once and is not interrupted.
 *
 * The page draws one banner for each pending mailbox. With two or more
 * mailboxes each banner draws the chip of its mailbox (EM-T8f-3).
 */

import Icon from "@/components/Icon";
import { firstSyncCopy } from "../lib/connect";
import type { EmailAccount } from "../lib/types";
import { MailboxChip } from "./MailboxChip";

export function FirstSyncBanner({
  address,
  mailbox,
}: {
  address: string;
  /** The mailbox that syncs, for its chip. Absent for one mailbox. */
  mailbox?: Pick<EmailAccount, "id" | "emailAddress" | "colorSlot" | "displayLabel">;
}) {
  const copy = firstSyncCopy(address);
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-start gap-3 border-b border-primary/20 bg-primary/5 px-3 py-2.5 flex-shrink-0"
    >
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Icon name="Loader2" size={14} className="animate-spin" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-1.5">
          {mailbox && <MailboxChip account={mailbox} />}
          <p className="truncate text-xs font-medium text-foreground">{copy.title}</p>
        </div>
        <p className="mt-0.5 text-[11px] text-muted-foreground">{copy.body}</p>
      </div>
    </div>
  );
}
