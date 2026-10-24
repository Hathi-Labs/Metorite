"use client";

/**
 * CardStack — one card of a stack, drawn as the top of a deck.
 *
 * Owner, 2026-10-24: several cards that wait on the member must read as a
 * stack at a glance, with "an animated swipe like motion where the card moves
 * a little bit to the side in slight angle and then comes back like a
 * spring … almost like Tinder", and the same motion for every app's stack.
 *
 * - A banner counts the cards, with a dot for each one.
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

import { useEffect, useRef, useState, type ReactNode } from "react";

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

/** A drag that starts on a control is the control's, not a swipe. */
const NO_SWIPE_FROM = "input, textarea, select, button, a, label, [contenteditable='true']";

/** One nudge per page load, so a long session is not shaken on each stack. */
let nudgedThisPage = false;

/** For the tests: let the next stack nudge again. */
export function resetCardStackNudgeForTests(): void {
  nudgedThisPage = false;
}

export interface CardStackProps {
  /** The number of cards in the stack. With one card the stack draws none
   *  of its chrome. */
  total: number;
  /** The index of the card shown. */
  index: number;
  /** Show the card at this index. */
  onGo: (index: number) => void;
  /** A stable key for each card, for the dots. */
  keys: readonly string[];
  /** The banner's noun, plural: "approvals" by default. */
  noun?: string;
  /** The shown card. */
  children: ReactNode;
}

export default function CardStack({ total, index, onGo, keys, noun, children }: CardStackProps) {
  const many = total > 1;
  const [dragX, setDragX] = useState(0);
  const [settling, setSettling] = useState(false);
  const drag = useRef<{ x: number; y: number; id: number; live: boolean } | null>(null);
  const [nudge, setNudge] = useState(false);
  useEffect(() => {
    if (!many || nudgedThisPage) return;
    nudgedThisPage = true;
    setNudge(true);
    const t = setTimeout(() => setNudge(false), 1800);
    return () => clearTimeout(t);
  }, [many]);

  if (!many) return <>{children}</>;
  const depth = stackDepth(total);
  const reveal = revealOf(dragX);

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
    // Release: the card springs home (the transition below), and a full
    // step shows the next or previous card.
    setSettling(true);
    setDragX(0);
    if (step !== 0) onGo(index + step);
  };

  const dragging = dragX !== 0;
  const topStyle: React.CSSProperties | undefined = dragging
    ? { transform: `translateX(${Math.round(dragX * 0.7)}px) rotate(${dragTilt(dragX)}deg)`, transformOrigin: "50% 100%" }
    : settling
      ? { transition: "transform var(--spring-duration) var(--spring-easing)", transform: "none", transformOrigin: "50% 100%" }
      : undefined;

  return (
    <div data-card-stack={total} className="space-y-2">
      <div
        role="status"
        data-card-stack-banner=""
        className="flex min-w-0 items-center gap-2 rounded-lg border border-primary/30 bg-primary/10 px-2.5 py-1.5 text-xs text-primary"
      >
        <Icon name="Layers" size={14} className="shrink-0" />
        <span className="min-w-0 flex-1 font-medium">{stackBanner(total, noun)}</span>
        <span className="flex shrink-0 items-center" aria-label={`Card ${index + 1} of ${total}`}>
          {keys.map((k, i) => (
            <Button
              key={k}
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label={`Show card ${i + 1} of ${total}`}
              aria-current={i === index ? "true" : undefined}
              onClick={() => onGo(i)}
            >
              <span className={`block h-2 w-2 rounded-full ${i === index ? "bg-primary" : "bg-primary/30"}`} />
            </Button>
          ))}
        </span>
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
              className={`absolute inset-0 rounded-lg border border-primary/30 bg-card shadow-sm ${nudge && i === 0 ? "card-stack-lift" : ""}`}
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
          className={`relative rounded-lg bg-background ${nudge ? "card-stack-nudge" : ""}`}
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
