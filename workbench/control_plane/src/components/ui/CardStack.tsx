"use client";

/**
 * CardStack — one card of a stack, drawn as the top of a deck.
 *
 * Owner, 2026-10-24: several cards that wait on the member must read as a
 * stack at a glance, with "an animated swipe like motion where the card moves
 * a little bit to the side in slight angle and then comes back like a
 * spring … almost like Tinder", and the same motion for every app's stack.
 *
 * - A row of tabs names the cards, one capsule each with its kind and a
 *   short name (owner, 2026-11-01: "some sort of tab-based mechanism to
 *   change between the multiple cards"). It is the stack's one switcher: a
 *   click or an arrow key shows a card, and the card holds no pager of its
 *   own while it is stacked.
 * - A change of card moves as a deck does (`swapStackCard`): forward, the
 *   card left is thrown to the side and the next rises from the deck; back
 *   is the mirror.
 * - The cards behind peek out: offset, smaller, a little turned.
 * - On a touch screen a sideways drag tilts the top card and lifts the card
 *   behind; past one step it moves to the next or previous card, and short
 *   of it the card springs back.
 * - The first stack of a page swings once to show the swipe, and springs
 *   back. With reduced motion nothing moves: the banner and the edges say it.
 *
 * It shows ONE card and never answers for the member. The rules are
 * `lib/cardStack.ts`; `ConfirmationQueue` is the first user.
 */

import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import {
  dragTilt,
  edgePose,
  revealOf,
  stackBanner,
  stackDepth,
  swipeStep,
} from "@/lib/cardStack";
import { prefersReducedMotion, swapStackCard } from "@/lib/panelMotion";

/** A drag that starts on a control is the control's, not a swipe. */
const NO_SWIPE_FROM = "input, textarea, select, button, a, label, [contenteditable='true']";

/** One nudge per page load, so a long session is not shaken on each stack. */
let nudgedThisPage = false;

/** For the tests: let the next stack nudge again. */
export function resetCardStackNudgeForTests(): void {
  nudgedThisPage = false;
}

/** One card's tab. */
export interface CardStackTab {
  /** A stable key for the card. */
  key: string;
  /** The tab's words: the card's short name. The tab cuts a long one. */
  label: string;
  /** The card's kind, as an icon name. */
  icon: string;
}

export interface CardStackProps {
  /** The number of cards in the stack. With one card the stack draws none
   *  of its chrome. */
  total: number;
  /** The index of the card shown. */
  index: number;
  /** Show the card at this index. */
  onGo: (index: number) => void;
  /** One tab for each card, in the stack's order. */
  tabs: readonly CardStackTab[];
  /** The tab row's noun, plural: "approvals" by default. */
  noun?: string;
  /** The shown card. */
  children: ReactNode;
}

export default function CardStack({ total, index, onGo, tabs, noun, children }: CardStackProps) {
  const many = total > 1;
  const [dragX, setDragX] = useState(0);
  const [settling, setSettling] = useState(false);
  const drag = useRef<{ x: number; y: number; id: number; live: boolean } | null>(null);
  const topRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const uid = useId();
  const tabId = (i: number) => `${uid}-tab-${i}`;
  const panelId = `${uid}-card`;
  const [nudge, setNudge] = useState(false);
  useEffect(() => {
    if (!many || nudgedThisPage) return;
    nudgedThisPage = true;
    setNudge(true);
    const t = setTimeout(() => setNudge(false), 1800);
    return () => clearTimeout(t);
  }, [many]);
  // A row wider than its room fades at the edge where more tabs wait, so a
  // cut tab reads as "scroll", not as a broken one.
  const [fade, setFade] = useState<{ left: boolean; right: boolean }>({ left: false, right: false });
  const measure = () => {
    const list = listRef.current;
    if (!list) return;
    const left = list.scrollLeft > 2;
    const right = list.scrollLeft + list.clientWidth < list.scrollWidth - 2;
    setFade((f) => (f.left === left && f.right === right ? f : { left, right }));
  };
  useEffect(() => {
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [total]);
  // The shown tab stays in view when the row scrolls. Only the row moves:
  // `scrollIntoView` would scroll the page under the member too.
  useEffect(() => {
    const list = listRef.current;
    const tab = list?.querySelector<HTMLElement>(`[data-card-stack-tab="${index}"]`);
    if (!list || !tab) return;
    const left = tab.offsetLeft - list.offsetLeft;
    const right = left + tab.offsetWidth;
    const to = left < list.scrollLeft ? left - 8 : right > list.scrollLeft + list.clientWidth ? right - list.clientWidth + 8 : null;
    if (to !== null) list.scrollTo({ left: to, behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }, [index]);

  if (!many) return <>{children}</>;
  const depth = stackDepth(total);
  const reveal = revealOf(dragX);

  /** Show card `to`, moving as a deck does. `first` runs inside the change. */
  const go = (to: number, first?: () => void) => {
    if (to === index || to < 0 || to >= total) {
      first?.();
      return;
    }
    swapStackCard(topRef.current, to > index ? "next" : "prev", () => {
      first?.();
      onGo(to);
    });
  };

  const onTabKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const to =
      e.key === "ArrowRight" ? index + 1 : e.key === "ArrowLeft" ? index - 1 : e.key === "Home" ? 0 : e.key === "End" ? total - 1 : null;
    if (to === null) return;
    e.preventDefault();
    const next = Math.min(Math.max(to, 0), total - 1);
    go(next);
    // The focus follows the shown card's tab, as a tab list does.
    requestAnimationFrame(() => document.getElementById(tabId(next))?.focus());
  };

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === "mouse") return;
    if ((e.target as Element).closest?.(NO_SWIPE_FROM)) return;
    drag.current = { x: e.clientX, y: e.clientY, id: e.pointerId, live: false };
    setSettling(false);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (!d.live) {
      // A mostly vertical move is the page scrolling: let it go.
      if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) {
        drag.current = null;
        return;
      }
      if (Math.abs(dx) < 10) return;
      d.live = true;
    }
    setDragX(dx);
  };
  const endDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    drag.current = null;
    if (!d || d.id !== e.pointerId || !d.live) {
      setDragX(0);
      return;
    }
    const step = swipeStep(e.clientX - d.x, index, total);
    if (step !== 0) {
      // A full step: the card leaves from where the finger let it go, and
      // the next one comes in at rest, with no spring.
      go(index + step, () => {
        setSettling(false);
        setDragX(0);
      });
      return;
    }
    // Short of a step: the card springs home (the transition below).
    setSettling(true);
    setDragX(0);
  };

  const dragging = dragX !== 0;
  const topStyle: React.CSSProperties | undefined = dragging
    ? { transform: `translateX(${Math.round(dragX * 0.7)}px) rotate(${dragTilt(dragX)}deg)`, transformOrigin: "50% 100%" }
    : settling
      ? { transition: "transform var(--spring-duration) var(--spring-easing)", transform: "none", transformOrigin: "50% 100%" }
      : undefined;

  return (
    <div data-card-stack={total} className="space-y-2">
      {/* The stack's one switcher: a capsule for each card, with its kind
          and its name. The count leads, so a stack never reads as one card. */}
      <div data-card-stack-banner="" className="flex min-w-0 items-center gap-2">
        <span className="shrink-0 text-xs font-medium tabular-nums text-muted-foreground glass:text-footnote" aria-hidden>
          {total} waiting
        </span>
        <div
          ref={listRef}
          role="tablist"
          aria-label={stackBanner(total, noun)}
          onKeyDown={onTabKey}
          onScroll={measure}
          data-fade={fade.left && fade.right ? "both" : fade.left ? "left" : fade.right ? "right" : undefined}
          // ⚠️ A box that scrolls clips what leaves it, and the focus ring of
          // a tab cut to two arcs (owner, 2026-11-01). The row keeps 4 px of
          // room inside, and gives it back outside, so the ring fits whole.
          className="-m-1 flex min-w-0 flex-1 items-center gap-1 overflow-x-auto p-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden data-[fade=both]:[mask-image:linear-gradient(to_right,transparent,#000_24px,#000_calc(100%-24px),transparent)] data-[fade=left]:[mask-image:linear-gradient(to_right,transparent,#000_24px)] data-[fade=right]:[mask-image:linear-gradient(to_right,#000_calc(100%-24px),transparent)]"
        >
          {tabs.map((t, i) => (
            <Button
              key={t.key}
              id={tabId(i)}
              role="tab"
              aria-selected={i === index}
              aria-controls={panelId}
              tabIndex={i === index ? 0 : -1}
              // The shown card's tab is the one filled. The others are quiet
              // words, as a segmented control draws them.
              variant="ghost"
              size="sm"
              selected={i === index}
              title={t.label}
              data-card-stack-tab={i}
              onClick={() => go(i)}
              className={`max-w-[15rem] shrink-0 ${i === index ? "" : "text-muted-foreground"}`}
            >
              <Icon name={t.icon} size={13} className="shrink-0" />
              <span className="min-w-0 truncate">{t.label}</span>
            </Button>
          ))}
        </div>
      </div>
      <div
        className="relative isolate"
        style={{ paddingBottom: depth * 10, touchAction: "pan-y" }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        {/* The cards behind: offset, smaller and turned, so the stack reads
            as a deck. The first one rises as the top card is dragged off. */}
        {Array.from({ length: depth }, (_, i) => {
          const pose = edgePose(i);
          const up = i === 0 ? reveal : 0;
          const y = pose.y * (1 - up);
          const scale = pose.scale + (1 - pose.scale) * up;
          const rotate = pose.rotate * (1 - up);
          return (
            <div
              key={i}
              aria-hidden
              data-card-stack-edge=""
              className={`absolute inset-0 rounded-lg border border-primary/30 bg-card shadow-sm glass:rounded-[16px] glass:border-transparent glass:glass-raised ${nudge && i === 0 ? "card-stack-lift" : ""}`}
              style={{
                transform: `translateY(${y}px) scale(${scale}) rotate(${rotate}deg)`,
                transition: dragging ? undefined : "transform var(--spring-duration) var(--spring-easing)",
                opacity: 1 - i * 0.3,
                zIndex: -1 - i,
              }}
            />
          );
        })}
        <div
          ref={topRef}
          id={panelId}
          role="tabpanel"
          aria-labelledby={tabId(index)}
          data-card-stack-top=""
          className={`relative rounded-lg bg-background glass:rounded-[16px] ${nudge ? "card-stack-nudge" : ""}`}
          data-nudge={nudge ? "" : undefined}
          style={topStyle}
          onTransitionEnd={() => setSettling(false)}
        >
          {children}
        </div>
      </div>
    </div>
  );
}
