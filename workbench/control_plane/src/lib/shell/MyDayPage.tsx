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
 */
import Link from "next/link";
import { useSession } from "next-auth/react";
import { useState, useSyncExternalStore } from "react";

import TodayCard, { useTodayItems } from "@/app/calendar/components/TodayCard";
import { UndoToast } from "@/app/tasks/components/UndoToast";
import NextActionsCard from "@/app/tasks/components/NextActionsCard";
import { useAccess } from "@/components/AccessProvider";
import { AppTopBar } from "@/components/AppTopBar";
import PageHeader from "@/components/PageHeader";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { visibleSections } from "@/lib/nav";

import AppLauncher from "./AppLauncher";
import { feedFailedLines, feedNeedsCount, handledClaim } from "./feed";
import { FeedColumn, useFeed } from "./FeedCard";
import NeedsYouCard, { useNeedsYou } from "./NeedsYouCard";
import { cardOrderFor, cardsFor, dateLine, failedLines, greetingLine, seesApprovals, summaryLine } from "./myDay";
import { myDayFeedOn, myDayFeedOnServer } from "./myDayFlag";
import { BUILT_CARDS } from "./presets";
import { homePane } from "./shellNav";
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
  // This page renders only with the flag on, so its pane is My Day.
  const home = homePane(true);

  const cards = cardsFor(accessLoading ? [] : access.features, access.is_admin);
  const needs = useNeedsYou(!accessLoading && cards.needs, seesApprovals(access.features, access.is_admin));
  const today = useTodayItems(!accessLoading && cards.today, now);
  const feedOn = useSyncExternalStore(noSubscribe, myDayFeedOn, myDayFeedOnServer);
  // The feed's sources are the needs sources and Calendar, and Calendar
  // needs what Today needs, so a member with no needs card gets no feed.
  const feed = useFeed(feedOn && !accessLoading && cards.needs);
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
              feedNeedsCount(feedAnswer, feed.marked, feed.hidden),
              todayCount,
              feedFailedLines(feedAnswer.sources, feedAnswer.source_apps).length > 0,
              // A stale answer under a failed refresh keeps its count, and
              // loses its claim: the claim may be yesterday's (fix round 1).
              handledClaim(feedAnswer) && !feed.error,
            )
          : summaryLine(needs.count ?? 0, todayCount, failedLines(needs.sources).length > 0);

  return (
    <div className="flex h-full min-h-0 flex-col">
    {/* The compact bar on a phone (AppTopBar decides), so the page keeps
        its one h1 there too. */}
    <AppTopBar title={home.label} icon={home.icon} />
    <div className="min-h-0 flex-1 overflow-auto">
    <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6 sm:py-10 xl:max-w-6xl" data-testid="my-day">
      <div>
        {now ? (
          // The one page header (`pageHeading.test.ts`): the same title
          // size as every other surface, so Home reads as the same product.
          <PageHeader title={greetingLine(now, session?.user?.name)} subtitle={dateLine(now)} />
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

      {accessLoading || (!feedOn && layoutPending) ? (
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
        <div className="mt-6 grid grid-cols-1 items-start gap-4 xl:grid-cols-3">
          {cards.today ? (
            <TodayCard today={today} now={now} className="xl:sticky xl:top-4 xl:col-start-3 xl:row-start-1" />
          ) : null}
          {cards.needs ? (
            <FeedColumn feed={feed} now={now} className="xl:col-span-2 xl:col-start-1 xl:row-start-1" />
          ) : null}
        </div>
      ) : (
        // One column in the order of the question. From `xl`, Today is a
        // right rail that spans both rows, and the other two stack left.
        <div className="mt-6 grid grid-cols-1 items-start gap-4 xl:grid-cols-3">
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
                className="xl:col-span-2"
              />
            ) : key === "today" ? (
              <TodayCard key={key} today={today} now={now} className="xl:col-start-3 xl:row-span-2 xl:row-start-1" />
            ) : key === "next" ? (
              <NextActionsCard
                key={key}
                enabled
                needs={needs.items ?? []}
                needsSettled={!cards.needs || needs.items !== undefined || !needs.loading}
                className="xl:col-span-2"
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
    </div>
    </div>
  );
}
