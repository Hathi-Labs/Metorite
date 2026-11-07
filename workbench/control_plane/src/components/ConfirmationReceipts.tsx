"use client";

/**
 * ConfirmationReceipts — one line for each answered approval card (UXD-3).
 *
 * An approval card left the thread with no word, so the member could not
 * tell an Approve that worked from one that did not. Each answer now leaves
 * one line under the turn: "Applying…", then "Approved" or "Rejected", or
 * "Not applied" with the reason of a refusal. The words are `receiptText` in
 * `lib/confirmationQueue.ts`. Fence: `src/lib/confirmationReceipts.test.ts`.
 *
 * WT-6a: the line of an approved archive card offers Undo too, while the
 * server's `confirmation_undo` named its row (`lib/archiveUndo.ts`). It uses
 * the same button as the Undo of a held Reject.
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { archiveUndoOffered } from "@/lib/archiveUndo";
import { receiptText, type ConfirmationReceipt } from "@/lib/confirmationQueue";

const LOOK: Record<ConfirmationReceipt["kind"], { icon: string; tone: string }> = {
  applying: { icon: "Loader2", tone: "text-muted-foreground" },
  approved: { icon: "Check", tone: "text-success" },
  rejected: { icon: "X", tone: "text-muted-foreground" },
  refused: { icon: "AlertCircle", tone: "text-destructive" },
  closed: { icon: "Info", tone: "text-muted-foreground" },
};

export default function ConfirmationReceipts({
  receipts,
  onUndo,
  onUndoArchive,
}: {
  receipts: readonly ConfirmationReceipt[];
  /** Cancels a held Reject (`lib/rejectHold.ts`). Without it no Undo draws. */
  onUndo?: (key: string) => void;
  /** Restores an approved archive (`lib/archiveUndo.ts`). Without it no Undo draws. */
  onUndoArchive?: (key: string) => void;
}) {
  if (receipts.length === 0) return null;
  return (
    <ul className="mt-1 space-y-0.5" aria-label="Answered approvals">
      {receipts.map((r) => {
        const look = LOOK[r.kind];
        const undo = r.undo && onUndo
          ? () => onUndo(r.key)
          : archiveUndoOffered(r.archive?.state) && onUndoArchive
            ? () => onUndoArchive(r.key)
            : null;
        return (
          <li
            key={r.key}
            data-confirmation-receipt={r.kind}
            role={r.kind === "refused" ? "alert" : "status"}
            className="flex items-start gap-1.5 text-xs text-muted-foreground glass:text-footnote"
          >
            <Icon
              name={look.icon}
              size={12}
              className={`mt-0.5 shrink-0 ${look.tone}${r.kind === "applying" ? " motion-safe:animate-spin" : ""}`}
            />
            <span className="min-w-0 break-words">{receiptText(r)}</span>
            {undo && (
              <>
                <span aria-hidden>—</span>
                <Button variant="text" size="none" data-confirmation-undo={r.key} onClick={undo}>
                  Undo
                </Button>
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
}
