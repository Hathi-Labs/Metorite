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

import { useLayoutEffect, useRef } from "react";

import Icon from "@/components/Icon";
import { prefersReducedMotion } from "@/lib/panelMotion";
import { tabKeyTarget, tabsCount, tabsLabel } from "@/lib/cardTabs";

/** One card's tab. */
export interface CardTab {
  /** A stable key for the card. */
  key: string;
  /** The tab's words: the card's short name. The tab cuts a long one. */
  label: string;
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

export default function CardTabs({ uid, index, onGo, tabs, noun }: CardTabsProps) {
  const total = tabs.length;
  const listRef = useRef<HTMLDivElement>(null);
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
  }, [index]);
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

  return (
    <div
      data-card-tabs={total}
      // The card's own padding is 2.5 (classic) or 4 (glass): the row takes
      // it back, so the hairline runs edge to edge inside the frame.
      className="-mx-2.5 -mt-1.5 mb-2 flex min-w-0 items-stretch border-b border-border px-2.5 glass:-mx-4 glass:-mt-4 glass:mb-3 glass:px-4"
    >
      <span
        data-card-tabs-count=""
        className="flex shrink-0 items-center pr-3 text-xs font-medium tabular-nums text-muted-foreground glass:text-footnote"
      >
        {tabsCount(total)}
      </span>
      <div
        ref={listRef}
        role="tablist"
        aria-label={tabsLabel(total, noun)}
        onKeyDown={onKey}
        className="flex min-w-0 flex-1 items-stretch gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {tabs.map((t, i) => {
          const active = i === index;
          return (
            <button
              key={t.key}
              type="button"
              id={cardTabId(uid, i)}
              role="tab"
              aria-selected={active}
              aria-controls={cardPanelId(uid)}
              tabIndex={active ? 0 : -1}
              title={t.label}
              data-card-tab={i}
              data-unseen={t.unseen && !active ? "" : undefined}
              onClick={() => onGo(i)}
              className={`-mb-px flex max-w-[15rem] shrink-0 items-center gap-1.5 border-b-2 px-2.5 py-2 text-sm tech-transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring glass:text-subhead ${
                active
                  ? "border-primary font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon name={t.icon} size={13} className="shrink-0" />
              <span className="min-w-0 truncate">{t.label}</span>
              {t.unseen && !active && (
                <>
                  <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-primary" />
                  <span className="sr-only">not yet seen</span>
                </>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
