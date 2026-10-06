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
import type { PendingConfirmation } from "@/lib/confirmationQueue";

export type ConfirmationAnswer = "APPROVE" | "REJECT";

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

export default function ConfirmationQueue({ cards, onAnswer, initialIndex = 0 }: ConfirmationQueueProps) {
  const [index, setIndex] = useState(initialIndex);
  if (cards.length === 0) return null;
  const at = shownIndex(index, cards.length);
  const card = cards[at];
  return (
    <ConfirmationCard
      // A new key per card, so a long body scrolled on one card does not
      // stay scrolled on the next.
      key={card.key}
      title={card.title}
      detail={card.detail}
      context={card.context}
      position={{
        index: at,
        total: cards.length,
        onPrev: () => setIndex(Math.max(0, at - 1)),
        onNext: () => setIndex(Math.min(cards.length - 1, at + 1)),
      }}
      onApprove={() => onAnswer(card, "APPROVE")}
      onReject={() => onAnswer(card, "REJECT")}
    />
  );
}
