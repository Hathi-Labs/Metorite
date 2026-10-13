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
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import type { DoneRow } from "@/app/tasks/lib/completeFromHome";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { accentForHue } from "@/lib/statusAccent";
import { useCachedResource } from "@/lib/useCachedResource";

import {
  FEED_SHOWN,
  type ActOverlay,
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
  feedHiddenRows,
  feedKey,
  fetchFeed,
  needsRowFor,
  overlayAdd,
  overlayDrop,
  overlayExpired,
  overlayNextEnd,
  sourceLine,
} from "./feed";
import { CardError, HomeCard, rowMover } from "./HomeCard";
import { emptyNeedsLine } from "./myDay";
import { needsKey, runAct } from "./needs";
import { EMPTY_ACTS, acquireReader, actsSnapshot, removeRow, setRowError, subscribeActs } from "./needsActs";
import { joinNeedsPoll } from "./ShellBell";

const DANGER = accentForHue("red");

/** The empty overlay. Never written: each change makes a new map. */
const NO_ACTS: ActOverlay = new Map();

export interface Feed {
  answer: FeedAnswer | undefined;
  loading: boolean;
  error: string | null;
  refresh: () => void;
  /** The needs rows that are off: the one store and the feed's overlay (`feedHiddenRows`). */
  marked: ReadonlySet<string>;
  /** Each needs row's error, from the same store. */
  errors: Readonly<Record<string, string>>;
  act: (card: Card, act: FeedAct, from: HTMLElement | null) => void;
}

/**
 * The feed and its acts. My Day calls it once and hands it to the column.
 *
 * Two sources say which needs rows are off (`feedHiddenRows`):
 *
 *   - the one store `needsActs.ts`, which the bell and the sidebar read too,
 *     so a Done in the bell hides the feed's card at once;
 *   - the overlay of the feed's OWN acts (`ActOverlay`). The needs store ends
 *     a mark against the needs answer, which lands before the feed answer.
 *     The overlay ends against the feed answer: an answer fetched after the
 *     act is the truth. Else it ends after `ACT_HOLD_MS`.
 *
 * A card leaves only while those rows close every kind it holds (`cardHidden`).
 */
export function useFeed(enabled: boolean): Feed {
  const feed = useCachedResource<FeedAnswer>(enabled ? feedKey() : null, () => fetchFeed());
  const { refresh } = feed;
  const { removed: needsMarks, errors } = useSyncExternalStore(subscribeActs, actsSnapshot, () => EMPTY_ACTS);

  // Count this reader (`needsActs.ts` rule 1), as `useNeedsYou` does.
  useEffect(() => acquireReader(), []);

  // The one poll (`joinFeedPoll`).
  const covers = coversNeeds(feed.data, feed.error);
  useEffect(() => (enabled ? joinFeedPoll(refresh, covers) : undefined), [enabled, refresh, covers]);

  // The overlay. A ref holds the newest copy, so a second tap in the same
  // frame already sees the first one (`runFeedAct` refuses it).
  const overlayRef = useRef<ActOverlay>(NO_ACTS);
  const [overlay, setOverlayState] = useState<ActOverlay>(NO_ACTS);
  const changeOverlay = useCallback((change: (o: ActOverlay) => ActOverlay) => {
    const next = change(overlayRef.current);
    if (next === overlayRef.current) return;
    overlayRef.current = next;
    setOverlayState(next);
  }, []);
  const fetchedAt = feed.data?.fetchedAt ?? 0;
  const fetchedRef = useRef(fetchedAt);
  useEffect(() => {
    fetchedRef.current = fetchedAt;
  });

  // Each overlay row ends after its hold, and its needs mark ends with it.
  useEffect(() => {
    const wait = overlayNextEnd(overlay, Date.now());
    if (wait === null) return;
    const id = setTimeout(() => changeOverlay((o) => expireFeedActs(o, Date.now())), wait);
    return () => clearTimeout(id);
  }, [overlay, changeOverlay]);

  const marked = useMemo(() => feedHiddenRows(needsMarks, overlay, fetchedAt), [needsMarks, overlay, fetchedAt]);

  const act = useCallback(
    (card: Card, a: FeedAct, from: HTMLElement | null) => {
      void runFeedAct(card, a, from, {
        held: () => feedHiddenRows(actsSnapshot().removed, overlayRef.current, fetchedRef.current),
        overlay: changeOverlay,
      });
    },
    [changeOverlay],
  );

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
 * Join the one poll for the feed. The feed's refresh owns the feed key. It
 * covers the needs key only when `covers` (`coversNeeds`): after a failed
 * read, or an answer with no needs view, the needs poller reads for the bell
 * and the sidebar again on the next tick. Returns the leave.
 */
export function joinFeedPoll(refresh: () => void, covers: boolean): () => void {
  return joinNeedsPoll(refresh, feedKey(), covers ? [needsKey()] : []);
}

/** What `runFeedAct` reads and writes, so a test can drive it with no React. */
export interface FeedActDeps {
  /** The needs rows that are off now: `feedHiddenRows` of the store and the overlay. */
  held: () => ReadonlySet<string>;
  /** Change the overlay of the feed's own acts. */
  overlay: (change: (o: ActOverlay) => ActOverlay) => void;
  now?: () => number;
}

/**
 * Run one act of a feed card through the owning app (`runAct`), as NS-3 does.
 *
 *   - An act whose rows are all off already does not run again: "blocked".
 *   - The act marks its needs rows in `needsActs`, for the bell and the
 *     sidebar, and adds them to the overlay at the time of the act.
 *   - The overlay time moves to the moment the write began (`keep`) and to
 *     the moment the act settled, so only a feed answer fetched after the
 *     write ends the overlay.
 *   - A show (Undo, a question answered no) or a failure takes the rows back
 *     out of both.
 */
export async function runFeedAct(
  card: Card,
  a: FeedAct,
  from: HTMLElement | null,
  deps: FeedActDeps,
): Promise<"done" | "kept" | "failed" | "blocked" | null> {
  const item = needsRowFor(card, a);
  if (!item) return null;
  const rows = actRows(a);
  const held = deps.held();
  if (rows.every((r) => held.has(r))) return "blocked";
  const now = deps.now ?? Date.now;
  const mover = feedActMover(rows, from);
  const stamp = () => deps.overlay((o) => overlayAdd(o, rows, now()));
  const clear = () => deps.overlay((o) => overlayDrop(o, rows));
  const row: DoneRow = {
    hide() {
      mover.hide();
      stamp();
    },
    show() {
      mover.show();
      clear();
    },
    fail(message) {
      mover.fail(message);
      clear();
    },
    keep() {
      mover.keep?.();
      stamp();
    },
  };
  const out = await runAct(item, row);
  if (out === "done") deps.overlay((o) => (rows.every((r) => o.has(r)) ? overlayAdd(o, rows, now()) : o));
  return out;
}

/**
 * End each overlay row whose hold is over, and its needs mark with it. A
 * Done that never landed then shows its card again, with a live act.
 */
export function expireFeedActs(overlay: ActOverlay, now: number): ActOverlay {
  const gone = overlayExpired(overlay, now);
  for (const row of gone) removeRow(row, false);
  return overlayDrop(overlay, gone);
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
