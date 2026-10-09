"use client";

import React from "react";
import Button from "@/components/ui/Button";
import { RUN_ERROR_WORDS, type RunErrorView } from "@/lib/runErrors";

/**
 * The error card a failed turn leaves in the thread.
 *
 * Owner report, 2026-10-08: the card printed a Python repr whose class name
 * ran past the right edge, and an operator command for a customer. Now:
 *
 * - The words come from the server's code (`lib/runErrors.ts`).
 * - The raw text sits inside "Show full error", wraps, and scrolls in its
 *   own block. Every text node carries `wrap-anywhere` and `min-w-0`, so a
 *   long token can never push the card wider than its column.
 * - No member sees an operator instruction, a server name or a unit name,
 *   admin or not. The fold shows the ref, and staff grep the logs for it.
 * - Retry re-sends the member's last message through the chat's one retry
 *   path (`AgentChat`'s `handleRetryMessage`).
 * - A `notice` code (an app update, a run an update ended) draws as a status
 *   in the warning tokens, with `role="status"`, and with no fold: nothing
 *   failed for good, and there is no raw text to show (incident 2026-10-09).
 *   Its button carries the code's own verb, such as "Continue".
 *
 * Fences: `runErrors.test.ts`, which renders this card.
 */
export function ErrorCardView({
  error,
  onRetry,
  defaultOpen = false,
}: {
  error: RunErrorView;
  onRetry?: () => void;
  /** Tests render the fold open. A member opens it by hand. */
  defaultOpen?: boolean;
}) {
  const [copied, setCopied] = React.useState(false);
  const [expanded, setExpanded] = React.useState(defaultOpen);
  const words = RUN_ERROR_WORDS[error.code];
  const notice = words.tone === "notice";

  const handleCopy = () => {
    const text = error.ref ? `${error.raw}\n\nReference: ${error.ref}` : error.raw;
    navigator.clipboard?.writeText(text).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div
      data-chat-error-card
      data-tone={notice ? "notice" : "error"}
      role={notice ? "status" : "alert"}
      className={[
        "w-full min-w-0 max-w-full overflow-hidden rounded-xl border px-4 py-3 text-sm",
        notice ? "border-warning/40 bg-warning/10" : "border-destructive/40 bg-destructive/10",
      ].join(" ")}
    >
      <p className={`min-w-0 font-semibold wrap-anywhere ${notice ? "text-foreground" : "text-destructive"}`}>
        {words.title}
      </p>
      <p className="mt-1 min-w-0 text-xs leading-relaxed text-foreground wrap-anywhere">{words.body}</p>
      <div className="mt-2 flex min-w-0 flex-wrap items-center gap-2">
        {onRetry && words.retry && (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            icon={words.action ? "CornerDownRight" : "RefreshCw"}
            onClick={onRetry}
          >
            {words.action ?? "Retry"}
          </Button>
        )}
        {!notice && (
          <>
            <Button type="button" size="sm" variant="text" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}>
              {expanded ? "Hide full error" : "Show full error"}
            </Button>
            <Button type="button" size="sm" variant="text" onClick={handleCopy} title="Copy the full error">
              {copied ? "Copied" : "Copy error"}
            </Button>
          </>
        )}
      </div>
      {!notice && expanded && (
        <div className="mt-2 min-w-0 space-y-2" data-chat-error-fold>
          <pre className="max-h-40 min-w-0 max-w-full overflow-auto whitespace-pre-wrap rounded-lg bg-muted/60 p-2 font-mono text-[10px] text-muted-foreground wrap-anywhere">
            {error.raw || "(no error details)"}
          </pre>
          {error.ref && (
            <p className="min-w-0 text-[11px] text-muted-foreground wrap-anywhere">
              Reference: <span className="font-mono">{error.ref}</span>
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** The card as the thread mounts it. */
export default function ErrorCard({ error, onRetry }: { error: RunErrorView; onRetry?: () => void }) {
  return <ErrorCardView error={error} onRetry={onRetry} />;
}
