"use client";

/**
 * The Do first band on My Day (`package_host.md` §9.3, PKG-18a). Behind
 * `NEXT_PUBLIC_TODAY_BAND` (`todayBandOn`), default OFF.
 *
 * Up to five items, each a link to its entity with the reason beneath it.
 * The items are the top cards of the feed that the column reads (`todayBand`).
 * The band takes that feed as a prop, so it sends no request of its own.
 *
 * The items hold for the visit (`bandStep`, PKG-Q15). A poll adds no item,
 * so the column under the band does not move. A done, snoozed or hidden card
 * leaves at once. Only a fresh answer starts a hold, so a cached answer of
 * an earlier visit is not frozen. The `visit` prop is the column's visit
 * number (`FeedColumn` `onTakeNew`). A new number starts a new hold. A
 * reload starts a new hold too.
 *
 * The title and the reason wrap as the column's title does (`FeedCard.tsx`):
 * two lines on a phone, one truncated line from `sm`. So the same text has
 * the same shape in the band and in the column.
 *
 * It draws nothing while the feed loads, after a failure, or with no item,
 * as `BriefCard` draws nothing when the brief is absent. It writes nothing.
 */
import Link from "next/link";
import { useState } from "react";

import Icon from "@/components/Icon";

import { HomeCard } from "./HomeCard";
import type { Feed } from "./FeedCard";
import { BAND_HEADING, BAND_WRAP, type BandHold, bandStep } from "./todayBand";

export default function TodayBand({
  feed,
  visit = 0,
  className = "",
}: {
  feed: Pick<Feed, "answer" | "marked" | "hidden" | "refreshing">;
  /** The column's visit number. It goes up when the member takes in new cards. */
  visit?: number;
  className?: string;
}) {
  const [hold, setHold] = useState<BandHold | null>(null);
  const step = bandStep(hold, feed.answer, feed.marked, feed.hidden, { fresh: !feed.refreshing, visit });
  // `bandStep` gives back the same hold while it lasts, so this sets state
  // only when a hold starts or ends: state from the render, as the column
  // starts its visit (`FeedColumn`).
  if (step.hold !== hold) setHold(step.hold);
  const { items } = step;
  if (items.length === 0) return null;
  return (
    <HomeCard title={BAND_HEADING} icon="Sparkles" testId="today-band" className={className}>
      <ul className="flex flex-col divide-y divide-border/60 px-4 pb-3">
        {items.map((item) => (
          <li key={item.signalId} className="min-w-0 py-2" data-today-band-item="">
            <div className="flex min-w-0 text-sm text-foreground">
              {item.href ? (
                <Link
                  href={item.href}
                  className={`cc-link rounded-sm hover:underline underline-offset-2 ${BAND_WRAP}`}
                  data-today-band-title=""
                >
                  {item.title}
                </Link>
              ) : (
                <span className={BAND_WRAP} data-today-band-title="">
                  {item.title}
                </span>
              )}
            </div>
            <p className="mt-0.5 flex min-w-0 items-start gap-1.5 text-xs text-muted-foreground" data-today-band-why="">
              <Icon name="Lightbulb" size={13} className="mt-px shrink-0" />
              <span className={BAND_WRAP}>{item.reason}</span>
            </p>
          </li>
        ))}
      </ul>
    </HomeCard>
  );
}
