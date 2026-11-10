"use client";

/**
 * The brief card of My Day (`core_brief.md` §5 item 1, WS-BRIEF BR-2c-2).
 *
 * A standalone component. It is NOT a kind of the My Day feed, and
 * `feed_rank` and the registry know nothing of it. It reads the stored run
 * of the member through `/api/brief/today?kind=brief`, and it draws the
 * lead as the title and one row for each claim, in the order of `position`.
 * A claim is an ordinary in-app link to its `href`.
 *
 * The card draws NOTHING in three cases: the route answers 404 (the brief is
 * off, not ready, or the member opted out), the read fails, or the brief
 * has no lead. A failed read logs once per page load and shows no error.
 *
 * Before the slot of today, the route serves the run of the day before. The
 * card then says so above the title, with the date (`staleLabel`).
 *
 * Fence: `briefCard.test.ts`.
 */
import Link from "next/link";
import { useEffect } from "react";

import Icon from "@/components/Icon";
import { isInAppPath } from "@/lib/inAppLink";

import { type BriefToday, staleLabel } from "./briefCard";
import { feedAppIcon } from "./feed";
import { HomeCard } from "./HomeCard";

let logged = false;

/**
 * `brief` is the page's read (`MyDayPage` calls `useCachedResource` itself, so
 * it can hold the grid back while the brief loads, and the grid does not jump
 * when the card arrives). `null` and `undefined` draw nothing.
 */
export default function BriefCard({
  brief,
  error,
  now,
  className = "",
}: {
  brief: BriefToday | null | undefined;
  /** The read's error, if any. The card draws nothing and logs it once. */
  error?: string | null;
  /** The page's clock, by the minute. Null before the client clock exists. */
  now: Date | null;
  className?: string;
}) {
  useEffect(() => {
    if (error && !logged) {
      logged = true;
      console.warn(`[brief-card] ${error}`);
    }
  }, [error]);

  const data = brief;
  if (!data) return null;

  // The card never renders on the server (the read is client-only), so a
  // clock read here cannot mismatch. It stops the label arriving a tick late
  // on a cache hit, when the page's own clock is still null.
  const stale = staleLabel(data.local_date, now ?? (typeof window === "undefined" ? null : new Date()));
  return (
    <HomeCard
      title={data.lead}
      titleClassName="break-words min-w-0"
      icon="Sparkles"
      eyebrow={stale ?? undefined}
      testId="brief-card"
      className={className}
    >
      {data.claims.length > 0 ? (
        <ul className="flex flex-col" data-testid="brief-claims">
          {data.claims.map((claim) => (
            <li key={`${claim.position}-${claim.text}`}>
              {claim.href && isInAppPath(claim.href) ? (
                <Link
                  href={claim.href}
                  data-claim-link=""
                  className="cc-link flex min-h-11 items-center gap-3 rounded-lg px-2 py-1.5 text-sm text-foreground hover:bg-secondary/60 tech-transition"
                >
                  {/* The row's door, as the Needs you rows draw it: the
                      source app's own icon first. The chevron last follows
                      `RunSignals`, not Needs you (its rows have none). */}
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-secondary text-muted-foreground">
                    <Icon name={feedAppIcon(claim.source_app)} size={14} />
                  </span>
                  <span className="min-w-0 flex-1 break-words">{claim.text}</span>
                  <Icon name="ChevronRight" size={14} className="shrink-0 text-muted-foreground" />
                </Link>
              ) : (
                <span className="flex min-h-11 min-w-0 items-center px-2 py-1.5 text-sm text-foreground break-words">{claim.text}</span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </HomeCard>
  );
}
