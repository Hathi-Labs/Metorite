"use client";

/**
 * The guided setup of a new mailbox, at the top of the mail pane (WS-17
 * EM-T6d items 4 to 7 and 12).
 *
 * It draws where `FirstSyncBanner` drew, and it is not a modal: the member
 * can read the mail that arrives while the import runs. The page draws it
 * when `onboardingStage(account)` is `importing`, and keeps
 * `FirstSyncBanner` for every other pending mailbox.
 *
 * The panel draws `ImportProgressView` from `lib/onboarding.ts` and decides
 * nothing. The page's first-sync poll re-reads `GET /email/accounts` every
 * `FIRST_SYNC_POLL_MS`, and that read carries each field the view needs.
 * There is no endpoint and no stream of its own.
 *
 * ⚠️ The bar always draws, in each phase, so the panel never shows a spinner
 * alone (item 6). Only the phase line is a live region. The detail changes
 * at each poll, and a live region over it would speak every five seconds.
 *
 * Part 2 of EM-T6d adds the rules step here, beside `importing`.
 */

import Icon from "@/components/Icon";
import ProgressBar from "@/components/ui/ProgressBar";
import { firstSyncCopy } from "../lib/connect";
import { IMPORT_PROGRESS_LABEL, type ImportProgressView } from "../lib/onboarding";

export function OnboardingPanel({
  address,
  progress,
}: {
  address: string;
  progress: ImportProgressView;
}) {
  const title = firstSyncCopy(address).title;
  return (
    // ⚠️ `bg-card`, not the `bg-primary/5` tint of FirstSyncBanner. In light
    // mode `--muted` and that tint over white are both about 96% light, so the
    // empty track of the bar vanished (visual review, 2026-10-02). `bg-card`
    // is the surface of the first ProgressBar caller, the Projects import.
    <section
      aria-label="Mailbox import"
      className="flex items-start gap-3 border-b border-border bg-card px-3 py-2.5 flex-shrink-0"
    >
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Icon name="Inbox" size={14} aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium text-foreground">{title}</p>
        <p role="status" aria-live="polite" className="mt-0.5 text-[11px] text-muted-foreground">
          {progress.phaseLine}
        </p>
        <div className="mt-1.5 max-w-md">
          <ProgressBar percent={progress.percent} label={IMPORT_PROGRESS_LABEL} detail={progress.detail} />
        </div>
      </div>
    </section>
  );
}
