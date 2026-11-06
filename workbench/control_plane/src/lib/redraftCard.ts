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
 *    place, in the same tab (D173), with the label "Draft <n>". A card with
 *    no link takes a slot only when its To shares an address with the To of
 *    the slot's card (`sharesTo`, fix round 3). So a new mail to another
 *    person shows as its own card. A slot that no card takes waits until
 *    the run ends. Then it closes with no line when a new-mail card came
 *    after it, and else it reads "The draft did not change." and closes.
 * 3. The "Changed:" line. The client computes it from its own copy of the
 *    member's last version and the new text, with `diffKeys` of
 *    `app/knowledge/lib/wordDiff.ts`, and never from a claim of the model.
 * 4. The people the member unticked (rule 7, fix round 2). The GATEWAY starts
 *    them unticked on the next card and names them (`ReplyForm.unticked`), so
 *    every path gets the same ticks: a slot, no slot, two revised cards, a
 *    remount, a reload and a replay. The client draws the server's start and
 *    the "Still unticked" note from the form. It ticks and unticks nobody.
 *
 * Fence: `src/components/redraftCard.test.ts`.
 */

import { diffKeys } from "@/app/knowledge/lib/wordDiff";
import { tabLabel } from "@/lib/cardTabs";
import type { PendingConfirmation } from "@/lib/confirmationQueue";
import { isNewMail, startTicks, tickedFor, type ReplyForm, type ReplyTicks } from "@/lib/replyCard";

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

/**
 * The most versions that one steer carries (P2). The chat proxy and the
 * gateway (`redraft.MAX_SNAPSHOTS`) keep the first ones, and
 * `tests/unit/test_email_redraft_snapshot.py` holds the numbers in step.
 */
export const MAX_CARD_DRAFTS = 8;

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

/** The people of the member's last version of the old card. */
export interface PeopleBefore {
  /** The people the member left ticked. */
  ticked: string[];
}

/** The people of the new card, as the server started them. */
export interface PeopleAfter {
  /** Every person the new card offers. */
  all: string[];
  /** The people it starts with ticked: the server's start (`ReplyForm.ticked`). */
  ticked: string[];
}

const low = (list: readonly string[] | undefined) => (list ?? []).map((a) => a.toLowerCase());

/** The people of a sent version: the ones it left ticked. */
export function tickedOfSent(v: SentVersion): string[] {
  return low([...(v.to ?? []), ...(v.cc ?? []), ...(v.bcc ?? [])]);
}

/**
 * True when two new-mail forms share an address in To, in any case (fix
 * round 3). The gateway links a redraft by the same rule (`redraft.link_fields`),
 * so a card with no link takes only the slot of a card to the same people.
 */
export function sharesTo(a: ReplyForm | null | undefined, b: ReplyForm | null | undefined): boolean {
  if (!a || !b || !isNewMail(a) || !isNewMail(b)) return false;
  const was = new Set(low(a.recipients.reply_all.to).map((x) => x.trim()).filter(Boolean));
  return low(b.recipients.reply_all.to).some((x) => was.has(x.trim()));
}

/**
 * The people that the server started unticked on this card because the
 * member unticked them on the draft before (rule 7), or none. They come from
 * the form, never from the client's own copy, so the note is right on every
 * path, and it never names a new address that the member did not untick.
 */
export function keptOutOf(form: ReplyForm | null | undefined): string[] {
  return form && isNewMail(form) ? [...(form.unticked ?? [])] : [];
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
 * card. A card the member edited gives its newest edit. A card at its start
 * gives its start, so the gateway replaces or deletes a snapshot that an
 * older steer left for it. A slot, an answered card and a card of another
 * kind give none.
 *
 * At most `MAX_CARD_DRAFTS` go (fix round 2). The edited cards go first, then
 * a card that starts a person unticked for the member, then the rest, each
 * group in the queue's order. So the cut loses no edit of the member.
 */
export function pickCardDrafts(
  cards: readonly PendingConfirmation[],
  kept: ReadonlyMap<string, KeptDraft>,
): CardDraft[] {
  const groups: CardDraft[][] = [[], [], []];
  for (const card of cards) {
    if (!isOpenNewMail(card) || !card.reply) continue;
    const k = kept.get(card.key);
    if (k && k.draft.request_id === card.requestId) {
      groups[0].push(k.draft);
      continue;
    }
    const start = snapshotOf(card.reply, card.requestId ?? "", {
      subject: card.reply.subject, body: card.reply.body, ticks: startTicks(card.reply),
    });
    if (start) groups[keptOutOf(card.reply).length ? 1 : 2].push(start);
  }
  return groups.flat().slice(0, MAX_CARD_DRAFTS);
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
  const people: PeopleChange = { added: [], removed: [] };
  if (before.people && after.people) {
    const was = new Set(before.people.ticked);
    const now = new Set(after.people.ticked);
    people.added = after.people.ticked.filter((a) => !was.has(a));
    people.removed = before.people.ticked.filter((a) => !now.has(a));
  }
  return {
    people,
    runs: all.slice(0, MAX_RUNS),
    more: Math.max(0, all.length - MAX_RUNS),
    coarse: s.coarse || t.coarse,
    none: all.length === 0 && !people.added.length && !people.removed.length,
  };
}

/**
 * One piece of the "Changed:" line: plain words, the words that went, or the
 * words that came. The card and the accessible name draw the SAME pieces
 * (`linePieces`), so the two always read alike.
 */
export type LinePiece = { text: string } | { removed: string } | { added: string };

/** The lead of the line. The card draws it in the strong weight. */
export const CHANGED_LEAD = "Changed: ";

function runPieces(r: ChangeRun): LinePiece[] {
  const lead = r.where === "subject" ? "subject " : "";
  if (r.removed && r.added) return [{ text: lead }, { removed: r.removed }, { text: " to " }, { added: r.added }];
  if (r.added) return [{ text: `${lead}added ` }, { added: r.added }];
  return [{ text: `${lead}removed ` }, { removed: r.removed }];
}

/**
 * The pieces of the "Changed:" line (fix round 2). Each change reads as a
 * phrase: "“x” to “y”", "added “y”" or "removed “x”". A comma joins the
 * phrases, and one stop ends them. The people go in sentences of their own:
 * "Now also to a." and "No longer to b." No semicolon, and no separator with
 * nothing on one side of it.
 */
export function linePieces(c: Changes): LinePiece[] {
  const out: LinePiece[] = [{ text: CHANGED_LEAD }];
  const t = (text: string) => out.push({ text });
  if (c.none) {
    t("no words. This draft has the words of your last version.");
    return out;
  }
  const phrases: LinePiece[][] = c.coarse
    ? [[{ text: "most of the text" }]]
    : c.runs.length ? c.runs.map(runPieces) : [[{ text: "the people" }]];
  phrases.forEach((p, i) => {
    if (i > 0) t(", ");
    out.push(...p);
  });
  if (!c.coarse && c.more) t(`, and ${c.more} more ${c.more === 1 ? "change" : "changes"}`);
  t(".");
  if (c.people.added.length) t(` Now also to ${c.people.added.join(", ")}.`);
  if (c.people.removed.length) t(` No longer to ${c.people.removed.join(", ")}.`);
  return out.filter((p) => !("text" in p) || p.text !== "");
}

/** The "Changed:" line as plain words, for its accessible name and tests. */
export function changedText(c: Changes): string {
  return linePieces(c)
    .map((p) => ("text" in p ? p.text : `“${"removed" in p ? p.removed : p.added}”`))
    .join("");
}

/**
 * The note on the people that start unticked because the member unticked
 * them on the draft before (rule 7), or "". It reads the server's names
 * (`keptOutOf`), so it shows on a card with no "Changed:" line too.
 */
export function keptOutText(names: readonly string[]): string {
  return names.length ? `Still unticked from your last draft: ${names.join(", ")}.` : "";
}
