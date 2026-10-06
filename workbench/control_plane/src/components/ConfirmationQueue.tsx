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
 * The queue itself is `lib/confirmationQueue.ts`.
 */

import { useState } from "react";

import ConfirmationCard from "@/components/ConfirmationCard";
import {
  approvedCard,
  withTicked,
  type PendingConfirmation,
  type RowsApproval,
} from "@/lib/confirmationQueue";

/** A plain answer, or an Approve that names the ticked rows of a card with
 *  rows (WS-46 P13 one-card). Both go in ONE respond-input. */
export type ConfirmationAnswer = "APPROVE" | "REJECT" | RowsApproval;

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

export default function ConfirmationQueue({ cards, onAnswer, initialIndex = 0 }: ConfirmationQueueProps) {
  const [shown, setShown] = useState<Shown>({ key: null, index: initialIndex });
  const [ticks, setTicks] = useState<Record<string, string[]>>({});
  if (cards.length === 0) return null;
  const at = pickShown(cards, shown);
  const card = cards[at];
  if (shown.key !== card.key || shown.index !== at) {
    // Pin the card on screen (React's adjust-state-during-render pattern).
    setShown({ key: card.key, index: at });
  }
  const go = (to: number) => setShown({ key: cards[to].key, index: to });
  // The member's ticks, per card. A card remounts when the member pages away
  // and back, and when a failed POST restores it, and both must keep the
  // ticks the member chose (review round 1). `withTicked` carries them.
  const shownCard = withTicked(card, ticks[card.key]);
  return (
    <ConfirmationCard
      // A new key per card: a long body scrolled on one card does not stay
      // scrolled on the next, and the buttons arm again (ConfirmationCard).
      key={card.key}
      title={card.title}
      detail={card.detail}
      context={card.context}
      rows={shownCard.rows}
      onTickedChange={(ids) => setTicks((prev) => ({ ...prev, [card.key]: ids }))}
      position={{
        index: at,
        total: cards.length,
        onPrev: () => go(Math.max(0, at - 1)),
        onNext: () => go(Math.min(cards.length - 1, at + 1)),
      }}
      onApprove={(ticked) => {
        // One respond-input: the Approve and the ticked ids together. The
        // answered card carries the ticks, so a restore shows them again.
        const approved = approvedCard(card, ticked);
        if (approved) onAnswer(approved.card, approved.answer);
      }}
      onReject={() => onAnswer(card, "REJECT")}
    />
  );
}
