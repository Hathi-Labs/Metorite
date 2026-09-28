"use client";

/**
 * ConfirmationCard — Human-in-the-Loop confirmation prompt.
 *
 * When the agent needs user approval before taking an action, it emits a
 * "confirmation_requested" custom event. This card renders inline in the
 * chat thread with Approve / Reject buttons.
 *
 * Usage:
 *   <ConfirmationCard
 *     title="Confirm email send"
 *     detail="Send weekly sales report to team@fracktal.in?"
 *     onApprove={() => sendMessage("APPROVE: send_email")}
 *     onReject={() => sendMessage("REJECT: send_email")}
 *   />
 */

interface ConfirmationCardProps {
  title: string;
  detail?: string;
  /** Additional context to show (e.g. what will happen). */
  context?: string;
  onApprove: () => void;
  onReject: () => void;
  /** Disable buttons after a choice is made. */
  disabled?: boolean;
}

import Button from "@/components/ui/Button";

export default function ConfirmationCard({
  title,
  detail,
  context,
  onApprove,
  onReject,
  disabled = false,
}: ConfirmationCardProps) {
  return (
    <div className="my-3 rounded-xl border border-warning/30 bg-warning/10 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-warning/30 bg-warning/10">
        <span className="text-base">⚠️</span>
        <span className="text-[12px] sm:text-[13px] font-medium text-warning">
          {title}
        </span>
      </div>

      {/* Body */}
      {(detail || context) && (
        <div className="px-4 py-3 space-y-2">
          {detail && (
            <p className="text-[12px] sm:text-[13px] text-foreground leading-relaxed">
              {detail}
            </p>
          )}
          {context && (
            <pre className="text-[11px] text-muted-foreground bg-card/60 rounded-lg p-2.5 overflow-x-auto whitespace-pre-wrap max-h-32 overflow-y-auto">
              {context}
            </pre>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-t border-warning/30 bg-warning/5">
        <button
          onClick={onApprove}
          disabled={disabled}
          className="text-[12px] px-4 py-1.5 rounded-lg bg-success text-success-foreground font-medium hover:bg-emerald-500 disabled:opacity-40 transition-colors"
        >
          ✓ Approve
        </button>
        <Button
          variant="secondary"
          size="sm"
          icon="X"
          onClick={onReject}
          disabled={disabled}
        >
          Reject
        </Button>
        <span className="text-[10px] text-muted-foreground ml-auto">
          Agent is waiting for your decision
        </span>
      </div>
    </div>
  );
}
