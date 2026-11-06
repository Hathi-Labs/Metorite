"use client";

/**
 * The tab bar of My Day's "Needs you" card (CC-11). It is the shared `Tabs`
 * underline bar, dense, plus the cue of a bar that scrolls. From `sm`, a bar
 * that overflows draws a soft fade on each cut edge and a chevron Button that
 * scrolls one page. A chevron hides when its edge is reached. On a phone the
 * bar keeps the plain swipe and draws no chevron.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import Tabs, { type TabDef } from "@/components/Tabs";
import Button from "@/components/ui/Button";

/**
 * The width of one cap and its fade, in px: `w-9` and `w-5`. A tab that the
 * bar scrolls into view keeps this much clear of each cut edge.
 */
const CAP_PX = 36 + 20;

export default function NeedsTabBar({
  tabs,
  active,
  onChange,
  panelId,
}: {
  tabs: TabDef[];
  active: string;
  onChange: (id: string) => void;
  panelId: string;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [edge, setEdge] = useState({ left: false, right: false });
  const bar = useCallback(() => wrap.current?.querySelector<HTMLElement>('[role="tablist"]') ?? null, []);
  const measure = useCallback(() => {
    const el = bar();
    if (!el) return;
    const left = el.scrollLeft > 1;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
    setEdge((e) => (e.left === left && e.right === right ? e : { left, right }));
  }, [bar]);

  useEffect(() => {
    const el = bar();
    if (!el) return;
    el.addEventListener("scroll", measure, { passive: true });
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", measure);
      ro.disconnect();
    };
  }, [bar, measure, tabs.length]);

  // The selected tab scrolls fully clear of both caps, whether a click, an
  // arrow key, Home or End chose it. Below `sm` no cap draws, so no margin.
  useEffect(() => {
    const el = bar();
    const tab = el?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]');
    if (!el || !tab) return;
    const pad = window.matchMedia("(min-width: 640px)").matches ? CAP_PX : 0;
    const left = tab.offsetLeft - el.offsetLeft;
    const right = left + tab.offsetWidth;
    let to = el.scrollLeft;
    if (left < to + pad) to = Math.max(0, left - pad);
    else if (right > to + el.clientWidth - pad) to = Math.min(el.scrollWidth - el.clientWidth, right - el.clientWidth + pad);
    if (Math.abs(to - el.scrollLeft) > 1) el.scrollTo({ left: to });
  }, [active, bar]);

  const page = (dir: 1 | -1) => {
    const el = bar();
    el?.scrollBy({ left: dir * el.clientWidth * 0.8, behavior: "smooth" });
  };

  return (
    <div ref={wrap} data-feed-filter="" className="relative -mx-4 border-b border-border px-4 sm:mx-0 sm:px-0">
      <Tabs
        variant="underline"
        chrome="inline"
        dense
        panelId={panelId}
        activeTab={active}
        onTabChange={onChange}
        tabs={tabs}
      />
      {edge.left ? (
        // An opaque cap holds the chevron, and the fade runs beyond it, so no
        // cut-off tab fragment shows beside the chevron.
        <div className="pointer-events-none absolute inset-y-0 left-0 hidden items-stretch sm:flex">
          <div className="pointer-events-auto flex w-9 items-center justify-center bg-card">
            <Button
              variant="ghost"
              size="icon-xs"
              icon="ChevronLeft"
              aria-label="Show more tabs"
              data-feed-tabs-prev=""
              tabIndex={-1}
              onClick={() => page(-1)}
            />
          </div>
          <div className="w-5 bg-gradient-to-r from-card from-30% to-transparent" />
        </div>
      ) : null}
      {edge.right ? (
        <div className="pointer-events-none absolute inset-y-0 right-0 hidden items-stretch sm:flex">
          <div className="w-5 bg-gradient-to-l from-card from-30% to-transparent" />
          <div className="pointer-events-auto flex w-9 items-center justify-center bg-card">
            <Button
              variant="ghost"
              size="icon-xs"
              icon="ChevronRight"
              aria-label="Show more tabs"
              data-feed-tabs-next=""
              tabIndex={-1}
              onClick={() => page(1)}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}
