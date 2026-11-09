"use client";

/**
 * My Day — the personal altitude of Home, at `/` (`navigation_shell.md` §4,
 * NS-3). Behind `NEXT_PUBLIC_MY_DAY`, default OFF (`myDayOn`).
 *
 * Its one job is to NOTICE: answer "What needs me today?" in five seconds.
 * The order on the page is the order of that question. Needs you comes
 * first and widest, then Today, then Next actions. From `xl`, Today moves to
 * a right rail and the other two stack on the left.
 *
 * The page computes nothing an app owns. Each card reads its app's own route
 * through `useCachedResource`, and shows `Skeleton` only on a true miss. A
 * card shows only for an app the member holds (`cardsFor`), and each card
 * fails on its own, with Retry.
 *
 * "All apps" at the foot opens the shell's one launcher, so the old grid is
 * one tap away.
 *
 * It opens with its title bar, `AppTopBar` (owner, 2026-10-10), as every app
 * does. The greeting stays a `PageHeader` under it: a sentence and the date
 * are the page's content, and the bar carries the name only.
 *
 * **The live feed** (`my_day_feed.md` §14.1, WS-58 MD-1b) is behind a second
 * flag, `NEXT_PUBLIC_MY_DAY_FEED` (`myDayFeedOn`), default OFF. With it on,
 * the cards above give way to one column of feed cards (`FeedColumn`), and
 * Today stays as the right rail from `xl`. Needs you and Next actions leave:
 * the feed holds their rows as cards. The greeting, the summary line and the
 * store's Undo stay. With it off, the page is the page above, unchanged.
 * With it on, the summary line counts the feed's cards and may say "The
 * rest is handled." (MD-2c, §6.7).
 *
 * **The brief card** (`core_brief.md` §5 item 1, BR-2c-2) is a third, separate
 * flag, `NEXT_PUBLIC_BRIEF_CARD` (`briefCardOn`). It sits above the cards or
 * the feed, in either layout, and draws nothing when the brief is absent.
 *
 * **The Do first band** (`package_host.md` §9.3, PKG-18a) is a fourth flag,
 * `NEXT_PUBLIC_TODAY_BAND` (`todayBandOn`). With the feed on, it sits above
 * the feed column and shows the top five cards with their reasons. It reads
 * the same feed answer as the column. `showTodayBand` is its one gate.
 *
 * **The panel** (`my_day_feed.md` §11.5, PK-1, D204) is a fifth flag,
 * `NEXT_PUBLIC_MY_DAY_PEEK` (`myDayPeekOn`). With it on, a plain click on a
 * task row opens the task in a panel at the right of the page, or in a full
 * sheet on a phone, and the path stays `/`. `useMyDayPeek` is its one seam.
 * Off, a row opens Projects, as before.
 */
import Link from "next/link";
import { useSession } from "next-auth/react";
import { useEffect, useState, useSyncExternalStore } from "react";

import TodayCard, { useTodayItems } from "@/app/calendar/components/TodayCard";
import { UndoToast } from "@/app/tasks/components/UndoToast";
import NextActionsCard from "@/app/tasks/components/NextActionsCard";
import { useAccess } from "@/components/AccessProvider";
import { AppTopBar } from "@/components/AppTopBar";
import PageHeader from "@/components/PageHeader";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { visibleSections } from "@/lib/nav";
import { useAssistantProfileEnabled } from "@/lib/useAssistantProfileEnabled";
import { useCachedResource } from "@/lib/useCachedResource";

import { ADDRESS_KEY, addressedGreetingLine, fetchAddressName } from "./addressName";
import AppLauncher from "./AppLauncher";
import { feedFailedLines, feedNeedsCount, handledClaim } from "./feed";
import BriefCard from "./BriefCard";
import { BRIEF_KEY, fetchBrief } from "./briefCard";
import { briefCardOn, briefCardOnServer } from "./briefCardFlag";
import { FeedColumn, useFeed } from "./FeedCard";
import NeedsYouCard, { useNeedsYou } from "./NeedsYouCard";
import { cardOrderFor, cardsFor, dateLine, failedLines, seesApprovals, summaryLine } from "./myDay";
import { myDayFeedOn, myDayFeedOnServer } from "./myDayFlag";
import { BUILT_CARDS } from "./presets";
import TodayBand from "./TodayBand";
import { showTodayBand, todayBandOn, todayBandOnServer } from "./todayBandFlag";
import { useMyDayPeek } from "./useMyDayPeek";
import { rereadFeed } from "./feedState";
import { needsKey } from "./needs";
import { CARD_PAGE_ATTR } from "@/components/CardPanel";
import { invalidate } from "@/lib/dataCache";
import { MyDayPeekContext } from "./myDayGo";
import { myDayPeekOn, myDayPeekOnServer } from "./myDayPeekFlag";
import { homePane } from "./shellNav";
import AiWaitingCard, { useAiWaiting } from "./AiWaitingCard";
import { publishMyDayCount } from "./myDayCount";
import { useShellPrefs } from "./shellPrefs";

// The feed flag goes through `useSyncExternalStore`, as `/` reads its own:
// the server render reads the build-time value only, and a browser's dev
// override takes effect after hydration, with no mismatch.
const noSubscribe = () => () => {};

/**
 * The clock, by the minute, on the client only. The server renders no time,
 * because its zone is not the member's, and a greeting that changes during
 * hydration is a mismatch.
 */
function useMinute(): Date | null {
  const minute = useSyncExternalStore(
    (notify) => {
      const id = window.setInterval(notify, 30_000);
      return () => window.clearInterval(id);
    },
    () => Math.floor(Date.now() / 60_000),
    () => null,
  );
  return minute === null ? null : new Date(minute * 60_000);
}

export default function MyDay() {
  const { access, loading: accessLoading } = useAccess();
  const { data: session } = useSession();
  const now = useMinute();
  const [launcherOpen, setLauncherOpen] = useState(false);
  // WS-63 PZ-4 (`personalization.md` §5.2): the name the assistant calls the
  // member, behind the gateway's yes for
  // the member's org (PZ-25, §14.2). Off, loading or failed, the header keeps
  // the session name (`addressName.ts`), and no address read goes out.
  const profileOn = useAssistantProfileEnabled();
  const address = useCachedResource<string>(
    profileOn ? ADDRESS_KEY : null,
    () => fetchAddressName(),
  );
  // This page renders only with the flag on, so its pane is My Day.
  const home = homePane(true);

  const cards = cardsFor(accessLoading ? [] : access.features, access.is_admin);
  const needs = useNeedsYou(!accessLoading && cards.needs, seesApprovals(access.features, access.is_admin));
  // The AI runs that wait on the member: a group of the page, and part of
  // its one count, as the sidebar's My Day row counts them.
  const aiWaiting = useAiWaiting();
  const today = useTodayItems(!accessLoading && cards.today, now);
  const feedOn = useSyncExternalStore(noSubscribe, myDayFeedOn, myDayFeedOnServer);
  const briefOn = useSyncExternalStore(noSubscribe, briefCardOn, briefCardOnServer);
  const bandOn = useSyncExternalStore(noSubscribe, todayBandOn, todayBandOnServer);
  const peekOn = useSyncExternalStore(noSubscribe, myDayPeekOn, myDayPeekOnServer);
  // The brief read is the page's, so the grid waits for it and paints once.
  // Off, the key is null: no read, and the brief never gates the page. A
  // cache hit has data at once, so a later visit is instant.
  const brief = useCachedResource(briefOn ? BRIEF_KEY : null, () => fetchBrief());
  const briefPending = briefOn && brief.data === undefined && brief.loading;
  // The feed's sources are the needs sources and Calendar, and Calendar
  // needs what Today needs, so a member with no needs card gets no feed.
  const feed = useFeed(feedOn && !accessLoading && cards.needs);
  // The column's visit number. It goes up when the member takes in the new
  // cards, and the Do first band then starts a new hold (PKG-Q15).
  const [feedVisit, setFeedVisit] = useState(0);
  const sections = visibleSections(accessLoading ? null : access.features, access.is_admin);
  // The member's preset orders the cards (NS-7, §8.1). It hides none: a card
  // the preset does not name follows the named ones. With the shell nav off,
  // the page keeps its own order.
  const shell = useShellPrefs();
  const held = BUILT_CARDS.filter((k) => cards[k as keyof typeof cards]);
  const order = cardOrderFor(held, shell);
  // ⚠️ With no known layout yet (a first visit, the read still out), the
  // cards wait, as they wait for access. Drawing the role's order first and
  // the member's after moved every card under the member's eye (round 2).
  const layoutPending = shell.enabled && shell.loading;

  // The summary waits for both reads. A feed that failed says nothing here,
  // because "Nothing needs you" would be a claim the page cannot back. A
  // calendar that failed drops its half of the sentence.
  //
  // With the live feed on (MD-2c, `my_day_feed.md` §6.7), the count is the
  // feed's open cards of Now and Needs you, after the column's own hides,
  // and not the bell's count: the bell still counts a snoozed card. "The
  // rest is handled." needs `handledClaim`. With the feed off, the line of
  // NS-3, unchanged.
  const fromFeed = feedOn && cards.needs;
  const feedAnswer = feed.answer;
  const needsPending =
    cards.needs && (fromFeed ? feedAnswer === undefined && feed.loading : needs.count === undefined && needs.loading);
  const needsFailed =
    cards.needs && (fromFeed ? feedAnswer === undefined && !feed.loading : needs.count === undefined && !needs.loading);
  const todayPending = cards.today && today.data === undefined && today.loading;
  const todayCount = cards.today && today.data ? today.data.length : null;
  const none = !accessLoading && !cards.needs && !cards.today && !cards.next;
  const summary =
    accessLoading || needsPending || todayPending
      ? "pending"
      : needsFailed || none
        ? null
        : fromFeed && feedAnswer
          ? summaryLine(
              feedNeedsCount(feedAnswer, feed.marked, feed.hidden) + aiWaiting.length,
              todayCount,
              feedFailedLines(feedAnswer.sources, feedAnswer.source_apps).length > 0,
              // A stale answer under a failed refresh keeps its count, and
              // loses its claim: the claim may be yesterday's (fix round 1).
              handledClaim(feedAnswer) && !feed.error,
            )
          : summaryLine((needs.count ?? 0) + aiWaiting.length, todayCount, failedLines(needs.sources).length > 0);
  // The sidebar's My Day row says the same number as the sentence.
  const pageCount =
    fromFeed && feedAnswer
      ? feedNeedsCount(feedAnswer, feed.marked, feed.hidden) + aiWaiting.length
      : needs.count !== undefined
        ? needs.count + aiWaiting.length
        : null;
  useEffect(() => {
    if (pageCount !== null) publishMyDayCount(pageCount);
  }, [pageCount]);

  // PK-1: a row opens its task in the panel. After a panel that wrote
  // something closes, the reads that drew the rows go out again, so the row
  // shows the change. ⚠️ A DROPPED key, never `refresh`: a forced read joins a
  // read already in flight (`dataCache.read`), and that read may predate the
  // write. A dropped key makes the next read a new one. A Projects write
  // already drops the Projects reads (Today, Next actions).
  const peek = useMyDayPeek(peekOn, {
    me: session?.user?.email ?? undefined,
    onSettled: () => {
      rereadFeed();
      invalidate(needsKey());
      today.refresh();
    },
  });
  // The grid below steps by the WINDOW width (`xl:`). With the card docked
  // beside it, the page column is 384 px narrower, and at 1280 three grid
  // columns left a feed title two letters long (PK-1 visual review). So while
  // the card is docked, the cards stand in one column and Today goes last.
  // Over the page, and closed, every class is as before.
  const wide = (classes: string) => (peek.docked ? "" : classes);

  return (
    <MyDayPeekContext.Provider value={peek.peek}>
    {/* The page root of the record card (`CardPanel`): a key from inside it
        reaches the card's Escape, and over the page the rest goes inert. */}
    <div {...{ [CARD_PAGE_ATTR]: "" }} className="flex h-full min-h-0 flex-col">
    {/* The compact bar on a phone (AppTopBar decides), so the page keeps
        its one h1 there too. */}
    <AppTopBar title={home.label} icon={home.icon} />
    {/* One row: the page column, `<main>`, and the card beside it while a
        task is docked (PK-1), as the CRM's row holds its record card. The
        card measures this row. With the flag off the row holds the column
        alone. */}
    <div className="flex min-h-0 flex-1">
    <main ref={peek.columnRef} className="min-h-0 min-w-0 flex-1 overflow-auto" onClickCapture={peek.onClickCapture}>
    <div className={`mx-auto w-full max-w-3xl px-4 py-6 sm:px-6 sm:py-10 ${wide("xl:max-w-6xl")}`} data-guide="home.my-day" data-testid="my-day">
      <div>
        {now ? (
          // The one page header (`pageHeading.test.ts`): the same title
          // size as every other surface, so Home reads as the same product.
          <PageHeader
            title={addressedGreetingLine(now, address.data, session?.user?.name)}
            subtitle={dateLine(now)}
          />
        ) : (
          <div aria-hidden className="mb-3">
            <Skeleton className="h-6 w-56" />
            <Skeleton className="mt-1.5 h-3.5 w-32" />
          </div>
        )}
        <div className="min-h-5">
          {summary === "pending" ? (
            <Skeleton className="h-4 w-72 max-w-full" />
          ) : summary ? (
            <p className="text-sm text-foreground" data-testid="my-day-summary">
              {summary}
            </p>
          ) : null}
        </div>
      </div>

      {/* The brief card (BR-2c-2): a component of its own above both
          layouts, outside the feed flag. It draws nothing on a 404. */}
      {briefOn && !accessLoading && !briefPending ? (
        <BriefCard brief={brief.data} error={brief.error} now={now} className="mt-6 -mb-2" />
      ) : null}

      {/* The AI runs that wait on the member (owner, 2026-11-01: Needs you
          folds into My Day). The feed holds it in its column. The cards
          layout draws it here. It draws nothing when no run waits. */}
      {!accessLoading && !feedOn ? <AiWaitingCard className="mt-6" /> : null}

      {accessLoading || briefPending || (!feedOn && layoutPending) ? (
        <div className="mt-6 grid grid-cols-1 gap-4 xl:grid-cols-3" aria-hidden>
          <Skeleton className="h-64 w-full rounded-xl xl:col-span-2" />
          <Skeleton className="h-40 w-full rounded-xl" />
        </div>
      ) : none ? (
        <div className="mt-6 rounded-xl border border-border bg-card p-6">
          <div className="text-sm font-semibold text-foreground">Nothing is enabled for your account yet</div>
          <p className="mt-2 text-sm text-muted-foreground">
            An organization admin grants access from Organisation → Members &amp; roles.{" "}
            <Link href="/people/access" className="text-primary hover:underline">
              See exactly what you can reach, and why
            </Link>
            .
          </p>
        </div>
      ) : feedOn ? (
        // The feed (MD-1b): one column, about 720 px, in the order of the
        // feed's groups. From `xl`, Today is the right rail, and it stays in
        // view while the member scrolls the column. Below `xl` Today comes
        // FIRST, so "You are caught up" ends the page (visual review). The
        // start classes place each one at `xl`, whatever the order.
        <>
        {showTodayBand(bandOn, feedOn, cards.needs) ? <TodayBand feed={feed} visit={feedVisit} className="mt-6" /> : null}
        <div className={`mt-6 grid grid-cols-1 items-start gap-4 ${wide("xl:grid-cols-3")}`}>
          {cards.today ? (
            <TodayCard
              today={today}
              now={now}
              className={peek.docked ? "order-last" : "xl:sticky xl:top-4 xl:col-start-3 xl:row-start-1"}
            />
          ) : null}
          {cards.needs ? (
            <FeedColumn
              feed={feed}
              now={now}
              ai={aiWaiting}
              onTakeNew={() => setFeedVisit((n) => n + 1)}
              className={wide("xl:col-span-2 xl:col-start-1 xl:row-start-1")} />
          ) : null}
        </div>
        </>
      ) : (
        // One column in the order of the question. From `xl`, Today is a
        // right rail that spans both rows, and the other two stack left.
        <div className={`mt-6 grid grid-cols-1 items-start gap-4 ${wide("xl:grid-cols-3")}`}>
          {/* Each card keeps its place in the grid at `xl`: Today is the right
              rail whatever its order. The order sets the one column, and
              the order of the two cards on the left. */}
          {order.map((key) =>
            key === "needs" ? (
              <NeedsYouCard
                key={key}
                needs={needs}
                now={now}
                first={shell.enabled ? shell.layout.needsFirst : undefined}
                className={wide("xl:col-span-2")}
              />
            ) : key === "today" ? (
              <TodayCard
                key={key}
                today={today}
                now={now}
                className={peek.docked ? "order-last" : "xl:col-start-3 xl:row-span-2 xl:row-start-1"}
              />
            ) : key === "next" ? (
              <NextActionsCard
                key={key}
                enabled
                needs={needs.items ?? []}
                needsSettled={!cards.needs || needs.items !== undefined || !needs.loading}
                className={wide("xl:col-span-2")}
              />
            ) : null,
          )}
        </div>
      )}

      {!accessLoading && sections.length > 0 ? (
        <div className="mt-8 flex justify-center xl:justify-start">
          <Button variant="ghost" size="sm" icon="LayoutGrid" onClick={() => setLauncherOpen(true)} aria-haspopup="dialog">
            All apps
          </Button>
        </div>
      ) : null}
      {/* The store's own Undo for a done (D79), as `/calendar` mounts it.
          The subtask question needs no mount here: `AppShell` hosts it. */}
      {cards.needs || cards.next ? <UndoToast /> : null}
      <AppLauncher
        open={launcherOpen}
        onClose={() => setLauncherOpen(false)}
        sections={sections}
        pathname="/"
      />
    </div>
    </main>
    {peek.panel}
    </div>
    {peek.reader}
    </div>
    </MyDayPeekContext.Provider>
  );
}
