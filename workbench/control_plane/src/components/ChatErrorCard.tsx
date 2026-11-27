"use client";

import React from "react";
import AiRestingArt from "@/components/AiRestingArt";
import Icon from "@/components/Icon";
import RollupCard, { RollupBody, RollupHeader } from "@/components/RollupCard";
import Button from "@/components/ui/Button";
import { dismissToolCard, useDismissedToolCards } from "@/lib/dismissedTools";
import {
  RUN_ERROR_WORDS,
  isInfoOnlyNotice,
  noticeBody,
  type RunErrorView,
  type RunErrorWords,
} from "@/lib/runErrors";
import { openActivity } from "@/lib/shell/ActivityControl";
import { accentForHue } from "@/lib/statusAccent";

/** The quiet tones of an info-only notice: muted words, an info icon. */
const MUTED = accentForHue("gray");
const INFO = accentForHue("blue");

/** The frame of an error card and of a notice card, and the two tints. */
const CARD_FRAME =
  "w-full min-w-0 max-w-full overflow-hidden rounded-lg border px-2.5 py-2 glass:glass-card-tile glass:rounded-[16px] glass:p-3";
const NOTICE_TINT = "border-warning/40 bg-warning/10 glass:[--tile:var(--system-orange)]";
const ERROR_TINT = "border-destructive/40 bg-destructive/10 glass:[--tile:var(--system-red)]";

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
      className={`flex w-full min-w-0 max-w-full items-start gap-1.5 rounded-md px-2 py-1 text-[11px] glass:rounded-[10px] glass:px-3 glass:py-2 glass:text-footnote ${MUTED.soft} ${MUTED.text}`}
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
function InfoNotice({
  error,
  words,
  dismissKey,
}: {
  error: RunErrorView;
  words: RunErrorWords;
  dismissKey?: string;
}) {
  const dismissed = useDismissedToolCards();
  const [hidden, setHidden] = React.useState(false);
  if (hidden || (dismissKey && dismissed.has(dismissKey))) return null;
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
  words: override,
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
  /** Words that replace the code's own, such as the other-account notice
   *  with its Switch button (`otherAccountSwitchWords`). */
  words?: RunErrorWords;
}) {
  const [copied, setCopied] = React.useState(false);
  const [expanded, setExpanded] = React.useState(defaultOpen);
  const words = override ?? RUN_ERROR_WORDS[error.code];
  const notice = words.tone === "notice";
  if (isInfoOnlyNotice(words)) return <InfoNotice error={error} words={words} dismissKey={dismissKey} />;
  if (error.code === "ai_unavailable" && !override) return <AiRestingCard onRetry={onRetry} id={id} />;
  const label = words.action ?? "Retry";

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
      className={`${CARD_FRAME} ${notice ? NOTICE_TINT : ERROR_TINT}`}
    >
      <RollupHeader
        className={`text-xs ${notice ? "text-foreground" : "text-destructive"}`}
        titleClassName={`font-semibold wrap-anywhere ${notice ? "text-foreground" : "text-destructive"}`}
      />
      <RollupBody>
      <p className="mt-1 min-w-0 text-xs leading-relaxed text-foreground wrap-anywhere glass:text-label">{noticeBody(words, error.raw)}</p>
      <div className="mt-2 flex min-w-0 flex-wrap items-center gap-2">
        {onRetry && words.retry && (
          // The label may carry an address (the other-account Switch), so it
          // truncates inside the card on a phone, and its title holds it all.
          <Button
            type="button"
            size="sm"
            variant="secondary"
            icon={words.action ? "ArrowRight" : "RefreshCw"}
            onClick={onRetry}
            title={label}
            className="min-w-0 max-w-full"
          >
            <span className="min-w-0 truncate">{label}</span>
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
          <pre className="max-h-40 min-w-0 max-w-full overflow-auto whitespace-pre-wrap rounded-lg bg-muted/60 p-2 font-mono text-[10px] text-muted-foreground wrap-anywhere glass:rounded-[10px] glass:bg-secondary glass:p-3 glass:text-caption">
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
 * The AI could not answer (AO-3, incident 2026-11-17).
 *
 * On 2026-11-17 the AI vendor refused every call, and the member saw an
 * empty reply. Now the server names the failure `ai_unavailable`, and the
 * chat draws this card: the resting elephant (`AiRestingArt`), the title,
 * one line that says what happened and what to do, and "Try again".
 *
 * - It is a notice, in the notice tokens and with `role="status"`. Nothing
 *   of the member's is lost: their message is saved.
 * - It has no "Show full error" fold. The raw text names our own services,
 *   and a member can do nothing with it. The server logs it with the ref.
 * - "Try again" re-sends the member's last message through the chat's one
 *   retry path (`AgentChat`'s `handleRetryMessage`), ONCE. A second press
 *   before the card leaves does nothing (`DESIGN_SYSTEM.md` §11, M3), and
 *   the button then shows as disabled.
 *
 * Pure, with no hook, so a test can call it and press its button.
 * {@link AiRestingCard} holds the state. Fence: `components/aiRestingCard.test.ts`.
 */
export function AiRestingCardView({
  onRetry,
  sent = false,
  id,
}: {
  onRetry?: () => void;
  /** True after the first press: the button shows as disabled. */
  sent?: boolean;
  id?: string;
}) {
  const words = RUN_ERROR_WORDS.ai_unavailable;
  return (
    <RollupCard id={id ?? "error:ai_unavailable"} title={words.title} icon="Unplug" header="own">
    <div
      data-chat-error-card
      data-tone="notice"
      data-code="ai_unavailable"
      role="status"
      className={`${CARD_FRAME} ${NOTICE_TINT}`}
    >
      <RollupHeader
        className="text-xs text-foreground"
        titleClassName="font-semibold wrap-anywhere text-foreground"
      />
      <RollupBody>
      <div className="mt-1 flex min-w-0 items-center gap-3">
        <AiRestingArt className="h-18 w-28 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="min-w-0 text-xs leading-relaxed text-foreground wrap-anywhere glass:text-label">{words.body}</p>
          {onRetry && (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              icon="RefreshCw"
              onClick={onRetry}
              disabled={sent}
              className="mt-2"
            >
              {words.action}
            </Button>
          )}
        </div>
      </div>
      </RollupBody>
    </div>
    </RollupCard>
  );
}

/**
 * `fn`, made to run once. Each later call does nothing. The "Try again" of
 * the resting card presses through it, so a double click sends one message.
 */
export function retryOnce(fn: () => void): () => void {
  let done = false;
  return () => {
    if (done) return;
    done = true;
    fn();
  };
}

/** The resting card with its one state: whether "Try again" was pressed. */
function AiRestingCard({ onRetry, id }: { onRetry?: () => void; id?: string }) {
  const [sent, setSent] = React.useState(false);
  const press = React.useMemo(
    () =>
      onRetry
        ? retryOnce(() => {
            setSent(true);
            onRetry();
          })
        : undefined,
    [onRetry],
  );
  return <AiRestingCardView onRetry={press} sent={sent} id={id} />;
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
