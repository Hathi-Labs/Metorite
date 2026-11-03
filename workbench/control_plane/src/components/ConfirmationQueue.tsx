"use client";

/**
 * ConfirmationQueue — every pending confirmation card, as ONE card.
 *
 * A turn can park many gated tools at once (2026-10-06: ten). One card
 * shows. With two cards or more, the cards that wait together are TABS inside
 * that card (owner, 2026-11-03, D173): "Instead of having, say, a stacked
 * cards animation, we just have one card. When there are multiple cards that
 * need to be approved, it shows up as one single card with tabs." A count
 * leads the row, and a dot marks a card the member has not opened. No stacked
 * edges, no swipe and no swap animation. Answering a card removes it, and the
 * next tab to the right opens, else the one to the left.
 *
 * No "Approve all". Each card is one consent, and a card the member did not
 * open is a card nobody read (the Projects writes rule, MAX_BATCH). A batch
 * tool with ONE card is the answer to many cards of one kind.
 *
 * The queue itself is `lib/confirmationQueue.ts`. The tab rules are
 * `lib/cardTabs.ts`, and the row is `components/ui/CardTabs.tsx`.
 */

import { useEffect, useId, useReducer, useRef, useState } from "react";

import ConfirmationCard, { cardSummary } from "@/components/ConfirmationCard";
import CardTabs, { cardPanelId, cardTabId, type CardTab } from "@/components/ui/CardTabs";
import {
  NO_TICKS,
  rowTicksReducer,
  rowsView,
  type PendingConfirmation,
  type RowsApproval,
} from "@/lib/confirmationQueue";
import { restoredFirst } from "@/lib/cardTabs";
import { unfenced } from "@/lib/fencedText";
import type { ReplyApproval } from "@/lib/replyCard";

/** A plain answer, an Approve that names the ticked rows of a card with
 *  rows (WS-46 P13 one-card), or the Send or Save of a reply card (EM-CR).
 *  Each goes in ONE respond-input. */
export type ConfirmationAnswer = "APPROVE" | "REJECT" | RowsApproval | ReplyApproval;

interface ConfirmationQueueProps {
  cards: PendingConfirmation[];
  /** Why the last answer of a card failed, by key (UXD-3). */
  errors?: ReadonlyMap<string, string>;
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

export default function ConfirmationQueue({ cards, errors, onAnswer, initialIndex = 0 }: ConfirmationQueueProps) {
  const [shown, setShown] = useState<Shown>({ key: null, index: initialIndex });
  // The member's ticks of every card with rows, by key. The queue holds
  // them, so a page away and back keeps them (PR #691 review). The logic is
  // `rowTicksReducer` and `rowsView`, and this component holds none.
  const [ticks, tick] = useReducer(rowTicksReducer, NO_TICKS);
  const uid = useId();
  // The cards the member has opened, by key: a tab not in it wears a dot.
  const [seen, setSeen] = useState<ReadonlySet<string>>(() => new Set());
  const prevKeys = useRef<string[]>([]);
  // The queue pins the shown card in `shown`, so opening a tab marks it seen.
  const shownKey = shown.key;
  useEffect(() => {
    if (shownKey !== null) setSeen((s) => (s.has(shownKey) ? s : new Set(s).add(shownKey)));
  }, [shownKey]);
  if (cards.length === 0) {
    prevKeys.current = [];
    return null;
  }
  // A card that comes back first (Undo of a Reject) opens, so the member
  // sees what they tried to answer. Any other change keeps the member's card.
  const keys = cards.map((c) => c.key);
  const opened = restoredFirst(prevKeys.current, keys);
  const pinned: Shown = opened !== null ? { key: opened, index: 0 } : shown;
  prevKeys.current = keys;
  const at = pickShown(cards, pinned);
  const card = cards[at];
  if (shown.key !== card.key || shown.index !== at) {
    // Pin the card on screen (React's adjust-state-during-render pattern).
    setShown({ key: card.key, index: at });
  }
  const go = (to: number) => setShown({ key: cards[to].key, index: to });
  const view = rowsView(card, ticks);
  const tabbed = cards.length > 1;
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
      error={errors?.get(card.key)}
      rows={view.rows}
      onToggle={(id, on) => tick({ type: "toggle", card, id, on })}
      // With tabs, they are the one switcher (D173), so the card draws no
      // "1 of N" pager of its own.
      position={
        tabbed
          ? undefined
          : { index: at, total: cards.length, onPrev: () => go(Math.max(0, at - 1)), onNext: () => go(Math.min(cards.length - 1, at + 1)) }
      }
      onApprove={() => {
        // One respond-input: the Approve and the ticked ids together. The
        // answered card carries the ticks, so a restore shows them again.
        if (view.approved) onAnswer(view.approved.card, view.approved.answer);
      }}
      onReject={() => onAnswer(card, "REJECT")}
      // EM-CR: a reply card answers with the member's choice and body.
      {...(card.reply !== undefined ? { reply: card.reply, onReply: (a: ReplyApproval) => onAnswer(card, a) } : {})}
      // The tab row sits inside the card's frame, and the card is its panel.
      {...(tabbed
        ? {
            tabs: <CardTabs uid={uid} index={at} onGo={go} tabs={cards.map((c) => ({ ...cardTab(c), unseen: !seen.has(c.key) }))} />,
            tabPanel: { id: cardPanelId(uid), labelledBy: cardTabId(uid, at) },
          }
        : {})}
    />
  );
  return shownCard;
}

/** A card's tab: its kind and the short name its own header leads with. */
export function cardTab(card: PendingConfirmation): CardTab {
  const { summary } = cardSummary(card.title, card.detail);
  return {
    key: card.key,
    label: card.fenced ? unfenced(summary) : summary,
    icon: card.reply !== undefined ? "Reply" : "ShieldCheck",
  };
}
