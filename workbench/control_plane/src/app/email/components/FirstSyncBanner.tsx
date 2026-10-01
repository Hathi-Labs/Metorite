"use client";

/**
 * "Connected as <address>" while the first sync of a new mailbox runs
 * (EM-T3b). The page polls the accounts while any account is pending and
 * hides this banner when the gateway reports `initial_sync_done`.
 *
 * `role="status"` with `aria-live="polite"`, so a screen reader hears the
 * change once and is not interrupted.
 */

import Icon from "@/components/Icon";
import { firstSyncCopy } from "../lib/connect";

export function FirstSyncBanner({ address }: { address: string }) {
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
        <p className="truncate text-xs font-medium text-foreground">{copy.title}</p>
        <p className="mt-0.5 text-[11px] text-muted-foreground">{copy.body}</p>
      </div>
    </div>
  );
}
