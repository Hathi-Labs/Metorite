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

import { isInAppPath } from "@/lib/inAppLink";
import { useCachedResource } from "@/lib/useCachedResource";

import { BRIEF_KEY, fetchBrief, staleLabel } from "./briefCard";
import { HomeCard } from "./HomeCard";

let logged = false;

export default function BriefCard({
  now,
  className = "",
}: {
  /** The page's clock, by the minute. A render reads no clock itself. */
  now: Date | null;
  className?: string;
}) {
  const brief = useCachedResource(BRIEF_KEY, () => fetchBrief());
  const failed = brief.error;
  useEffect(() => {
    if (failed && !logged) {
      logged = true;
      console.warn(`[brief-card] ${failed}`);
    }
  }, [failed]);

  const data = brief.data;
  if (!data) return null;

  const stale = staleLabel(data.local_date, now);
  return (
    <HomeCard
      title={data.lead}
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
                  className="cc-link flex min-h-11 items-center rounded-lg px-2 py-1.5 text-sm text-foreground hover:bg-secondary/60 tech-transition"
                >
                  {claim.text}
                </Link>
              ) : (
                <span className="flex min-h-11 items-center px-2 py-1.5 text-sm text-foreground">{claim.text}</span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </HomeCard>
  );
}
