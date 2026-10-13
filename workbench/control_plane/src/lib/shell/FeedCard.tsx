"use client";

/**
 * My Day as a live feed: the column and its cards (`my_day_feed.md` §4.2 and
 * §14.1, WS-57 MD-1b). Behind `NEXT_PUBLIC_MY_DAY_FEED`, default OFF
 * (`myDayFeedOn`).
 *
 * **The card** is `HomeCard`, widened: the source line ("My Tasks · Overdue"
 * and the time) above the headline, the headline as the door to the entity,
 * the why line after a light bulb, and the acts. MD-1b draws no body and no
 * menu. Those are MD-4 and MD-2.
 *
 * **The column** draws the groups in the order of `groups`, and the cards in
 * the server's order inside each group (`feedGroups`). It shows 20 cards,
 * and the next 20 when the member scrolls to the end or asks. A true miss is
 * `Skeleton`, and a revisit never blanks (`AGENTS.md` rule 9).
 *
 * **The acts call the owning app's code** (NS-3). A `tap` act runs as a needs
 * row through `runAct`, so a done is the My Tasks store's gesture, with its
 * subtask question and its Undo, and a read is the Projects bell's client.
 * The act also marks the needs row in `needsActs`, so the bell and the
 * sidebar drop it at once. An `open` act is a link.
 *
 * **The one poll.** `useFeed` joins it with the feed's key. It covers the
 * needs key (`joinNeedsPoll`) only while its last read held a needs view
 * (`coversNeeds`), because only then did `fetchFeed` write that key.
 *
 * Fences: `feed.test.ts` and `myDayPage.test.ts`.
 */
import Link from "next/link";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import type { DoneRow } from "@/app/tasks/lib/completeFromHome";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { accentForHue } from "@/lib/statusAccent";
import { useCachedResource } from "@/lib/useCachedResource";

import {
  FEED_SHOWN,
  type FeedAct,
  type FeedAnswer,
  type FeedCard as Card,
  actLeavesCard,
  actRows,
  actSpent,
  cardTime,
  coversNeeds,
  drawnActs,
  feedAppIcon,
  feedFailedLines,
  feedGroups,
  feedKey,
  fetchFeed,
  needsRowFor,
  sourceLine,
} from "./feed";
import { CardError, HomeCard, rowMover } from "./HomeCard";
import { emptyNeedsLine } from "./myDay";
import { needsKey, runAct } from "./needs";
import { EMPTY_ACTS, acquireReader, actsSnapshot, removeRow, setRowError, subscribeActs } from "./needsActs";
import { joinNeedsPoll } from "./ShellBell";

const DANGER = accentForHue("red");

export interface Feed {
  answer: FeedAnswer | undefined;
  loading: boolean;
  error: string | null;
  refresh: () => void;
  /** The needs rows an act took off, from the one store (`needsActs.ts`). */
  marked: ReadonlySet<string>;
  /** Each needs row's error, from the same store. */
  errors: Readonly<Record<string, string>>;
  act: (card: Card, act: FeedAct, from: HTMLElement | null) => void;
}

/**
 * The feed and its acts. My Day calls it once and hands it to the column.
 *
 * ⚠️ The feed keeps no removed set of its own. Each act marks the needs rows
 * it closes (`actRows`) in the one store that the bell and the sidebar read.
 * That store forgets a mark when the needs view no longer holds its row, or
 * after its 30 s hold, so a task that comes back shows again. A card leaves
 * only while its marked rows close every kind it holds (`cardHidden`).
 */
export function useFeed(enabled: boolean): Feed {
  const feed = useCachedResource<FeedAnswer>(enabled ? feedKey() : null, () => fetchFeed());
  const { refresh } = feed;
  const { removed: marked, errors } = useSyncExternalStore(subscribeActs, actsSnapshot, () => EMPTY_ACTS);

  // Count this reader (`needsActs.ts` rule 1), as `useNeedsYou` does.
  useEffect(() => acquireReader(), []);

  // The one poll: this refresh owns the feed key. It covers the needs key
  // only while its last read held a needs view (`coversNeeds`). After a
  // failed read, the needs poller reads for the bell and the sidebar again.
  const covers = coversNeeds(feed.data, feed.error);
  useEffect(
    () => (enabled ? joinNeedsPoll(refresh, feedKey(), covers ? [needsKey()] : []) : undefined),
    [enabled, refresh, covers],
  );

  const act = useCallback((card: Card, a: FeedAct, from: HTMLElement | null) => {
    const item = needsRowFor(card, a);
    if (item) void runAct(item, feedActMover(actRows(a), from));
  }, []);

  return {
    answer: feed.data,
    loading: feed.loading,
    error: feed.error,
    refresh,
    marked,
    errors,
    act,
  };
}

/**
 * The row half of a feed act (`rowMover`): it marks EVERY needs row the act
 * closes in the one store, so a merged read takes each `projects:<id>` row
 * out of the bell at once. A failure puts them all back, with the error on
 * the first.
 */
export function feedActMover(rows: readonly string[], from: HTMLElement | null = null): DoneRow {
  const move = (_: string, gone: boolean) => {
    for (const row of rows) removeRow(row, gone);
  };
  const fail = (_: string, message: string | null) => setRowError(rows[0], message);
  return rowMover(rows[0], move, fail, from);
}

/** Show the next cards when the member scrolls to the end of the column. */
function useRevealOnScroll(onReveal: () => void, active: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!active || !el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) onReveal();
    });
    io.observe(el);
    return () => io.disconnect();
  }, [active, onReveal]);
  return ref;
}

/** THE column of My Day's feed. */
export function FeedColumn({
  feed,
  now,
  className = "",
}: {
  feed: Feed;
  /** The page's clock, by the minute. A render reads no clock itself. */
  now: Date | null;
  className?: string;
}) {
  const [shown, setShown] = useState(FEED_SHOWN);
  const reveal = useCallback(() => setShown((n) => n + FEED_SHOWN), []);
  const { groups, hidden } = feed.answer
    ? feedGroups(feed.answer, shown, feed.marked)
    : { groups: [], hidden: 0 };
  const sentinel = useRevealOnScroll(reveal, hidden > 0);
  const nowMs = now?.getTime() ?? null;
  const failed = feed.answer ? feedFailedLines(feed.answer.sources) : [];

  let body: React.ReactNode;
  if (feed.answer === undefined) {
    body = feed.loading ? (
      <div role="status" aria-busy="true" aria-label="Loading" className="flex flex-col gap-3">
        <Skeleton className="h-28 w-full rounded-xl" />
        <Skeleton className="h-24 w-full rounded-xl" />
        <Skeleton className="h-28 w-full rounded-xl" />
      </div>
    ) : (
      <div className="rounded-xl border border-border bg-card px-2">
        <CardError message="Your day could not load." onRetry={feed.refresh} />
      </div>
    );
  } else if (groups.length === 0) {
    body = (
      <div className="flex items-center gap-2.5 rounded-xl border border-border bg-card px-4 py-4">
        <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${accentForHue("green").soft}`}>
          <Icon name="Check" size={14} className={accentForHue("green").text} />
        </span>
        <span className="text-sm text-muted-foreground">{emptyNeedsLine(failed.length > 0)}</span>
      </div>
    );
  } else {
    body = (
      <div className="flex flex-col gap-5">
        {groups.map((group) => (
          <div key={group.id} role="group" aria-labelledby={`feed-group-${group.id}`} data-feed-group={group.id}>
            <div id={`feed-group-${group.id}`} className="px-1 pb-2 text-xs font-medium text-muted-foreground">
              {group.label}
            </div>
            <div className="flex flex-col gap-3">
              {group.cards.map((card) => (
                <FeedCard
                  key={card.signal_id}
                  card={card}
                  nowMs={nowMs}
                  marked={feed.marked}
                  error={card.acts.flatMap(actRows).map((r) => feed.errors[r]).find(Boolean)}
                  onAct={(a, from) => feed.act(card, a, from)}
                />
              ))}
            </div>
          </div>
        ))}
        {hidden > 0 ? (
          <div ref={sentinel} className="flex justify-center">
            <Button variant="ghost" size="sm" icon="ChevronDown" onClick={reveal}>
              Show {Math.min(hidden, FEED_SHOWN)} more
            </Button>
          </div>
        ) : (
          <p className="px-1 text-center text-xs text-muted-foreground">You are caught up.</p>
        )}
      </div>
    );
  }

  // A stale feed under a failed refresh keeps its cards, and says so.
  const stale = feed.answer !== undefined && feed.error;
  return (
    <div data-feed="" data-testid="my-day-feed" className={`flex min-w-0 flex-col gap-3 ${className}`}>
      {/* Focus lands here when the last act of the feed leaves. */}
      <h2 tabIndex={-1} data-leave-focus="" className="sr-only">
        Your day
      </h2>
      {body}
      {failed.length > 0 || stale ? (
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-1">
          <div className="flex min-w-0 flex-col gap-0.5">
            {failed.map((line) => (
              <p key={line} className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Icon name="Info" size={12} className="shrink-0" />
                {line}
              </p>
            ))}
            {stale ? (
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Icon name="Info" size={12} className="shrink-0" />
                Could not refresh your day.
              </p>
            ) : null}
          </div>
          <Button variant="text" size="none" icon="RefreshCw" onClick={feed.refresh} className="min-h-8 gap-1 text-xs font-medium">
            Retry
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** An id that is safe in `id` and `aria-labelledby`: no spaces, no odd marks. */
const safeId = (raw: string) => raw.replace(/[^A-Za-z0-9_:-]/g, "-");

/**
 * One card of the feed (§4.2): the source line, the headline as the door,
 * the why line and the acts. Pure: it draws what it gets.
 */
export function FeedCard({
  card,
  nowMs,
  marked,
  error,
  onAct,
}: {
  card: Card;
  /** `null` before the client clock exists: the card then prints no time. */
  nowMs: number | null;
  /** The needs rows an act took off. A spent act draws nothing. */
  marked?: ReadonlySet<string>;
  error?: string;
  onAct: (act: FeedAct, from: HTMLElement) => void;
}) {
  const time = nowMs === null ? "" : cardTime(card, nowMs);
  const acts = drawnActs(card).filter((a) => !marked || !actSpent(a, marked));
  const eyebrow = (
    <span className="flex min-w-0 items-center gap-1.5" data-feed-source="">
      <Icon name={feedAppIcon(card.app)} size={13} className="shrink-0" />
      <span className="truncate">{sourceLine(card)}</span>
      {time ? (
        <span className={`ml-auto shrink-0 ${card.kind === "overdue" ? DANGER.text : ""}`}>{time}</span>
      ) : null}
    </span>
  );
  return (
    <HomeCard
      title={card.title}
      titleHref={card.href}
      eyebrow={eyebrow}
      testId={`feed-card-${safeId(card.signal_id)}`}
    >
      {card.why ? (
        <p className="flex items-start gap-1.5 px-2 pb-1 text-xs text-muted-foreground" data-feed-why="">
          <Icon name="Lightbulb" size={13} className="mt-px shrink-0" />
          <span>{card.why}</span>
        </p>
      ) : null}
      {acts.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2 px-2 pt-1 pb-1">
          {acts.map((a, i) =>
            a.way === "tap" ? (
              <Button
                key={a.id}
                variant={i === 0 ? "primary" : "secondary"}
                size="sm"
                icon={a.act === "done" ? "Check" : "CheckCheck"}
                data-row-act=""
                data-leaves-card={actLeavesCard(card, a) ? "" : undefined}
                aria-label={`${a.label}: ${card.title}`}
                onClick={(e) => onAct(a, e.currentTarget)}
              >
                {a.label}
              </Button>
            ) : (
              <Link
                key={a.id}
                href={a.href ?? card.href}
                data-feed-open=""
                className="inline-flex min-h-8 items-center gap-1 rounded-md px-1.5 text-xs font-medium text-primary hover:underline underline-offset-2"
              >
                {a.label}
                <Icon name="ArrowRight" size={12} />
              </Link>
            ),
          )}
        </div>
      ) : null}
      {error ? (
        <p role="alert" className={`px-2 pb-1 text-xs ${DANGER.text}`}>
          {error}
        </p>
      ) : null}
    </HomeCard>
  );
}
