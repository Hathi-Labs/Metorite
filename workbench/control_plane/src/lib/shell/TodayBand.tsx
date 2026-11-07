"use client";

/**
 * The Today band on My Day (`package_host.md` §9.3, PKG-18a). Behind
 * `NEXT_PUBLIC_TODAY_BAND` (`todayBandOn`), default OFF.
 *
 * Up to five items, each a link to its entity with the reason beneath it.
 * The items are the top cards of the feed that the column reads (`todayBand`).
 * The band takes that feed as a prop, so it sends no request of its own.
 *
 * It draws nothing while the feed loads, after a failure, or with no item,
 * as `BriefCard` draws nothing when the brief is absent. It writes nothing.
 */
import Link from "next/link";

import Icon from "@/components/Icon";

import { HomeCard } from "./HomeCard";
import type { Feed } from "./FeedCard";
import { todayBand } from "./todayBand";

export default function TodayBand({
  feed,
  className = "",
}: {
  feed: Pick<Feed, "answer" | "marked" | "hidden">;
  className?: string;
}) {
  const items = todayBand(feed.answer, feed.marked, feed.hidden);
  if (items.length === 0) return null;
  return (
    <HomeCard title="Today" icon="Sparkles" testId="today-band" className={className}>
      <ul className="flex flex-col divide-y divide-border/60 px-4 pb-3">
        {items.map((item) => (
          <li key={item.signalId} className="py-2" data-today-band-item="">
            <div className="text-sm text-foreground">
              {item.href ? (
                <Link href={item.href} className="cc-link rounded-sm hover:underline underline-offset-2">
                  {item.title}
                </Link>
              ) : (
                item.title
              )}
            </div>
            <p className="mt-0.5 flex items-start gap-1.5 text-xs text-muted-foreground" data-today-band-why="">
              <Icon name="Lightbulb" size={13} className="mt-px shrink-0" />
              <span>{item.reason}</span>
            </p>
          </li>
        ))}
      </ul>
    </HomeCard>
  );
}
