/**
 * redraftCard — a redraft through the chat keeps the member's edits
 * (`email_app_master_plan.md` §18.11, owner 2026-11-05).
 *
 * While the card of a new mail waits, the member can edit it and then ask in
 * the chat for a change ("make it shorter"). This file holds the client's
 * rules of that redraft. They are pure, so the node-env vitest holds them.
 *
 * 1. The snapshot (step 1). `NewMailCardForm` reports the member's version
 *    after each edit (`cardDraftOf`), and `AgentChat` keeps it. The next steer
 *    of the chat carries the newest one as `card_draft` (`pickCardDraft`).
 *    The gateway checks it against its own record of the card. The snapshot
 *    only informs the model, and no act reads it.
 * 2. The slot (step 7). The queue (`lib/confirmationQueue.ts`) keeps a
 *    new-mail card that a steer closed as a slot with no buttons:
 *    "Updating the draft…". The next card whose `revises` names it takes its
 *    place, in the same tab (D173), with the label "Draft <n>". A run that
 *    ends with no new card turns the slot into "The draft did not change.",
 *    and then closes it.
 * 3. The "Changed:" line. The client computes it from its own copy of the
 *    member's last version and the new text, with `diffKeys` of
 *    `app/knowledge/lib/wordDiff.ts`, and never from a claim of the model.
 *
 * Fence: `src/components/redraftCard.test.ts`.
 */

import { diffKeys } from "@/app/knowledge/lib/wordDiff";
import { tabLabel } from "@/lib/cardTabs";
import type { PendingConfirmation } from "@/lib/confirmationQueue";
import { isNewMail, startTicks, tickedFor, type ReplyForm, type ReplyRecipients, type ReplyTicks } from "@/lib/replyCard";

/**
 * The member's version of an open new-mail card, as a steer carries it. The
 * keys are exactly those of `redraft.CardDraft` in the gateway, and
 * `tests/unit/test_email_redraft_snapshot.py` reads this list to hold the two
 * in step.
 */
export interface CardDraft {
  request_id: string;
  subject: string;
  body: string;
  to: string[];
  cc: string[];
  bcc: string[];
}

export const CARD_DRAFT_KEYS: readonly (keyof CardDraft)[] = ["request_id", "subject", "body", "to", "cc", "bcc"];

/** The words of the slot while the assistant writes the new draft. */
export const UPDATING_LINE = "Updating the draft…";
/** The words of the slot when the run ended with no new card. */
export const UNCHANGED_LINE = "The draft did not change.";
/** How long "The draft did not change." shows before the slot closes. */
export const UNCHANGED_CLOSE_MS = 4000;

/** The text of a card: its subject and its text. */
export interface CardText {
  subject: string;
  body: string;
}

/**
 * The version of a card that a steer carried: its text, and the people the
 * member left ticked. Lower-case addresses, as the form keeps its ticks.
 */
export interface SentVersion extends CardText {
  to?: string[];
  cc?: string[];
  bcc?: string[];
}

/** The people of the member's last version of the old card (P1). */
export interface PeopleBefore {
  /** The people the member left ticked. */
  ticked: string[];
  /** The people the member unticked. */
  unticked: string[];
}

/** The people of the new card. */
export interface PeopleAfter {
  /** Every person the new card offers. */
  all: string[];
  /** The people it starts with ticked, after the member's unticks. */
  ticked: string[];
}

const low = (list: readonly string[] | undefined) => (list ?? []).map((a) => a.toLowerCase());

/** The people of a sent version: the ones it left ticked. */
export function tickedOfSent(v: SentVersion): string[] {
  return low([...(v.to ?? []), ...(v.cc ?? []), ...(v.bcc ?? [])]);
}

/**
 * The people of the old card that the member unticked: every person it
 * offered that its sent version did not keep ticked. UNTICK ONLY: the result
 * only ever removes a tick from the new card (P1).
 */
export function untickedOf(form: ReplyForm, sent: SentVersion): string[] {
  const all = form.recipients.reply_all;
  const offered = low([...all.to, ...all.cc, ...(all.bcc ?? [])]);
  const on = new Set(tickedOfSent(sent));
  return [...new Set(offered)].filter((a) => !on.has(a));
}

/**
 * The form of a redrafted card with each person the member unticked still
 * unticked (P1). It narrows the ticked start and never widens it: a person the
 * new card did not tick stays unticked, and nobody is added.
 */
export function withUnticked(form: ReplyForm, unticked: readonly string[]): ReplyForm {
  if (!unticked.length) return form;
  const off = new Set(low(unticked));
  const narrow = (r: ReplyRecipients): ReplyRecipients => ({
    to: r.to.filter((a) => !off.has(a.toLowerCase())),
    cc: r.cc.filter((a) => !off.has(a.toLowerCase())),
    ...(r.bcc ? { bcc: r.bcc.filter((a) => !off.has(a.toLowerCase())) } : {}),
  });
  const start = form.ticked ?? form.recipients;
  return { ...form, ticked: { reply: narrow(start.reply), reply_all: narrow(start.reply_all) } };
}

const sameList = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && a.every((x, i) => x === b[i]);

/**
 * The member's version of a new-mail card, whether they edited it or not:
 * the subject, the text and the people they left ticked, from the server's
 * own lists. A steer carries one for EACH open card (P2), so the gateway
 * replaces the snapshot that an older steer left for a card the member has
 * since put back to its start. Null for a card that is not a new mail.
 */
export function snapshotOf(
  form: ReplyForm,
  requestId: string,
  now: { subject: string; body: string; ticks: ReplyTicks },
): CardDraft | null {
  if (!isNewMail(form) || !/^[0-9a-f]{32}$/.test(requestId)) return null;
  const picked = tickedFor(form, true, now.ticks);
  return { request_id: requestId, subject: now.subject, body: now.body, to: picked.to, cc: picked.cc, bcc: picked.bcc ?? [] };
}

/**
 * The member's version of a new-mail card, or null when the card shows its
 * own start: no edit of the subject, the text or the ticks. `to`, `cc` and
 * `bcc` are the people that the member left ticked, from the server's own
 * lists.
 */
export function cardDraftOf(
  form: ReplyForm,
  requestId: string,
  now: { subject: string; body: string; ticks: ReplyTicks },
): CardDraft | null {
  if (!isNewMail(form)) return null;
  const ticksMoved = !sameList(startTicks(form).reply_all, now.ticks.reply_all);
  if (now.subject === form.subject && now.body === form.body && !ticksMoved) return null;
  return snapshotOf(form, requestId, now);
}

/** The version that a card's form last reported, with the order of the edit. */
export interface KeptDraft {
  draft: CardDraft;
  seq: number;
}

/** A card that waits on the member: not a slot of a redraft. */
export function isLiveCard(card: Pick<PendingConfirmation, "redraft">): boolean {
  return card.redraft === undefined;
}

/** An open new-mail card: it waits, and its form is a new mail. */
function isOpenNewMail(card: PendingConfirmation): boolean {
  return isLiveCard(card) && isNewMail(card.reply) && !!card.requestId;
}

/**
 * The versions that the next steer carries (P2): one for EACH open new-mail
 * card, in the queue's order. A card the member edited gives its newest
 * edit. A card at its start gives its start, so the gateway replaces or
 * deletes a snapshot that an older steer left for it. A slot, an answered
 * card and a card of another kind give none.
 */
export function pickCardDrafts(
  cards: readonly PendingConfirmation[],
  kept: ReadonlyMap<string, KeptDraft>,
): CardDraft[] {
  const out: CardDraft[] = [];
  for (const card of cards) {
    if (!isOpenNewMail(card) || !card.reply) continue;
    const k = kept.get(card.key);
    if (k && k.draft.request_id === card.requestId) {
      out.push(k.draft);
      continue;
    }
    const start = snapshotOf(card.reply, card.requestId ?? "", {
      subject: card.reply.subject, body: card.reply.body, ticks: startTicks(card.reply),
    });
    if (start) out.push(start);
  }
  return out;
}

/** The newest open new-mail card, for the composer's line, or null. */
export function changingCard(cards: readonly PendingConfirmation[]): PendingConfirmation | null {
  for (let i = cards.length - 1; i >= 0; i--) {
    if (isOpenNewMail(cards[i])) return cards[i];
  }
  return null;
}

/** The composer's line while a new-mail card is open: "Changing: Email to Kim". */
export function changingLine(card: PendingConfirmation): string {
  return `Changing: ${tabLabel(card)}`;
}

/** The label of a redrafted card, "Draft 2", or "" for a first draft. */
export function draftLabel(draft: number | undefined): string {
  return typeof draft === "number" && Number.isInteger(draft) && draft >= 2 ? `Draft ${draft}` : "";
}

/** The words of the slot of a redraft. */
export function slotLine(slot: "updating" | "unchanged"): string {
  return slot === "updating" ? UPDATING_LINE : UNCHANGED_LINE;
}

// ── The "Changed:" line ─────────────────────────────────────────────────────

/** One change of the new card: the words that went and the words that came. */
export interface ChangeRun {
  where: "subject" | "text";
  removed: string;
  added: string;
}

/** The people that changed between the old card and the new one (P1). */
export interface PeopleChange {
  /** Ticked on the new card, and not ticked on the member's version. */
  added: string[];
  /** Ticked on the member's version, and not ticked on the new card. */
  removed: string[];
  /** Unticked by the member, offered again, and kept unticked. */
  keptOut: string[];
}

export interface Changes {
  /** The people who changed. */
  people: PeopleChange;
  /** The first changes, in the card's order. */
  runs: ChangeRun[];
  /** How many more changes the line does not show. */
  more: number;
  /** The diff stopped at its limit, so the middle of the text is one change. */
  coarse: boolean;
  /** The new card has the same words and the same people as the member's last version. */
  none: boolean;
}

/** The most changes that the line shows. */
export const MAX_RUNS = 4;
/** The most characters of one side of a change. */
export const RUN_CHARS = 60;

const words = (s: string): string[] => s.split(/\s+/).filter(Boolean);

function cut(s: string): string {
  return s.length <= RUN_CHARS ? s : `${s.slice(0, RUN_CHARS - 1).trimEnd()}…`;
}

function runsOf(where: ChangeRun["where"], before: string, after: string): { runs: ChangeRun[]; coarse: boolean } {
  const a = words(before);
  const b = words(after);
  const { steps, coarse } = diffKeys(a, b);
  const runs: ChangeRun[] = [];
  let removed: string[] = [];
  let added: string[] = [];
  const close = () => {
    if (removed.length || added.length) runs.push({ where, removed: cut(removed.join(" ")), added: cut(added.join(" ")) });
    removed = [];
    added = [];
  };
  for (const s of steps) {
    if (s.kind === "equal") close();
    else if (s.kind === "delete") removed.push(a[s.a]);
    else added.push(b[s.b]);
  }
  close();
  return { runs, coarse };
}

/**
 * What changed from the member's last version to the new card, word by word,
 * and person by person. The subject comes first, then the text. A change of
 * spaces or of line breaks alone is no change.
 */
export function changesOf(
  before: CardText & { people?: PeopleBefore },
  after: CardText & { people?: PeopleAfter },
): Changes {
  const s = runsOf("subject", before.subject, after.subject);
  const t = runsOf("text", before.body, after.body);
  const all = [...s.runs, ...t.runs];
  const people: PeopleChange = { added: [], removed: [], keptOut: [] };
  if (before.people && after.people) {
    const was = new Set(before.people.ticked);
    const now = new Set(after.people.ticked);
    const offered = new Set(after.people.all);
    people.added = after.people.ticked.filter((a) => !was.has(a));
    people.removed = before.people.ticked.filter((a) => !now.has(a));
    people.keptOut = before.people.unticked.filter((a) => offered.has(a) && !now.has(a));
  }
  return {
    people,
    runs: all.slice(0, MAX_RUNS),
    more: Math.max(0, all.length - MAX_RUNS),
    coarse: s.coarse || t.coarse,
    none: all.length === 0 && !people.added.length && !people.removed.length,
  };
}

/** The people of a change as plain words: "people added a; removed b". */
export function peopleParts(p: PeopleChange): string[] {
  const out: string[] = [];
  if (p.added.length) out.push(`people added ${p.added.join(", ")}`);
  if (p.removed.length) out.push(`people removed ${p.removed.join(", ")}`);
  return out;
}

/** The note on the people that stay unticked, or "". */
export function keptOutText(p: PeopleChange): string {
  return p.keptOut.length ? `Still unticked: ${p.keptOut.join(", ")}.` : "";
}

/** The "Changed:" line as plain words, for its accessible name and tests. */
export function changedText(c: Changes): string {
  const keep = keptOutText(c.people);
  const tail = keep ? ` ${keep}` : "";
  if (c.none) return `Changed: no words. This draft has the words of your last version.${tail}`;
  const words = c.coarse
    ? ["most of the text"]
    : c.runs.map((r) => {
        const lead = r.where === "subject" ? "subject " : "";
        if (r.removed && r.added) return `${lead}“${r.removed}” to “${r.added}”`;
        if (r.added) return `${lead}added “${r.added}”`;
        return `${lead}removed “${r.removed}”`;
      });
  const more = !c.coarse && c.more ? `, and ${c.more} more ${c.more === 1 ? "change" : "changes"}` : "";
  return `Changed: ${[...words, ...peopleParts(c.people)].join("; ")}${more}.${tail}`;
}
