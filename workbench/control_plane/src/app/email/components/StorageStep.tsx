"use client";

/**
 * The storage step of the guided setup (WS-17 EM-T6e, scope item 4, D6).
 *
 * Spec: `project-docs/specs/email_app_master_plan.md` §10.4.7, "EM-T6e".
 *
 * The page draws it when `onboardingStage(account, { storageKept })` is
 * `storage`: the first import stopped at the limit, and the meter is still
 * at it. It sits between the import panels and the rules step, where the
 * rules step draws, and it is not a modal.
 *
 * Two actions:
 * - "Remove older mail from Metorite" opens the dialog for THIS mailbox.
 * - "Keep it as it is" stores the id of the mailbox (`keepStorage` in
 *   `lib/storage.ts`). The stage then moves to `rules`, and the storage
 *   notice names the mailbox from then on (D3).
 *
 * While the step shows, the page draws no storage notice for its mailbox,
 * because the step names it already (`storageMailbox`, D3).
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { mailboxLabel } from "../lib/mailbox";
import { STORAGE_COPY, storageNotice } from "../lib/storage";
import { MailboxChip } from "./MailboxChip";
import type { StorageNoticeAccount } from "./StorageNotice";

export function StorageStep({
  account,
  named,
  onRemove,
  onKeep,
  now = new Date(),
  locale,
}: {
  account: StorageNoticeAccount;
  /** True with two or more mailboxes: the chip, the address and the label. */
  named: boolean;
  onRemove: () => void;
  onKeep: () => void;
  now?: Date;
  locale?: string;
}) {
  const view = storageNotice(account, { name: named ? mailboxLabel(account) : null, now, locale });
  if (!view || view.kind !== "limit") return null;
  return (
    <section
      aria-label="Mailbox setup, storage"
      className="flex items-start gap-3 border-b border-border bg-card px-3 py-2.5 flex-shrink-0"
    >
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-warning/10 text-warning">
        <Icon name="HardDrive" size={14} aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs font-medium text-foreground">
          <span>{STORAGE_COPY.stepTitle}</span>
          {named ? (
            <>
              <span className="font-normal text-muted-foreground">for</span>
              <MailboxChip account={account} />
              <span className="truncate text-[11px] font-normal text-muted-foreground">{account.emailAddress}</span>
            </>
          ) : null}
        </p>
        <p className="mt-0.5 text-[11px] text-muted-foreground">{view.text}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button variant="primary" size="sm" onClick={onRemove}>
            {view.action}
          </Button>
          <Button variant="secondary" size="sm" onClick={onKeep}>
            {STORAGE_COPY.keep}
          </Button>
        </div>
      </div>
    </section>
  );
}
