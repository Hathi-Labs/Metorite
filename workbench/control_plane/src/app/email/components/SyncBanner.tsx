"use client";

/**
 * The sync banner under the header of the email page (WS-17 EM-S9,
 * `email_app_master_plan.md` §14.4.6 and §14.6.9, D-EM-58).
 *
 * One row for each mailbox whose import writes progress, in view or not. Each
 * row names its mailbox with `MailboxChip`, says that the sync still runs, and
 * shows a percent (`ProgressBar`) or a count. Before the first phase it reads
 * "Starting sync", with no bar and no count. A row goes away when its phase
 * ends. It replaces `FirstSyncBanner`.
 *
 * `syncBanners` in `lib/onboarding.ts` decides each row, and this file only
 * draws them. The page's first-sync poll re-reads `GET /email/accounts`, so
 * the banner has no request of its own.
 *
 * ⚠️ Only the phase line of each row is a live region (`role="status"`).
 * The count changes at each poll, and a live region over it would speak
 * every five seconds. `OnboardingPanel` keeps the same rule. The line holds
 * the address of its mailbox in an `sr-only` span, so the announcement names
 * the mailbox.
 *
 * ⚠️ `bg-card`, not a `bg-primary/5` tint. In light mode the empty track of
 * the bar (`bg-muted`) vanishes on that tint (visual review, 2026-10-02).
 */

import Icon from "@/components/Icon";
import ProgressBar from "@/components/ui/ProgressBar";
import { SYNC_BANNER_LABEL, SYNC_BANNER_REGION, type SyncBannerRow } from "../lib/onboarding";
import type { EmailAccount } from "../lib/types";
import { MailboxChip } from "./MailboxChip";

type Mailbox = Pick<EmailAccount, "id" | "emailAddress" | "colorSlot" | "displayLabel">;

export function SyncBanner<A extends Mailbox>({ rows }: { rows: ReadonlyArray<SyncBannerRow<A>> }) {
  if (rows.length === 0) return null;
  return (
    <section
      aria-label={SYNC_BANNER_REGION}
      className="flex flex-col gap-1.5 border-b border-border bg-card px-3 py-2 flex-shrink-0"
    >
      {rows.map(({ account, line, percent, detail }) => (
        <div
          key={account.id}
          data-sync-row={account.id}
          className="flex flex-wrap items-center gap-x-3 gap-y-1"
        >
          <div className="flex min-w-0 flex-1 items-center gap-1.5">
            <Icon name="RefreshCw" size={12} className="flex-shrink-0 animate-spin text-primary" aria-hidden />
            <MailboxChip account={account} />
            {/* The address is in the live text, hidden from the eye, so a
                screen reader hears "<address>: Importing mail". The chip
                beside it shows the mailbox to the eye. */}
            <p role="status" className="truncate text-xs font-medium text-foreground">
              <span className="sr-only">{account.emailAddress}: </span>
              {line}
            </p>
          </div>
          {percent === null ? (
            // "Starting sync" has no count yet, so it draws no detail.
            detail !== "" && <p className="text-[11px] text-muted-foreground">{detail}</p>
          ) : (
            <div className="w-full sm:w-64">
              <ProgressBar percent={percent} label={`${SYNC_BANNER_LABEL}, ${account.emailAddress}`} detail={detail} />
            </div>
          )}
        </div>
      ))}
    </section>
  );
}
