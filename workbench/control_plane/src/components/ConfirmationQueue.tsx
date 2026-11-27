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
 *
 * §18.11: a new-mail card that a chat message closed keeps its tab as a slot
 * with no buttons ("Updating the draft…"), and the new draft takes that tab.
 * The rules are `lib/redraftCard.ts`.
 */

import { useId, useReducer, useState, type ReactNode } from "react";

import ConfirmationCard, { cardSummary } from "@/components/ConfirmationCard";
import Button from "@/components/ui/Button";
import CardTabs, { cardPanelId, cardTabId, type CardTab } from "@/components/ui/CardTabs";
import { CARD_FRAME_CLASS } from "@/components/ui/Collapsible";
import {
  NO_TICKS,
  earlierOf,
  asNewMailEdits,
  asTaskEdits,
  rowTicksReducer,
  rowsView,
  type FormEdit,
  type PendingConfirmation,
  type RowsApproval,
} from "@/lib/confirmationQueue";
import { cardKind, countWordsFor, KIND_ICON, restoredFirst, tabLabel } from "@/lib/cardTabs";
import { unfenced } from "@/lib/fencedText";
import type { CardDraft } from "@/lib/redraftCard";
import { DELETE_EARLIER_ANSWER, isNewMail, type EarlierDraft, type NewMailEdits, type ReplyApproval } from "@/lib/replyCard";
import type { TaskCardEdits, TaskEditApproval } from "@/lib/taskEditCard";
import { unfoldLabel, type DockMode } from "@/lib/waitingDock";

/** A plain answer, an Approve that names the ticked rows of a card with
 *  rows (WS-46 P13 one-card), or the Send or Save of a reply card (EM-CR).
 *  Each goes in ONE respond-input. Task 3978: the Discard of a new-mail card
 *  that also deletes the earlier draft of the chat is a REJECT too. */
export type ConfirmationAnswer =
  | "APPROVE" | "REJECT" | typeof DELETE_EARLIER_ANSWER | RowsApproval | ReplyApproval | TaskEditApproval;

interface ConfirmationQueueProps {
  cards: PendingConfirmation[];
  /** Why the last answer of a card failed, by key (UXD-3). */
  errors?: ReadonlyMap<string, string>;
  onAnswer: (card: PendingConfirmation, answer: ConfirmationAnswer) => void;
  /** The card to show first. Tests and the visual rig set it. */
  initialIndex?: number;
  /** Put words in the chat box, and send nothing (a new-mail card's chips, §18.10). */
  onSuggest?: (text: string) => void;
  /** §18.11: a new-mail card's version after each edit, by the card's key. */
  onDraftChange?: (key: string, draft: CardDraft | null) => void;
  /** §18.11: a message in the chat carries the member's version now. */
  carriesEdits?: boolean;
  /**
   * The member's edits of a form, by key: a new mail or a task card (EAC-2).
   * The CHAT holds them (fix round 5), because this queue mounts again on each
   * new message, and when its last card leaves. Each card reads its own kind
   * through a guard (`asNewMailEdits`, `asTaskEdits`).
   */
  savedEdits?: (key: string) => FormEdit | undefined;
  /** A form reports the member's edits, by key. */
  onEdits?: (key: string, edits: FormEdit) => void;
  /**
   * The waiting dock (§24.11 WD-D3): the question cards that wait, as tabs of
   * this ONE card, after the approvals. With it set, the queue draws in the
   * dock's shape: every question card stays mounted (hidden when its tab is
   * not open), so its picks and typed text live through a change of tab and
   * a fold. Without it, the queue draws as before.
   */
  questions?: readonly DockQuestion[];
  /** The dock is folded to one row: the tabs, the title and one button that
   *  only opens the card (audit rule 2). */
  folded?: boolean;
  /** Open the folded dock. The fold row's one button calls it. */
  onUnfold?: () => void;
  /** The dock's Expand and Fold controls. They sit in the tab row, or on the
   *  title row of the one card when no tab row is drawn, so no row of their
   *  own takes height. */
  dockControls?: ReactNode;
  /** The dock's view of the open card (D2): Compact or Expanded. */
  dockMode?: DockMode;
  /** Open the Expanded view. A card's Review calls it (D3). */
  onExpand?: () => void;
}

/** What the queue hands a card in the dock: the tab row, the panel, and the
 *  dock's controls for a card with no tab row. */
export interface DockSlot {
  tabs?: ReactNode;
  tabPanel?: { id: string; labelledBy: string };
  headerAside?: ReactNode;
}

/** A question card as a tab of the dock's one card (§24.11). */
export interface DockQuestion {
  /** Stable for the card's life: its request id. */
  key: string;
  /** The tab's words (`questionTabLabel`). */
  label: string;
  /** The whole question, for the tooltip and the fold row. */
  title: string;
  /** Draw the card, with the tab row and the panel when it is the open tab. */
  render: (slot: DockSlot) => ReactNode;
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
export function pickShown(cards: readonly Pick<PendingConfirmation, "key">[], shown: Shown): number {
  const found = shown.key === null ? -1 : cards.findIndex((c) => c.key === shown.key);
  return found >= 0 ? found : shownIndex(shown.index, cards.length);
}

/**
 * What the queue holds after its cards changed: the keys it saw, and the
 * card it shows. A card that came back first opens (`restoredFirst`), and any
 * other change keeps the member's card. Pure and idempotent, so a render that
 * React runs twice reaches the same state. The component calls it, and
 * `confirmationQueue.test.ts` runs it over a real Undo.
 *
 * One change does move the view (§18.11). The member looks at a slot, which
 * has no button, and the queue changes: a card arrives, or the shown card
 * turns into a slot. The view then goes to the FIRST live card, new or not
 * (fix round 5), because the run waits on the member there. A slot counts
 * apart from a card in `keys`, so a card that turns into a slot is a change.
 */
/** The mark of a slot in the keys of `reconcileShown`. No card id holds it. */
const SLOT_MARK = "\u0000slot";
const NO_KEYS: ReadonlySet<string> = new Set();

export function reconcileShown(
  prevKeys: readonly string[],
  cards: readonly Pick<PendingConfirmation, "key" | "redraft">[],
  shown: Shown,
  questionKeys: ReadonlySet<string> = NO_KEYS,
): { keys: readonly string[]; shown: Shown } {
  const keys = cards.map((c) => (c.redraft ? `${c.key}${SLOT_MARK}` : c.key));
  if (keys.length === prevKeys.length && keys.every((k, i) => k === prevKeys[i])) return { keys: prevKeys, shown };
  // The dock (§24.11): a question tab sits after the approvals, so a new
  // approval lands before it. Only the approvals tell a restored card.
  const plain = (list: readonly string[]) =>
    list.flatMap((k) => (questionKeys.has(k.replace(SLOT_MARK, "")) ? [] : [k.replace(SLOT_MARK, "")]));
  const opened = restoredFirst(plain(prevKeys), plain(keys));
  if (opened !== null) return { keys, shown: { key: opened, index: 0 } };
  const showing = cards.find((c) => c.key === shown.key);
  const live = showing?.redraft ? cards.findIndex((c) => !c.redraft) : -1;
  return { keys, shown: live >= 0 ? { key: cards[live].key, index: live } : shown };
}

export default function ConfirmationQueue({ cards, errors, onAnswer, initialIndex = 0, onSuggest, onDraftChange, carriesEdits, savedEdits, onEdits, questions, folded = false, onUnfold, dockControls, dockMode, onExpand }: ConfirmationQueueProps) {
  const [shown, setShown] = useState<Shown>({ key: null, index: initialIndex });
  // The member's ticks of every card with rows, by key. The queue holds
  // them, so a page away and back keeps them (PR #691 review). The logic is
  // `rowTicksReducer` and `rowsView`, and this component holds none.
  const [ticks, tick] = useReducer(rowTicksReducer, NO_TICKS);
  const uid = useId();
  // The cards the member has opened, by key: a tab not in it wears a dot.
  const [seen, setSeen] = useState<ReadonlySet<string>>(() => new Set());
  // The keys of the last render, in state and not in a ref: a render that
  // React runs twice (StrictMode) must see the same change both times.
  const [prevKeys, setPrevKeys] = useState<readonly string[]>([]);
  // The queue pins the shown card in `shown`, so opening a tab marks it seen.
  const shownKey = shown.key;
  if (shownKey !== null && !seen.has(shownKey)) setSeen(new Set(seen).add(shownKey));
  // A card that comes back first (Undo of a Reject) opens, so the member
  // sees what they tried to answer. Any other change keeps the member's card.
  // (React's adjust-state-during-render pattern, as for `shown` below.)
  // The dock (§24.11, D4): the question cards come first, then the
  // approvals, oldest first, in ONE list with ONE shown rule. Without the
  // dock, the list is the queue.
  const items: readonly Pick<PendingConfirmation, "key" | "redraft">[] = questions
    ? [...questions.map((q) => ({ key: q.key })), ...cards]
    : cards;
  const next = reconcileShown(prevKeys, items, shown, questions ? new Set(questions.map((q) => q.key)) : NO_KEYS);
  if (next.keys !== prevKeys) setPrevKeys(next.keys);
  if (next.shown !== shown) setShown(next.shown);
  if (items.length === 0) return null;
  const at = pickShown(items, shown);
  const item = items[at];
  if (shown.key !== item.key || shown.index !== at) {
    // Pin the card on screen (React's adjust-state-during-render pattern).
    setShown({ key: item.key, index: at });
  }
  const go = (to: number) => setShown({ key: items[to].key, index: to });
  // The dock always draws its header row (D1), so a card there has no pager.
  const tabbed = questions ? true : items.length > 1;
  // The one approval card that shows, with the tab row in its frame or none.
  const approvalCard = (card: PendingConfirmation, slot: DockSlot | null) => {
  const view = rowsView(card, ticks);
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
      {...(card.reply !== undefined ? { reply: card.reply, onReply: (a: ReplyApproval) => onAnswer(card, a), onSuggest } : {})}
      // Task 3978: Discard or Reject, then "Delete it", on a card that names a draft of the chat.
      {...earlierProps(card, onAnswer)}
      // §18.11: the slot of a redraft answers nothing. A new-mail card
      // reports each edit, and a redrafted card knows the member's last version.
      {...(card.redraft ? { slot: card.redraft } : {})}
      {...(onDraftChange ? { onDraftChange: (d: CardDraft | null) => onDraftChange(card.key, d) } : {})}
      {...(isNewMail(card.reply)
        ? { carriesEdits, savedEdits: asNewMailEdits(savedEdits?.(card.key)), onEdits: (value: NewMailEdits) => onEdits?.(card.key, value) }
        : {})}
      // EAC-2: a task card that carries a form. Approve sends the edit answer, or a plain APPROVE.
      {...(card.edit
        ? {
            edit: card.edit,
            savedTaskEdits: asTaskEdits(savedEdits?.(card.key)),
            onTaskEdits: (value: TaskCardEdits) => onEdits?.(card.key, value),
            onEditAnswer: (answer: "APPROVE" | TaskEditApproval) => onAnswer(card, answer),
          }
        : {})}
      {...(card.before ? { before: card.before } : {})}
      // The tab row sits inside the card's frame, and the card is its panel.
      {...(slot ?? {})}
      // The dock (D2): the Compact or the Expanded view of this card.
      {...(questions ? { dockMode: dockMode ?? "compact", onExpand } : {})}
    />
  );
  return shownCard;
  };
  if (questions) {
    return (
      <DockQueue
        cards={cards}
        questions={questions}
        at={at}
        go={go}
        uid={uid}
        seen={seen}
        folded={folded}
        onUnfold={onUnfold}
        controls={dockControls}
        approvalCard={at >= questions.length ? (tabs) => approvalCard(cards[at - questions.length], tabs) : null}
      />
    );
  }
  return approvalCard(cards[at], tabbed
    ? {
        tabs: <CardTabs uid={uid} index={at} onGo={go} tabs={cards.map((c) => ({ ...cardTab(c), unseen: !seen.has(c.key) }))} />,
        tabPanel: { id: cardPanelId(uid), labelledBy: cardTabId(uid, at) },
      }
    : null);
}

/**
 * The queue in the waiting dock (§24.11): approvals and question cards as the
 * tabs of ONE card. The open tab draws its card. Each question card stays
 * mounted, hidden while its tab is closed, so its picks and typed text live
 * through a change of tab and a fold. A folded dock draws one row: the tabs,
 * the title of the open item and one button that ONLY opens the card. It
 * never approves, rejects or sends (audit rule 2): the consent is on the card.
 */
function DockQueue({
  cards,
  questions,
  at,
  go,
  uid,
  seen,
  folded,
  onUnfold,
  controls,
  approvalCard,
}: {
  cards: PendingConfirmation[];
  questions: readonly DockQuestion[];
  at: number;
  go: (to: number) => void;
  uid: string;
  seen: ReadonlySet<string>;
  folded: boolean;
  onUnfold?: () => void;
  controls?: ReactNode;
  approvalCard: ((slot: DockSlot | null) => ReactNode) | null;
}) {
  // D4: the questions first, then the approvals.
  const tabs: CardTab[] = [
    ...questions.map((q) => ({ key: q.key, label: q.label, title: q.title, icon: KIND_ICON.question, unseen: !seen.has(q.key) })),
    ...cards.map((c) => ({ ...cardTab(c), unseen: !seen.has(c.key) })),
  ];
  const words = countWordsFor([...questions.map(() => "question" as const), ...cards.map(cardKind)]);
  const kind = at < questions.length ? "question" : "approval";
  // The fold row's one button only opens the card. Never a consent.
  const unfold = (
    <Button
      type="button"
      size="sm"
      variant="secondary"
      onClick={onUnfold}
      data-dock-unfold={kind}
      className="ml-2 shrink-0 self-center"
    >
      {unfoldLabel(kind)}
    </Button>
  );
  // The header row (D1): the count, the tabs, Expand and Fold, also for one
  // item. It is in the open card, or in the fold row. Never both, so each DOM
  // id is drawn once.
  const row = (trailing?: ReactNode) => (
    <CardTabs uid={uid} index={at} onGo={go} tabs={tabs} words={words} trailing={trailing} always />
  );
  const slot: DockSlot | null = folded
    ? null
    : { tabs: row(controls), tabPanel: { id: cardPanelId(uid), labelledBy: cardTabId(uid, at) } };
  return (
    <div data-dock-queue="" data-dock-folded={folded ? "" : undefined}>
      {folded && (
        <div data-dock-fold-row="" className={`${CARD_FRAME_CLASS} glass:rounded-[16px] glass:border-primary/35 glass:p-3`}>
          {/* The open tab's label is the title of the open item. */}
          <div className="[&>[data-card-tabs]]:!m-0 [&>[data-card-tabs]]:!px-0 [&>[data-card-tabs]]:border-b-0">{row(unfold)}</div>
        </div>
      )}
      <div hidden={folded}>
        {questions.map((q, i) => {
          const open = at === i;
          return (
            <div key={q.key} hidden={!open} data-dock-question={q.key}>
              {q.render(open ? (slot ?? {}) : {})}
            </div>
          );
        })}
        {approvalCard?.(slot)}
      </div>
    </div>
  );
}

/**
 * Task 3978 (§18.13.1): the props that let a card ask "Keep the earlier
 * draft, or delete it?". A new-mail card with an `earlier` draft gets the
 * answer only, because its form holds the line. A plain card gets the line
 * too. Any other card gets nothing, so its Discard or Reject is as before.
 */
export function earlierProps(
  card: PendingConfirmation,
  onAnswer: (card: PendingConfirmation, answer: ConfirmationAnswer) => void,
): { onDiscardEarlier?: () => void; earlier?: EarlierDraft } {
  const earlier = earlierOf(card);
  if (!earlier) return {};
  const onDiscardEarlier = () => onAnswer(card, DELETE_EARLIER_ANSWER);
  return isNewMail(card.reply) ? { onDiscardEarlier } : { onDiscardEarlier, earlier };
}

/** A card's tab: its kind, a short label (`tabLabel`), and the card's full
 *  summary for the tooltip. */
export function cardTab(card: PendingConfirmation): CardTab {
  const { summary } = cardSummary(card.title, card.detail);
  return {
    key: card.key,
    label: tabLabel(card),
    title: card.fenced ? unfenced(summary) : summary,
    icon: KIND_ICON[cardKind(card)],
    // A slot of a redraft is a tab, and no card waits on the member in it.
    ...(card.redraft ? { updating: true } : {}),
  };
}
