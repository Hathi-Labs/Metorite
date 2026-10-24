"use client";

/**
 * ConfirmationQueue — every pending confirmation card, one at a time.
 *
 * A turn can park many gated tools at once (2026-10-06: ten). One card shows,
 * with "1 of N" and arrows to reach the rest. One at a time, and not a
 * stack, because the Projects rail is narrow: ten stacked cards push the
 * turn that asked for them out of view. Answering a card removes it, and the
 * next card takes its place.
 *
 * No "Approve all". Each card is one consent, and a card the member did not
 * page to is a card nobody read (the Projects writes rule, MAX_BATCH). A
 * batch tool with ONE card is the answer to many cards of one kind.
 *
 * The stack (owner, 2026-10-24): with two cards or more, a banner counts
 * them, the edges of the cards behind peek out under the shown one, a
 * sideways swipe on a touch screen moves between them, and the first stack
 * of a page nudges once to show the swipe. The member could miss "1 of 2" in
 * the corner. The nudge stops for a member who asked for less motion.
 *
 * The queue itself is `lib/confirmationQueue.ts`.
 */

import { useEffect, useReducer, useRef, useState } from "react";

import ConfirmationCard from "@/components/ConfirmationCard";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import {
  NO_TICKS,
  rowTicksReducer,
  rowsView,
  stackBanner,
  stackDepth,
  swipeStep,
  type PendingConfirmation,
  type RowsApproval,
} from "@/lib/confirmationQueue";
import type { ReplyApproval } from "@/lib/replyCard";

/** A plain answer, an Approve that names the ticked rows of a card with
 *  rows (WS-46 P13 one-card), or the Send or Save of a reply card (EM-CR).
 *  Each goes in ONE respond-input. */
export type ConfirmationAnswer = "APPROVE" | "REJECT" | RowsApproval | ReplyApproval;

interface ConfirmationQueueProps {
  cards: PendingConfirmation[];
  onAnswer: (card: PendingConfirmation, answer: ConfirmationAnswer) => void;
  /** The card to show first. Tests and the visual rig set it. */
  initialIndex?: number;
}

/** The index the queue shows: the one asked for, held inside the list. */
export function shownIndex(index: number, total: number): number {
  if (total <= 0) return 0;
  return Math.min(Math.max(index, 0), total - 1);
}

export interface Shown {
  /** The card the member looks at. Null before the first render pins one. */
  key: string | null;
  /** Where it was. Used only when that card has left the queue. */
  index: number;
}

/**
 * The card to show. The member's card stays under their eye while the queue
 * moves around it: a restored card goes first, and an answer from another
 * tab removes a card above it. A numeric index alone would slide a different
 * card under the cursor, and the next Approve would sign it. When the card
 * itself leaves (it was answered), the card now at its place takes over.
 */
export function pickShown(cards: PendingConfirmation[], shown: Shown): number {
  const found = shown.key === null ? -1 : cards.findIndex((c) => c.key === shown.key);
  return found >= 0 ? found : shownIndex(shown.index, cards.length);
}

/** One nudge per page load: the first stack shows the swipe, and later
 *  stacks do not repeat it. */
let nudgedThisPage = false;

/** A drag that starts on a control is the control's, not a swipe. */
const NO_SWIPE_FROM = "input, textarea, select, button, a, label, [contenteditable='true']";

export default function ConfirmationQueue({ cards, onAnswer, initialIndex = 0 }: ConfirmationQueueProps) {
  const [shown, setShown] = useState<Shown>({ key: null, index: initialIndex });
  // The sideways drag of a swipe, in px, while a finger is down.
  const [dragX, setDragX] = useState(0);
  const drag = useRef<{ x: number; y: number; id: number; live: boolean } | null>(null);
  const many = cards.length > 1;
  const [nudge, setNudge] = useState(false);
  useEffect(() => {
    if (!many || nudgedThisPage) return;
    nudgedThisPage = true;
    setNudge(true);
    const t = setTimeout(() => setNudge(false), 1600);
    return () => clearTimeout(t);
  }, [many]);
  // The member's ticks of every card with rows, by key. The queue holds
  // them, so a page away and back keeps them (PR #691 review). The logic is
  // `rowTicksReducer` and `rowsView`, and this component holds none.
  const [ticks, tick] = useReducer(rowTicksReducer, NO_TICKS);
  if (cards.length === 0) return null;
  const at = pickShown(cards, shown);
  const card = cards[at];
  if (shown.key !== card.key || shown.index !== at) {
    // Pin the card on screen (React's adjust-state-during-render pattern).
    setShown({ key: card.key, index: at });
  }
  const go = (to: number) => setShown({ key: cards[to].key, index: to });
  const view = rowsView(card, ticks);
  const depth = stackDepth(cards.length);

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!many || e.pointerType === "mouse") return;
    if ((e.target as Element).closest?.(NO_SWIPE_FROM)) return;
    drag.current = { x: e.clientX, y: e.clientY, id: e.pointerId, live: false };
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
    const step = swipeStep(e.clientX - d.x, at, cards.length);
    setDragX(0);
    if (step !== 0) go(at + step);
  };

  const shownCard = (
    <ConfirmationCard
      // A new key per card: a long body scrolled on one card does not stay
      // scrolled on the next, and the buttons arm again (ConfirmationCard).
      key={card.key}
      id={`confirm:${card.key}`}
      title={card.title}
      detail={card.detail}
      context={card.context}
      fenced={card.fenced}
      rows={view.rows}
      onToggle={(id, on) => tick({ type: "toggle", card, id, on })}
      position={{
        index: at,
        total: cards.length,
        onPrev: () => go(Math.max(0, at - 1)),
        onNext: () => go(Math.min(cards.length - 1, at + 1)),
      }}
      onApprove={() => {
        // One respond-input: the Approve and the ticked ids together. The
        // answered card carries the ticks, so a restore shows them again.
        if (view.approved) onAnswer(view.approved.card, view.approved.answer);
      }}
      onReject={() => onAnswer(card, "REJECT")}
      // EM-CR: a reply card answers with the member's choice and body.
      {...(card.reply !== undefined ? { reply: card.reply, onReply: (a: ReplyApproval) => onAnswer(card, a) } : {})}
    />
  );
  if (!many) return shownCard;
  return (
    <div data-approval-stack={cards.length} className="space-y-2">
      <div
        role="status"
        data-approval-stack-banner=""
        className="flex min-w-0 items-center gap-2 rounded-lg border border-primary/30 bg-primary/10 px-2.5 py-1.5 text-xs text-primary"
      >
        <Icon name="Layers" size={14} className="shrink-0" />
        <span className="min-w-0 flex-1 font-medium">{stackBanner(cards.length)}</span>
        <span className="flex shrink-0 items-center" aria-label={`Card ${at + 1} of ${cards.length}`}>
          {cards.map((c, i) => (
            <Button
              key={c.key}
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label={`Show card ${i + 1} of ${cards.length}`}
              aria-current={i === at ? "true" : undefined}
              onClick={() => go(i)}
            >
              <span
                className={`block h-2 w-2 rounded-full ${i === at ? "bg-primary" : "bg-primary/30"}`}
              />
            </Button>
          ))}
        </span>
      </div>
      <div
        className="relative"
        style={{ paddingBottom: depth * 6, touchAction: "pan-y" }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        {/* The edges of the cards behind: they say "more than one" at a
            glance, before any words are read. */}
        {Array.from({ length: depth }, (_, i) => (
          <div
            key={i}
            aria-hidden
            data-approval-stack-edge=""
            className="absolute rounded-lg border border-border bg-card"
            style={{ left: (i + 1) * 8, right: (i + 1) * 8, top: (i + 1) * 6, bottom: 0, opacity: 0.7 - i * 0.25 }}
          />
        ))}
        <div
          className={`relative ${nudge ? "approval-stack-nudge" : ""}`}
          data-nudge={nudge ? "" : undefined}
          style={dragX ? { transform: `translateX(${Math.round(dragX * 0.6)}px)` } : undefined}
        >
          {shownCard}
        </div>
      </div>
    </div>
  );
}
