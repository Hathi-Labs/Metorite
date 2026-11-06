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
        <div className="pointer-events-none absolute inset-y-0 left-0 hidden w-14 items-center bg-gradient-to-r from-background via-background/90 to-transparent sm:flex">
          <Button
            variant="ghost"
            size="icon-xs"
            icon="ChevronLeft"
            aria-label="Show more tabs"
            data-feed-tabs-prev=""
            tabIndex={-1}
            onClick={() => page(-1)}
            className="pointer-events-auto"
          />
        </div>
      ) : null}
      {edge.right ? (
        <div className="pointer-events-none absolute inset-y-0 right-0 hidden w-14 items-center justify-end bg-gradient-to-l from-background via-background/90 to-transparent sm:flex">
          <Button
            variant="ghost"
            size="icon-xs"
            icon="ChevronRight"
            aria-label="Show more tabs"
            data-feed-tabs-next=""
            tabIndex={-1}
            onClick={() => page(1)}
            className="pointer-events-auto"
          />
        </div>
      ) : null}
    </div>
  );
}
