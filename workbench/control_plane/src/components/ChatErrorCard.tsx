"use client";

import React from "react";
import Icon from "@/components/Icon";
import RollupCard, { RollupBody, RollupHeader } from "@/components/RollupCard";
import Button from "@/components/ui/Button";
import { dismissToolCard, useDismissedToolCards } from "@/lib/dismissedTools";
import { RUN_ERROR_WORDS, isInfoOnlyNotice, noticeBody, type RunErrorView } from "@/lib/runErrors";
import { openActivity } from "@/lib/shell/ActivityControl";
import { accentForHue } from "@/lib/statusAccent";

/** The quiet tones of an info-only notice: muted words, an info icon. */
const MUTED = accentForHue("gray");
const INFO = accentForHue("blue");

/**
 * An info-only notice, as the transcript draws it (owner report,
 * 2026-10-17): "shouldn't this card be closable? At the moment, it is
 * persistent." It is one slim line, not a warning card, because nothing
 * failed and nothing remains to do. The title leads, the body follows, and
 * the row wraps on a narrow screen.
 *
 * Pure, with no hook, so a test can call it and press its Dismiss.
 */
export function InfoNoticeView({
  title,
  body,
  onDismiss,
}: {
  title: string;
  body: string;
  onDismiss: () => void;
}) {
  return (
    <div
      data-chat-notice-marker
      data-tone="info"
      role="status"
      className={`flex w-full min-w-0 max-w-full items-start gap-1.5 rounded-md px-2 py-1 text-[11px] ${MUTED.soft} ${MUTED.text}`}
    >
      <Icon name="Info" size={12} className={`mt-0.5 shrink-0 ${INFO.text}`} />
      <p className="min-w-0 flex-1 leading-relaxed wrap-anywhere">
        <span className="font-medium text-foreground">{title}</span>
        <span aria-hidden="true">{" · "}</span>
        <span>{body}</span>
      </p>
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        icon="X"
        aria-label="Dismiss"
        title="Dismiss"
        onClick={onDismiss}
        className="shrink-0"
      />
    </div>
  );
}

/**
 * The marker with its state. A dismissal goes into the chat's one set of
 * dismissed cards (`lib/dismissedTools.ts`), under `dismissKey`, so a
 * remount or a reload keeps it gone. The local flag hides it at once, also
 * when there is no key or the browser refuses storage.
 */
function InfoNotice({ error, dismissKey }: { error: RunErrorView; dismissKey?: string }) {
  const dismissed = useDismissedToolCards();
  const [hidden, setHidden] = React.useState(false);
  if (hidden || (dismissKey && dismissed.has(dismissKey))) return null;
  const words = RUN_ERROR_WORDS[error.code];
  return (
    <InfoNoticeView
      title={words.title}
      body={noticeBody(words, error.raw)}
      onDismiss={() => {
        setHidden(true);
        if (dismissKey) dismissToolCard(dismissKey);
      }}
    />
  );
}

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
 * - An info-only notice (`isInfoOnlyNotice`: no retry, no action) draws as
 *   {@link InfoNoticeView}, a slim line with a Dismiss control (owner
 *   report, 2026-10-17). A notice with an action keeps its card.
 *
 * - One look with the other chat cards (owner report, 2026-10-13): the
 *   shared frame size, and a title row that is `RollupHeader`, with an icon
 *   from the app set (`CircleAlert` for an error, `Info` for a notice). It
 *   draws through `RollupCard`, so a member may fold it like any card.
 *
 * Fences: `runErrors.test.ts`, which renders this card, and
 * `chatCardConformance.test.ts`.
 */
export function ErrorCardView({
  error,
  onRetry,
  defaultOpen = false,
  id,
  dismissKey,
}: {
  error: RunErrorView;
  onRetry?: () => void;
  /** Tests render the fold open. A member opens it by hand. */
  defaultOpen?: boolean;
  /** The roll-up's id: stable for the card's life (its message id). */
  id?: string;
  /** Where an info-only notice keeps its dismissal (`noticeDismissKey`).
   *  Without one, a dismissal lasts for the life of the line only. */
  dismissKey?: string;
}) {
  const [copied, setCopied] = React.useState(false);
  const [expanded, setExpanded] = React.useState(defaultOpen);
  const words = RUN_ERROR_WORDS[error.code];
  const notice = words.tone === "notice";
  if (isInfoOnlyNotice(words)) return <InfoNotice error={error} dismissKey={dismissKey} />;

  const handleCopy = () => {
    const text = error.ref ? `${error.raw}\n\nReference: ${error.ref}` : error.raw;
    navigator.clipboard?.writeText(text).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <RollupCard id={id ?? `error:${error.code}`} title={words.title} icon={notice ? "Info" : "CircleAlert"} header="own">
    <div
      data-chat-error-card
      data-tone={notice ? "notice" : "error"}
      role={notice ? "status" : "alert"}
      className={[
        "w-full min-w-0 max-w-full overflow-hidden rounded-lg border px-2.5 py-2",
        notice ? "border-warning/40 bg-warning/10" : "border-destructive/40 bg-destructive/10",
      ].join(" ")}
    >
      <RollupHeader
        className={`text-xs ${notice ? "text-foreground" : "text-destructive"}`}
        titleClassName={`font-semibold wrap-anywhere ${notice ? "text-foreground" : "text-destructive"}`}
      />
      <RollupBody>
      <p className="mt-1 min-w-0 text-xs leading-relaxed text-foreground wrap-anywhere">{noticeBody(words, error.raw)}</p>
      <div className="mt-2 flex min-w-0 flex-wrap items-center gap-2">
        {onRetry && words.retry && (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            icon={words.action ? "ArrowRight" : "RefreshCw"}
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
      </RollupBody>
    </div>
    </RollupCard>
  );
}

/**
 * The card as the thread mounts it.
 *
 * The run-cap notice (WS-51 D-3) is the one card whose button is not a
 * retry: it opens the activity panel, so the member can stop a run. It never
 * re-sends the turn.
 */
export default function ErrorCard({
  error,
  onRetry,
  id,
  dismissKey,
}: {
  error: RunErrorView;
  onRetry?: () => void;
  id?: string;
  dismissKey?: string;
}) {
  const action = error.code === "too_many_runs" ? openActivity : onRetry;
  return <ErrorCardView error={error} onRetry={action} id={id} dismissKey={dismissKey} />;
}
