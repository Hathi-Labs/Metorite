"use client";

/**
 * CardTabs — the tab row on the top edge of ONE approval card.
 *
 * Owner, 2026-11-03 (D173): "Instead of having, say, a stacked cards
 * animation, we just have one card. When there are multiple cards that need
 * to be approved, it shows up as one single card with tabs." The row sits
 * INSIDE the card, above its own header, with a hairline under it. A count
 * leads ("3 to approve") and stays put. The tabs scroll sideways when they
 * overflow. The shown tab has a 2 px underline in `--primary`. A tab the
 * member has not opened yet has a dot.
 *
 * It draws the row and nothing else: `ConfirmationCard` takes it in its
 * `tabs` slot, and `ConfirmationQueue` holds the state. No swipe, no stacked
 * edges and no swap animation. The rules are `lib/cardTabs.ts`.
 */

import { useLayoutEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import { prefersReducedMotion } from "@/lib/panelMotion";
import { pickLevel, tabKeyTarget, tabsCount, tabsLabel, type TabLevel } from "@/lib/cardTabs";

/** One card's tab. */
export interface CardTab {
  /** A stable key for the card. */
  key: string;
  /** The tab's words: a kind word and a short object (`tabLabel`). */
  label: string;
  /** The full text for the tooltip. Defaults to the label. */
  title?: string;
  /** The card's kind, as an icon name. */
  icon: string;
  /** The member has not opened this card yet. */
  unseen?: boolean;
}

/** The DOM id of tab `i` and of the one panel, from the row's `uid`. */
export const cardTabId = (uid: string, i: number) => `${uid}-tab-${i}`;
export const cardPanelId = (uid: string) => `${uid}-panel`;

export interface CardTabsProps {
  /** A stable id base (`useId`) that ties the tabs to the panel. */
  uid: string;
  /** The index of the card shown. */
  index: number;
  /** Show the card at this index. */
  onGo: (index: number) => void;
  /** One tab for each card, in the queue's order. */
  tabs: readonly CardTab[];
  /** The row's noun, plural: "approvals" by default. */
  noun?: string;
}

const COUNT_CLASS = "flex shrink-0 items-center pr-3 text-xs font-medium tabular-nums text-muted-foreground glass:text-footnote";
const PILL_CLASS = "rounded-full bg-secondary px-1.5 text-xs text-foreground";

/** The classes of one tab at a level. The real row and the probe share them. */
function tabClass(active: boolean, level: number): string {
  return `-mb-px flex max-w-[12rem] ${level >= 2 && active ? "min-w-20 shrink" : "shrink-0"} items-center gap-1.5 border-b-2 px-2.5 py-2 text-sm tech-transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring glass:text-subhead ${
    active ? "border-primary font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"
  }`;
}

/** What a tab holds at a level: its icon, its label unless it is slim, and
 *  its dot. A tab that is not open is slim at level 1 and above. */
function TabFace({ tab, active, level }: { tab: CardTab; active: boolean; level: number }) {
  const slim = level >= 1 && !active;
  return (
    <>
      <Icon name={tab.icon} size={13} className="shrink-0" />
      {!slim && <span className="min-w-0 truncate">{tab.label}</span>}
      {tab.unseen && !active && (
        <>
          <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-primary" />
          {!slim && <span className="sr-only">not yet seen</span>}
        </>
      )}
    </>
  );
}

const LEVELS = [0, 1, 2] as const;

export default function CardTabs({ uid, index, onGo, tabs, noun }: CardTabsProps) {
  const total = tabs.length;
  const outerRef = useRef<HTMLDivElement>(null);
  const probeRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  // A row wider than its room fades at the edge where more tabs wait, so a
  // cut tab reads as "scroll", not as a broken one (the narrow Ask panel).
  const [fade, setFade] = useState<{ left: boolean; right: boolean }>({ left: false, right: false });
  // The tightness (owner, 2026-11-03: tabs "easily overlooked"). It is chosen
  // from two numbers that DO NOT depend on the level: the width each level
  // needs, measured on a hidden copy of the row at that level, and the room,
  // the width of the row's container. Setting the level changes neither, so
  // no feedback can exist. Level 0 shows every tab in full. Level 1 shows a
  // tab that is not open as its icon and dot. Level 2 also shrinks the open
  // tab and shows the count as its number. The fade is the last resort.
  const [needs, setNeeds] = useState<readonly number[]>([]);
  const [room, setRoom] = useState(0);
  const [level, setLevel] = useState<TabLevel>(0);
  const next = needs.length === 3 && room > 0 ? pickLevel(needs, room, level) : level;
  if (next !== level) setLevel(next);
  const measure = () => {
    const outer = outerRef.current;
    const probe = probeRef.current;
    const list = listRef.current;
    if (outer && probe) {
      const cs = getComputedStyle(outer);
      const r = Math.floor(outer.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight));
      setRoom((x) => (x === r ? x : r));
      const n = LEVELS.map((l) => Math.ceil(probe.querySelector<HTMLElement>(`[data-probe="${l}"]`)?.offsetWidth ?? 0));
      setNeeds((x) => (x.length === n.length && x.every((v, i) => v === n[i]) ? x : n));
    }
    if (list) {
      const left = list.scrollLeft > 2;
      const right = list.scrollLeft + list.clientWidth < list.scrollWidth - 2;
      setFade((f) => (f.left === left && f.right === right ? f : { left, right }));
    }
  };
  // The shown tab stays in view. Only the row moves: `scrollIntoView` would
  // scroll the page under the member too. The card mounts again for each
  // card, so this also runs on a change of card.
  useLayoutEffect(() => {
    const list = listRef.current;
    const tab = list?.querySelector<HTMLElement>(`[data-card-tab="${index}"]`);
    if (!list || !tab) return;
    const left = tab.offsetLeft - list.offsetLeft;
    const right = left + tab.offsetWidth;
    const to = left < list.scrollLeft ? left - 8 : right > list.scrollLeft + list.clientWidth ? right - list.clientWidth + 8 : null;
    if (to !== null) list.scrollTo({ left: to, behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }, [index, total]);
  // Every render: read the probe and the room again. Each `set` runs only when
  // a value changed, and neither value depends on the level, so it settles.
  useLayoutEffect(() => {
    measure();
  });
  // The container's width changes with the window and the panel: observe the
  // container, never the tabs, and let go on unmount.
  useLayoutEffect(() => {
    const outer = outerRef.current;
    if (!outer || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(outer);
    return () => ro.disconnect();
  });
  if (total < 2) return null;

  const onKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const to = tabKeyTarget(e.key, index, total);
    if (to === null) return;
    e.preventDefault();
    onGo(to);
    // The focus follows the shown card's tab, as a tab list does. The card
    // mounts again, so the focus waits for the new row.
    requestAnimationFrame(() => document.getElementById(cardTabId(uid, to))?.focus());
  };

  const count = (l: number) =>
    l >= 2 ? (
      // Level 2: the count is its number in a small pill. The words stay
      // for a screen reader.
      <span role="img" aria-label={tabsCount(total)} title={tabsCount(total)} className={PILL_CLASS}>
        {total}
      </span>
    ) : (
      tabsCount(total)
    );

  return (
    <div
      ref={outerRef}
      data-card-tabs={total}
      // The card's own padding is 2.5 (classic) or 4 (glass): the row takes
      // it back, so the hairline runs edge to edge inside the frame.
      className="relative -mx-2.5 -mt-1.5 mb-2 flex min-w-0 items-stretch border-b border-border px-2.5 glass:-mx-4 glass:-mt-4 glass:mb-3 glass:px-4"
    >
      <span data-card-tabs-count="" className={COUNT_CLASS}>
        {count(level)}
      </span>
      <div
        ref={listRef}
        role="tablist"
        aria-label={tabsLabel(total, noun)}
        onKeyDown={onKey}
        onScroll={measure}
        data-fade={fade.left && fade.right ? "both" : fade.left ? "left" : fade.right ? "right" : undefined}
        className="flex min-w-0 flex-1 items-stretch gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden data-[fade=both]:[mask-image:linear-gradient(to_right,transparent,#000_24px,#000_calc(100%-24px),transparent)] data-[fade=left]:[mask-image:linear-gradient(to_right,transparent,#000_24px)] data-[fade=right]:[mask-image:linear-gradient(to_right,#000_calc(100%-24px),transparent)]"
      >
        {tabs.map((t, i) => {
          const active = i === index;
          const slim = level >= 1 && !active;
          return (
            <button
              key={t.key}
              type="button"
              id={cardTabId(uid, i)}
              role="tab"
              aria-selected={active}
              aria-controls={cardPanelId(uid)}
              tabIndex={active ? 0 : -1}
              title={t.title ?? t.label}
              aria-label={slim ? `${t.title ?? t.label}${t.unseen ? " (not yet seen)" : ""}` : undefined}
              data-card-tab={i}
              data-unseen={t.unseen && !active ? "" : undefined}
              onClick={() => onGo(i)}
              className={tabClass(active, level)}
            >
              <TabFace tab={t} active={active} level={level} />
            </button>
          );
        })}
      </div>
      {/* The probe: a hidden copy of the row at each level, only to read the
          width each needs. It is out of the flow, out of the accessibility
          tree and out of the tab order, and the level never changes it. */}
      <div
        ref={probeRef}
        aria-hidden
        data-card-tabs-probe=""
        className="pointer-events-none invisible absolute left-0 top-0 h-0 w-full overflow-hidden"
      >
        {LEVELS.map((l) => (
          <div key={l} data-probe={l} className="flex w-max items-stretch gap-1">
            <span className={COUNT_CLASS}>{count(l)}</span>
            {tabs.map((t, i) => (
              <span key={t.key} className={`${tabClass(i === index, l)} ${l >= 2 && i === index ? "w-20" : ""}`}>
                <TabFace tab={t} active={i === index} level={l} />
              </span>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
