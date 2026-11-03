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
import { nextCompact, tabKeyTarget, tabsCount, tabsLabel } from "@/lib/cardTabs";

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

export default function CardTabs({ uid, index, onGo, tabs, noun }: CardTabsProps) {
  const total = tabs.length;
  const listRef = useRef<HTMLDivElement>(null);
  // A row wider than its room fades at the edge where more tabs wait, so a
  // cut tab reads as "scroll", not as a broken one (the narrow Ask panel).
  const [fade, setFade] = useState<{ left: boolean; right: boolean }>({ left: false, right: false });
  // Compact (owner, 2026-11-03: tabs "easily overlooked"): when the full row
  // would overflow, each tab that is not open shows its icon and dot only.
  // The fade is the last resort, for a row that overflows even as icons.
  // A change of the tabs' words or dots changes the full width, so the
  // state resets and the full row is measured again before the browser paints.
  const signature = tabs.map((t) => `${t.label}||${t.unseen ? 1 : 0}`).join("||");
  const [fit, setFit] = useState({ compact: false, full: 0, sig: signature });
  if (fit.sig !== signature) setFit({ compact: false, full: 0, sig: signature });
  const compact = fit.compact && fit.sig === signature;
  const measure = () => {
    const list = listRef.current;
    if (!list) return;
    const c = nextCompact(fit, list.scrollWidth, list.clientWidth);
    if (c.compact !== fit.compact || c.full !== fit.full) setFit({ ...c, sig: fit.sig });
    const left = list.scrollLeft > 2;
    const right = list.scrollLeft + list.clientWidth < list.scrollWidth - 2;
    setFade((f) => (f.left === left && f.right === right ? f : { left, right }));
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
  // Every render: measure again. `nextCompact` holds a margin, so the state
  // settles, and `setFit` runs only when a value changed.
  useLayoutEffect(() => {
    measure();
  });
  // The row's own width changes with the window and the panel: observe it,
  // and let go on unmount.
  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(list);
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
        onScroll={measure}
        data-fade={fade.left && fade.right ? "both" : fade.left ? "left" : fade.right ? "right" : undefined}
        className="flex min-w-0 flex-1 items-stretch gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden data-[fade=both]:[mask-image:linear-gradient(to_right,transparent,#000_24px,#000_calc(100%-24px),transparent)] data-[fade=left]:[mask-image:linear-gradient(to_right,transparent,#000_24px)] data-[fade=right]:[mask-image:linear-gradient(to_right,#000_calc(100%-24px),transparent)]"
      >
        {tabs.map((t, i) => {
          const active = i === index;
          // A tab that is not open shows its icon and dot only when compact.
          const slim = compact && !active;
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
              className={`-mb-px flex max-w-[12rem] shrink-0 items-center gap-1.5 border-b-2 px-2.5 py-2 text-sm tech-transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring glass:text-subhead ${
                active
                  ? "border-primary font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon name={t.icon} size={13} className="shrink-0" />
              {!slim && <span className="min-w-0 truncate">{t.label}</span>}
              {t.unseen && !active && (
                <>
                  <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-primary" />
                  {!slim && <span className="sr-only">not yet seen</span>}
                </>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
