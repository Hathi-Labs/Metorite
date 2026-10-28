"use client";

/**
 * ConfirmationReceipts — one line for each answered approval card (UXD-3).
 *
 * An approval card left the thread with no word, so the member could not
 * tell an Approve that worked from one that did not. Each answer now leaves
 * one line under the turn: "Applying…", then "Approved" or "Rejected", or
 * "Not applied" with the reason of a refusal. The words are `receiptText` in
 * `lib/confirmationQueue.ts`. Fence: `src/lib/confirmationReceipts.test.ts`.
 */

import Icon from "@/components/Icon";
import { receiptText, type ConfirmationReceipt } from "@/lib/confirmationQueue";

const LOOK: Record<ConfirmationReceipt["kind"], { icon: string; tone: string }> = {
  applying: { icon: "Loader2", tone: "text-muted-foreground" },
  approved: { icon: "Check", tone: "text-success" },
  rejected: { icon: "X", tone: "text-muted-foreground" },
  refused: { icon: "AlertCircle", tone: "text-destructive" },
};

export default function ConfirmationReceipts({ receipts }: { receipts: readonly ConfirmationReceipt[] }) {
  if (receipts.length === 0) return null;
  return (
    <ul className="mt-1 space-y-0.5" aria-label="Answered approvals">
      {receipts.map((r) => {
        const look = LOOK[r.kind];
        return (
          <li
            key={r.key}
            data-confirmation-receipt={r.kind}
            role={r.kind === "refused" ? "alert" : "status"}
            className="flex items-start gap-1.5 text-xs text-muted-foreground"
          >
            <Icon
              name={look.icon}
              size={12}
              className={`mt-0.5 shrink-0 ${look.tone}${r.kind === "applying" ? " motion-safe:animate-spin" : ""}`}
            />
            <span className="min-w-0 break-words">{receiptText(r)}</span>
          </li>
        );
      })}
    </ul>
  );
}
