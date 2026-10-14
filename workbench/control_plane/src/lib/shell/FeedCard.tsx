"use client";

/**
 * My Day as a live feed: the column and its cards (`my_day_feed.md` §4.2 and
 * §14.1, WS-58 MD-1b). Behind `NEXT_PUBLIC_MY_DAY_FEED`, default OFF
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
 * **The life of a card** (WS-58 MD-2b, `feedState.ts`). The column is the
 * ONLY seen observer, and the bell never observes. A tap act that succeeds
 * also records `acted`. The menu snoozes, dismisses or marks a card "Not
 * useful" through `ContextMenu`, the one menu, and it hides the card through
 * an overlay of its own. It never sets a needs mark, so the bell's count does
 * not change. The order holds during a visit, and new cards wait behind the
 * "N new" pill (§7 rule 4). Later draws folded, with its count.
 *
 * **Handled** (WS-58 MD-2c, §6.7) draws folded after Later, as "Handled
 * today" and its count. Each row names the entity, who closed it and the
 * time. It is never a card: no menu, no act, no sighting, and no place in
 * the cut or the pill.
 *
 * Fences: `feed.test.ts`, `feedState.test.ts`, `feedHandled.test.ts` and
 * `myDayPage.test.ts`.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import type { DoneRow } from "@/app/tasks/lib/completeFromHome";
import { ContextMenu, type CtxItem } from "@/components/ContextMenu";
import Icon, { themedIcon } from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { accentForHue } from "@/lib/statusAccent";
import { onClear, read } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";

import {
  FEED_SHOWN,
  GROUP_LABELS,
  PENDING,
  type ActOverlay,
  type FeedAct,
  type FeedAnswer,
  type FeedCard as Card,
  type HandledEntry,
  type Visit,
  actLeavesCard,
  actRows,
  actSpent,
  allowCarry,
  cappedLinks,
  cardTime,
  columnView,
  coversNeeds,
  drawnActs,
  feedAppIcon,
  feedFailedLines,
  feedHiddenRows,
  feedKey,
  fetchFeed,
  forgetCarry,
  handledMeta,
  needsRowFor,
  overlayAdd,
  overlayDrop,
  overlayExpired,
  overlayNextEnd,
  sendable,
  sourceLine,
  startVisit,
  stateMark,
} from "./feed";
import {
  MENU_CHOICES,
  SEEN_FLUSH_MS,
  SEEN_RATIO,
  type MenuChoice,
  type MenuOverlay,
  type StateQueue,
  createDwell,
  createStateQueue,
  menuHidden,
  menuSettle,
  runMenuAct,
  stateItem,
} from "./feedState";
import { CardError, HomeCard, rowMover } from "./HomeCard";
import { emptyNeedsLine } from "./myDay";
import { carryNeeds, needsKey, runAct } from "./needs";
import {
  EMPTY_ACTS,
  acquireReader,
  actsSnapshot,
  markedAt,
  removeRow,
  setRowError,
  subscribeActs,
} from "./needsActs";
import { joinNeedsPoll } from "./ShellBell";

const DANGER = accentForHue("red");

/** The empty overlay. Never written: each change makes a new map. */
const NO_ACTS: ActOverlay = new Map();

/** The empty overlay of the menu acts. Never written either. */
const NO_MENU: MenuOverlay = new Map();

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
  /** The cards that a menu act hid (`menuHidden`), by `signal_id`. Never a needs row. */
  hidden: ReadonlySet<string>;
  /** The error line of a failed menu act, by `signal_id`. */
  menuErrors: Readonly<Record<string, string>>;
  /** Run one menu act (`runMenuAct`). */
  menu: (card: Card, choice: MenuChoice) => void;
  /** The column saw this card (§6.2). Only the column calls it. */
  seen: (card: Card) => void;
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

  // One provider run on a cold load (`carryFeedNeeds`). A LAYOUT effect on
  // purpose: the layout phase of a commit runs before every passive effect
  // of it, and each needs reader (My Day, the bell, the sidebar) starts its
  // read in a passive effect. So the carrier is in place before any of them.
  useLayoutEffect(() => (enabled ? carryFeedNeeds() : undefined), [enabled]);

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
  useEffect(
    () =>
      watchOverlayEnds(
        () => overlayRef.current,
        (now) => changeOverlay((o) => expireFeedActs(o, now)),
      ),
    [overlay, changeOverlay],
  );

  const marked = useMemo(() => feedHiddenRows(needsMarks, overlay, fetchedAt), [needsMarks, overlay, fetchedAt]);

  // The queue of the seen observer and of `acted` (`feedState.ts`). One POST
  // each 10 s, and the rest on `pagehide` and when the column leaves.
  // ⚠️ A queue holds ONE member's items (fix round 1). A change of member
  // empties the cache, and the clear gives this hook a new, empty queue. The
  // queue also refuses to send once the member changed (`createStateQueue`).
  const queueRef = useRef<StateQueue | null>(null);
  if (queueRef.current === null) queueRef.current = createStateQueue();
  useEffect(
    () =>
      onClear(() => {
        queueRef.current = createStateQueue();
      }),
    [],
  );
  useEffect(() => (enabled ? sendStateQueue(() => queueRef.current!) : undefined), [enabled]);

  const act = useCallback(
    (card: Card, a: FeedAct, from: HTMLElement | null) => {
      void runFeedAct(card, a, from, {
        held: () => feedHiddenRows(actsSnapshot().removed, overlayRef.current, fetchedRef.current),
        overlay: changeOverlay,
        acted: () => {
          if (sendable(card)) queueRef.current?.add(stateItem(card, "acted"));
        },
      });
    },
    [changeOverlay],
  );

  const seen = useCallback((card: Card) => {
    if (sendable(card)) queueRef.current?.see(stateItem(card, "seen"));
  }, []);

  // The overlay of the menu acts. It is NOT `needsActs`: the bell and the
  // sidebar counts never change for a menu act (§6.3, §6.8 item 9).
  const menuRef = useRef<MenuOverlay>(NO_MENU);
  const [menuOverlay, setMenuOverlay] = useState<MenuOverlay>(NO_MENU);
  const changeMenu = useCallback((change: (o: MenuOverlay) => MenuOverlay) => {
    const next = change(menuRef.current);
    if (next === menuRef.current) return;
    menuRef.current = next;
    setMenuOverlay(next);
  }, []);
  // A feed answer fetched after the write ends the hide of that card.
  useEffect(() => changeMenu((o) => menuSettle(o, fetchedAt)), [fetchedAt, changeMenu]);
  const hidden = useMemo(() => menuHidden(menuOverlay, fetchedAt), [menuOverlay, fetchedAt]);

  const [menuErrors, setMenuErrors] = useState<Readonly<Record<string, string>>>({});
  const setMenuError = useCallback((id: string, message: string | null) => {
    setMenuErrors((now) => {
      if (!message && !(id in now)) return now;
      const next = { ...now };
      if (message) next[id] = message;
      else delete next[id];
      return next;
    });
  }, []);

  const toast = useToast();
  const menu = useCallback(
    (card: Card, choice: MenuChoice) => {
      void runMenuAct(card, choice, {
        overlay: changeMenu,
        toast: (spec) => {
          toast.show(spec);
        },
        setError: setMenuError,
      });
    },
    [changeMenu, toast, setMenuError],
  );

  return {
    answer: feed.data,
    loading: feed.loading,
    error: feed.error,
    refresh,
    marked,
    errors,
    act,
    hidden,
    menuErrors,
    menu,
    seen,
  };
}

/**
 * Send the state queue: one batch each `SEEN_FLUSH_MS`, and every item on
 * `pagehide` and at the stop, with `keepalive`. Returns the stop. It reads
 * the queue of NOW on each send, because a change of member replaces it.
 */
export function sendStateQueue(queue: () => StateQueue): () => void {
  const tick = window.setInterval(() => void queue().flush(), SEEN_FLUSH_MS);
  const hide = () => queue().drain();
  window.addEventListener("pagehide", hide);
  return () => {
    window.clearInterval(tick);
    window.removeEventListener("pagehide", hide);
    queue().drain();
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

/**
 * Make the feed the carrier of the needs view (`carryNeeds` in `needs.ts`).
 * A needs read then joins the feed's read of the feed key, the one request
 * in flight, and takes the needs view out of its answer. So a cold load of
 * My Day runs the providers once. Returns the release.
 */
export function carryFeedNeeds(): () => void {
  return carryNeeds(() => read(feedKey(), () => fetchFeed()).then((answer) => answer.needs));
}

/** What `runFeedAct` reads and writes, so a test can drive it with no React. */
export interface FeedActDeps {
  /** The needs rows that are off now: `feedHiddenRows` of the store and the overlay. */
  held: () => ReadonlySet<string>;
  /** Change the overlay of the feed's own acts. */
  overlay: (change: (o: ActOverlay) => ActOverlay) => void;
  /** Record `acted` for the card (§6.8 item 4). It runs only after a success. */
  acted?: () => void;
  now?: () => number;
}

/**
 * Run one act of a feed card through the owning app (`runAct`), as NS-3 does.
 *
 *   - An act whose rows are all off already does not run again: "blocked".
 *   - The act marks its needs rows in `needsActs`, for the bell and the
 *     sidebar, and adds them to the overlay as `PENDING`. No feed answer is
 *     the truth for a pending row (fix round 3: a cold hydrate comes first).
 *   - The overlay takes a real time when the write begins (`keep`), and moves
 *     it forward when the act settles. So only a feed answer fetched after
 *     the write began ends the overlay.
 *   - A show (Undo, a question answered no) or a failure takes the rows back
 *     out of both.
 *   - An act that succeeds records `acted` (`deps.acted`, §6.8 item 4). A
 *     kept or failed act records nothing.
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
  const mover = feedActMover(rows, from, now);
  const stamp = () => deps.overlay((o) => overlayAdd(o, rows, now()));
  // An act that takes the whole card away must not come back from the carry
  // of a failed source, with a live act (fix round 1, P1-a). Undo, a "no"
  // and a failure let the carry keep it again.
  const leaves = actLeavesCard(card, a);
  const clear = () => {
    deps.overlay((o) => overlayDrop(o, rows));
    if (leaves) allowCarry(card.signal_id);
  };
  const row: DoneRow = {
    hide() {
      mover.hide();
      deps.overlay((o) => overlayAdd(o, rows, PENDING));
      if (leaves) forgetCarry(card.signal_id);
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
  if (out === "done") {
    deps.overlay((o) => (rows.every((r) => o.has(r)) ? overlayAdd(o, rows, now()) : o));
    if (leaves) forgetCarry(card.signal_id, now());
    deps.acted?.();
  }
  return out;
}

/**
 * End each overlay row whose hold is over, and its needs mark with it. A
 * Done that never landed then shows its card again, with a live act.
 *
 * ⚠️ It clears a needs mark only when the mark is not newer than the
 * overlay row. A newer mark is the bell's own act on the same row, and that
 * mark stays (fix round 3). A row with no mark has nothing to clear.
 */
export function expireFeedActs(overlay: ActOverlay, now: number): ActOverlay {
  const gone = overlayExpired(overlay, now);
  for (const row of gone) {
    const marked = markedAt(row);
    if (marked !== undefined && marked <= (overlay.get(row) ?? -Infinity)) removeRow(row, false);
  }
  return overlayDrop(overlay, gone);
}

/** The least wait between two passes over the overlay's holds, in ms. */
export const OVERLAY_MIN_WAIT_MS = 50;

/**
 * Watch the holds of the overlay. Each pass expires what is due, then ALWAYS
 * books the next pass from `overlayNextEnd`, at least `OVERLAY_MIN_WAIT_MS`
 * away. So a timer that fires early, or a clock that steps back, books one
 * more pass and never leaves a row for the life of the page (fix round 3).
 * Returns the stop.
 */
export function watchOverlayEnds(
  read: () => ActOverlay,
  expire: (now: number) => void,
  now: () => number = Date.now,
): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const book = () => {
    const wait = overlayNextEnd(read(), now());
    timer = wait === null ? undefined : setTimeout(pass, Math.max(OVERLAY_MIN_WAIT_MS, wait));
  };
  const pass = () => {
    expire(now());
    book();
  };
  book();
  return () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  };
}

/**
 * The row half of a feed act (`rowMover`): it marks EVERY needs row the act
 * closes in the one store, so a merged read takes each `projects:<id>` row
 * out of the bell at once. A failure puts them all back, with the error on
 * the first.
 */
export function feedActMover(
  rows: readonly string[],
  from: HTMLElement | null = null,
  now: () => number = Date.now,
): DoneRow {
  const move = (_: string, gone: boolean) => {
    for (const row of rows) removeRow(row, gone, now());
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

/**
 * The seen observer (§6.2). THE one: only the feed column calls it, and the
 * bell never does. Each card that the client may write wears
 * `data-feed-seen`. Half of it in view for 1.5 s on a visible tab is a
 * sighting (`createDwell`), and `feed.seen` puts it in the queue.
 */
function useSeenObserver(feed: Pick<Feed, "answer" | "seen">, active: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  const io = useRef<IntersectionObserver | null>(null);
  const watched = useRef(new Map<Element, string>());
  const dwell = useRef<ReturnType<typeof createDwell> | null>(null);
  const latest = useRef(feed);
  useEffect(() => {
    latest.current = feed;
  });

  useEffect(() => {
    if (!active || typeof IntersectionObserver === "undefined") return;
    const d = createDwell((id) => {
      const card = latest.current.answer?.cards.find((c) => c.signal_id === id);
      if (card) latest.current.seen(card);
    });
    const observer = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const id = watched.current.get(e.target);
          if (!id) continue;
          if (e.isIntersecting && e.intersectionRatio >= SEEN_RATIO) d.enter(id);
          else d.leave(id);
        }
      },
      { threshold: [0, SEEN_RATIO, 1] },
    );
    const onVisibility = () => d.visibility(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVisibility);
    dwell.current = d;
    io.current = observer;
    const seen = watched.current;
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      observer.disconnect();
      d.stop();
      seen.clear();
      io.current = null;
      dwell.current = null;
    };
  }, [active]);

  // After each render: watch each new card, and forget each card that left.
  useEffect(() => {
    const observer = io.current;
    const root = ref.current;
    if (!observer || !root) return;
    for (const [el, id] of [...watched.current]) {
      if (el.isConnected && root.contains(el)) continue;
      observer.unobserve(el);
      watched.current.delete(el);
      dwell.current?.leave(id);
    }
    for (const el of root.querySelectorAll<HTMLElement>("[data-feed-seen]")) {
      const id = el.dataset.feedSeen;
      if (!id || watched.current.get(el) === id) continue;
      watched.current.set(el, id);
      observer.observe(el);
    }
  });
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
  const [laterOpen, setLaterOpen] = useState(false);
  const [handledOpen, setHandledOpen] = useState(false);

  // The visit (§7 rule 4): it starts with the first answer this column draws,
  // and it holds each card's place until the member takes the new cards in.
  // `columnView` is the one rule of the visit, the cut and the pill.
  const [visit, setVisit] = useState<Visit | null>(null);
  const view = columnView(feed.answer, visit, { shown, marked: feed.marked, hidden: feed.hidden });
  // The first answer starts the visit: state from the render, as React's
  // "adjust state while rendering" pattern does it.
  if (visit === null && view.visit !== null) setVisit(view.visit);
  const fresh = view.fresh;
  const takeNew = () => setVisit(feed.answer ? startVisit(feed.answer.cards) : null);
  const { groups: allGroups, hidden } = view;
  const groups = allGroups.filter((g) => g.id !== "later");
  const later = allGroups.find((g) => g.id === "later");
  const sentinel = useRevealOnScroll(reveal, hidden > 0);
  const nowMs = now?.getTime() ?? null;
  const failed = feed.answer ? feedFailedLines(feed.answer.sources) : [];
  const capped = feed.answer?.capped ?? [];
  const handled = feed.answer?.handled ?? [];
  const columnRef = useSeenObserver(feed, feed.answer !== undefined);

  const drawCard = (card: Card) => (
    <div key={card.signal_id} data-feed-seen={sendable(card) ? card.signal_id : undefined}>
      <FeedCard
        card={card}
        nowMs={nowMs}
        marked={feed.marked}
        error={feed.menuErrors[card.signal_id] ?? card.acts.flatMap(actRows).map((r) => feed.errors[r]).find(Boolean)}
        onAct={(a, from) => feed.act(card, a, from)}
        onMenu={(choice) => feed.menu(card, choice)}
      />
    </div>
  );

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
  } else {
    body = (
      <div className="flex flex-col gap-5">
        {fresh > 0 ? (
          // §7 rule 4: a new card never moves the cards under the pointer.
          // It waits here, and enters on a click or on the next visit.
          <div className="sticky top-2 z-10 flex justify-center">
            <Button variant="primary" size="sm" icon="ArrowUp" onClick={takeNew} data-feed-new-pill="">
              {fresh} new
            </Button>
          </div>
        ) : null}
        {groups.length === 0 ? (
          <div className="flex items-center gap-2.5 rounded-xl border border-border bg-card px-4 py-4">
            <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${accentForHue("green").soft}`}>
              <Icon name="Check" size={14} className={accentForHue("green").text} />
            </span>
            <span className="text-sm text-muted-foreground">{emptyNeedsLine(failed.length > 0)}</span>
          </div>
        ) : null}
        {groups.map((group) => (
          <div key={group.id} role="group" aria-labelledby={`feed-group-${group.id}`} data-feed-group={group.id}>
            <div id={`feed-group-${group.id}`} className="px-1 pb-2 text-xs font-medium text-muted-foreground">
              {group.label}
            </div>
            <div className="flex flex-col gap-3">{group.cards.map(drawCard)}</div>
            {cappedLinks(group, capped).map((link) => (
              <div key={link.app} className="flex justify-center pt-1">
                <Button
                  href={link.href}
                  variant="text"
                  size="none"
                  icon="ArrowRight"
                  data-feed-show-all={link.app}
                  className="min-h-8 gap-1 text-xs font-medium"
                >
                  {link.label}
                </Button>
              </div>
            ))}
          </div>
        ))}
        {hidden > 0 ? (
          <div ref={sentinel} className="flex justify-center">
            <Button variant="ghost" size="sm" icon="ChevronDown" onClick={reveal}>
              Show {Math.min(hidden, FEED_SHOWN)} more
            </Button>
          </div>
        ) : null}
        {later ? (
          // Later draws folded, with its count (MD-2b).
          <div role="group" aria-labelledby="feed-group-later" data-feed-group="later">
            {/* The same size and weight as the other group labels. */}
            <Button
              variant="text"
              size="none"
              icon={laterOpen ? "ChevronDown" : "ChevronRight"}
              aria-expanded={laterOpen}
              aria-controls="feed-later-cards"
              onClick={() => setLaterOpen((open) => !open)}
              className="min-h-8 gap-1 px-1 text-xs font-medium"
            >
              <span id="feed-group-later">{later.label}</span>
              <span className="text-muted-foreground" data-feed-later-count="">
                {later.cards.length}
              </span>
            </Button>
            {laterOpen ? (
              <div id="feed-later-cards" className="flex flex-col gap-3 pt-2">
                {later.cards.map(drawCard)}
              </div>
            ) : null}
          </div>
        ) : null}
        {handled.length > 0 ? (
          // Handled draws folded, after Later, with its count (MD-2c). It is
          // a list of what closed, never cards: it holds no act and no menu.
          <div role="group" aria-labelledby="feed-group-handled" data-feed-group="handled">
            <Button
              variant="text"
              size="none"
              icon={handledOpen ? "ChevronDown" : "ChevronRight"}
              aria-expanded={handledOpen}
              aria-controls="feed-handled-rows"
              onClick={() => setHandledOpen((open) => !open)}
              className="min-h-8 gap-1 px-1 text-xs font-medium"
            >
              <span id="feed-group-handled">{GROUP_LABELS.handled}</span>
              <span className="text-muted-foreground" data-feed-handled-count="">
                {handled.length}
              </span>
            </Button>
            {handledOpen ? (
              <ul id="feed-handled-rows" className="mt-2 flex flex-col rounded-xl border border-border bg-card p-1.5">
                {handled.map((entry, i) => (
                  <HandledRow key={`${entry.app}:${entry.href}:${entry.at ?? ""}:${i}`} entry={entry} nowMs={nowMs} />
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
        {hidden === 0 && groups.length > 0 ? (
          <p className="px-1 text-center text-xs text-muted-foreground">You are caught up.</p>
        ) : null}
      </div>
    );
  }

  // A stale feed under a failed refresh keeps its cards, and says so.
  const stale = feed.answer !== undefined && feed.error;
  return (
    <div ref={columnRef} data-feed="" data-testid="my-day-feed" className={`flex min-w-0 flex-col gap-3 ${className}`}>
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

/**
 * One row of Handled (MD-2c): the app's icon, the entity as the door to it,
 * and who closed it with the time ("You · 09:30"). The row draws as a row of
 * Needs you does, with no act. The door is `Button` with `href`, as each
 * link of this file is, so it keeps the house focus ring.
 */
export function HandledRow({ entry, nowMs }: { entry: HandledEntry; nowMs: number | null }) {
  return (
    <li>
      <Button
        href={entry.href}
        variant="ghost"
        size="none"
        layout="flex items-center"
        data-feed-handled-row=""
        className="min-h-11 w-full min-w-0 gap-3 px-2 py-1.5 text-left"
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-secondary text-muted-foreground">
          <Icon name={feedAppIcon(entry.app)} size={14} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-normal text-foreground">{entry.title}</span>
          <span className="block truncate text-xs font-normal text-muted-foreground" data-feed-handled-by="">
            {handledMeta(entry, nowMs)}
          </span>
        </span>
      </Button>
    </li>
  );
}

/** An id that is safe in `id` and `aria-labelledby`: no spaces, no odd marks. */
const safeId = (raw: string) => raw.replace(/[^A-Za-z0-9_:-]/g, "-");

/** The menu's items (§4.2 item 6, MD-2b): Snooze and its four choices, then Dismiss and "Not useful". */
export function cardMenuItems(onMenu: (choice: MenuChoice) => void): CtxItem[] {
  const item = (c: MenuChoice): CtxItem => ({
    kind: "item",
    label: c.label,
    icon: themedIcon(c.icon),
    onSelect: () => onMenu(c),
  });
  return [
    { kind: "label", label: "Snooze" },
    ...MENU_CHOICES.filter((c) => c.action === "snooze").map(item),
    { kind: "sep" },
    ...MENU_CHOICES.filter((c) => c.action !== "snooze").map(item),
  ];
}

/**
 * One card of the feed (§4.2): the source line, the headline as the door,
 * the why line, the acts and the menu. It draws what it gets. Its one state
 * is whether its menu is open.
 *
 * The source line ends with a dot for a `new` card, and "Updated" or "Still
 * open" (§6.3 rules 3 and 4). The menu is `ContextMenu`, THE menu, and a card
 * the client may not write (`sendable`) has none.
 */
export function FeedCard({
  card,
  nowMs,
  marked,
  error,
  onAct,
  onMenu,
}: {
  card: Card;
  /** `null` before the client clock exists: the card then prints no time. */
  nowMs: number | null;
  /** The needs rows an act took off. A spent act draws nothing. */
  marked?: ReadonlySet<string>;
  error?: string;
  onAct: (act: FeedAct, from: HTMLElement) => void;
  /** A menu act. With none, the card draws no menu. */
  onMenu?: (choice: MenuChoice) => void;
}) {
  const [menuAt, setMenuAt] = useState<{ x: number; y: number } | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const time = nowMs === null ? "" : cardTime(card, nowMs);
  const acts = drawnActs(card).filter((a) => !marked || !actSpent(a, marked));
  const mark = stateMark(card);
  const withMenu = !!onMenu && sendable(card);
  const closeMenu = () => {
    setMenuAt(null);
    const from = opener.current;
    const column = from?.closest<HTMLElement>("[data-feed]") ?? null;
    // Back to the opener. If a menu act took the card away, to the column's heading.
    requestAnimationFrame(() => {
      if (from?.isConnected) from.focus();
      else column?.querySelector<HTMLElement>("[data-leave-focus]")?.focus();
    });
  };
  const eyebrow = (
    <span className="flex min-w-0 items-center gap-1.5" data-feed-source="">
      <Icon name={feedAppIcon(card.app)} size={13} className="shrink-0" />
      <span className="truncate">
        {sourceLine(card)}
        {mark ? (
          <>
            {" · "}
            <span className="font-medium text-foreground" data-feed-mark="">
              {mark}
            </span>
          </>
        ) : null}
      </span>
      {card.state === "new" ? (
        <span role="img" aria-label="New" data-feed-new="" className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
      ) : null}
      {time ? (
        <span className={`ml-auto shrink-0 ${card.kind === "overdue" ? DANGER.text : ""}`}>{time}</span>
      ) : null}
      {withMenu ? (
        <Button
          variant="ghost"
          size="icon-xs"
          icon="MoreHorizontal"
          aria-label={`More for ${card.title}`}
          aria-haspopup="menu"
          aria-expanded={menuAt !== null}
          data-feed-menu=""
          className={`-my-1 shrink-0 ${time ? "" : "ml-auto"}`}
          onClick={(e) => {
            opener.current = e.currentTarget;
            const r = e.currentTarget.getBoundingClientRect();
            setMenuAt({ x: Math.max(4, r.right - 200), y: r.bottom + 4 });
          }}
        />
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
          {/* §4.2 item 5, by POSITION and never by way: act 0 is the one
              filled act, and each later act is outlined. An `open` act is
              the same primitive drawn as a real link (`Button` with `href`),
              so it keeps the house focus ring (MD-1b visual review). */}
          {acts.map((a, i) =>
            a.way === "tap" ? (
              <Button
                key={a.id}
                variant={i === 0 ? "primary" : "secondary"}
                size="sm"
                icon={a.act === "done" ? "Check" : "CheckCheck"}
                data-feed-act={i}
                data-row-act=""
                data-leaves-card={actLeavesCard(card, a) ? "" : undefined}
                aria-label={`${a.label}: ${card.title}`}
                onClick={(e) => onAct(a, e.currentTarget)}
              >
                {a.label}
              </Button>
            ) : (
              <Button
                key={a.id}
                href={a.href ?? card.href}
                variant={i === 0 ? "primary" : "secondary"}
                size="sm"
                data-feed-act={i}
                data-feed-open=""
              >
                {a.label}
              </Button>
            ),
          )}
        </div>
      ) : null}
      {error ? (
        <p role="alert" className={`px-2 pb-1 text-xs ${DANGER.text}`}>
          {error}
        </p>
      ) : null}
      {withMenu && menuAt ? (
        <ContextMenu x={menuAt.x} y={menuAt.y} autoFocus items={cardMenuItems(onMenu)} onClose={closeMenu} />
      ) : null}
    </HomeCard>
  );
}
