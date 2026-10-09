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
 */
import Link from "next/link";
import { useSession } from "next-auth/react";
import { useState, useSyncExternalStore } from "react";

import TodayCard, { useTodayItems } from "@/app/calendar/components/TodayCard";
import NextActionsCard from "@/app/tasks/components/NextActionsCard";
import { useAccess } from "@/components/AccessProvider";
import PageHeader from "@/components/PageHeader";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { visibleSections } from "@/lib/nav";

import AppLauncher from "./AppLauncher";
import NeedsYouCard, { useNeedsYou } from "./NeedsYouCard";
import { cardsFor, dateLine, greetingLine, summaryLine } from "./myDay";

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

  const cards = cardsFor(accessLoading ? [] : access.features);
  const needs = useNeedsYou(!accessLoading && cards.needs);
  const today = useTodayItems(!accessLoading && cards.today, now);
  const sections = visibleSections(accessLoading ? null : access.features, access.is_admin);

  // The summary waits for both reads. A feed that failed says nothing here,
  // because "Nothing needs you" would be a claim the page cannot back. A
  // calendar that failed drops its half of the sentence.
  const needsPending = cards.needs && needs.count === undefined && needs.loading;
  const needsFailed = cards.needs && needs.count === undefined && !needs.loading;
  const todayPending = cards.today && today.data === undefined && today.loading;
  const todayCount = cards.today && today.data ? today.data.length : null;
  const none = !accessLoading && !cards.needs && !cards.today && !cards.next;
  const summary =
    accessLoading || needsPending || todayPending
      ? "pending"
      : needsFailed || none
        ? null
        : summaryLine(needs.count ?? 0, todayCount);

  return (
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

      {accessLoading ? (
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
      ) : (
        // One column in the order of the question. From `xl`, Today is a
        // right rail that spans both rows, and the other two stack left.
        <div className="mt-6 grid grid-cols-1 items-start gap-4 xl:grid-cols-3">
          {cards.needs ? <NeedsYouCard needs={needs} className="xl:col-span-2" /> : null}
          {cards.today ? (
            <TodayCard today={today} className="xl:col-start-3 xl:row-span-2 xl:row-start-1" />
          ) : null}
          {cards.next ? (
            <NextActionsCard
              enabled
              needs={needs.items ?? []}
              needsSettled={!cards.needs || needs.items !== undefined || !needs.loading}
              className="xl:col-span-2"
            />
          ) : null}
        </div>
      )}

      {!accessLoading && sections.length > 0 ? (
        <div className="mt-8 flex justify-center xl:justify-start">
          <Button variant="ghost" size="sm" icon="LayoutGrid" onClick={() => setLauncherOpen(true)} aria-haspopup="dialog">
            All apps
          </Button>
        </div>
      ) : null}
      <AppLauncher
        open={launcherOpen}
        onClose={() => setLauncherOpen(false)}
        sections={sections}
        pathname="/"
      />
    </div>
  );
}
