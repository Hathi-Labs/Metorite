/**
 * A redraft through the chat keeps the member's edits (`email_app_master_plan.md`
 * §18.11, owner 2026-11-05). The client half.
 *
 * The rules this file holds (R7). Each test names the mutation that turns it red.
 *
 * 1. The snapshot on a steer: an edited new-mail card gives the member's
 *    version, and each steer of the chat carries the newest one.
 * 2. The slot: a new-mail card that a chat message closed stays as a slot
 *    with no buttons, "Updating the draft…". A card of another kind closes
 *    as before.
 * 3. The label: the card that revises it takes its slot, in its tab, as
 *    "Draft <n>".
 * 4. The "Changed:" line: the client computes it from its own copy of the
 *    member's last version and the new text.
 * 5. The end with no new card: "The draft did not change.", then the slot
 *    closes.
 * 6. Rule 7 (fix round 2): the GATEWAY starts each person the member
 *    unticked unticked, and names them. The client draws that start and the
 *    "Still unticked" note on every path: a slot, no slot, two revised cards,
 *    a remount and a replay. It ticks and unticks nobody.
 * 7. Fix round 3: a card with no link takes only the slot of a card to the
 *    same people (`sharesTo`, in any case). A new mail to another person
 *    shows as its own card, and a slot that no card takes ends at the run's
 *    end.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ConfirmationCard from "@/components/ConfirmationCard";
import { cardTab } from "@/components/ConfirmationQueue";
import NewMailCardForm from "@/components/NewMailCardForm";
import CardTabs from "@/components/ui/CardTabs";
import { planCardRedraw } from "@/lib/cardReplay";
import {
  CONFIRMATION_RESOLVED,
  EMPTY_CONFIRMATIONS,
  REVISED_ANSWER,
  confirmationReducer,
  liveCards,
  type ConfirmationAction,
  type ConfirmationQueueState,
} from "@/lib/confirmationQueue";
import {
  CARD_DRAFT_KEYS,
  MAX_CARD_DRAFTS,
  UNCHANGED_LINE,
  UPDATING_LINE,
  cardDraftOf,
  changedText,
  changesOf,
  changingCard,
  changingLine,
  draftLabel,
  keptOutOf,
  keptOutText,
  linePieces,
  pickCardDrafts,
  sharesTo,
  snapshotOf,
  type KeptDraft,
} from "@/lib/redraftCard";
import { replyFromEvent, startTicks, toggleTick } from "@/lib/replyCard";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8").replace(/\r\n/g, "\n");

const OLD = "a".repeat(32);
const NEW = "b".repeat(32);
const TASK = "c".repeat(32);
const PEOPLE = { to: ["kim@contoso.test"], cc: ["geo@acme.test"] };
const FORM = {
  kind: "new",
  message_id: "",
  mailbox: "Work · jane@acme.test",
  subject: "Rates",
  body: "Hi Kim,\n\nThe rates are attached.",
  recipients: { reply: PEOPLE, reply_all: PEOPLE },
  files: [],
  notices: [],
  note: "",
};

const mailCard = (id: string, reply: Record<string, unknown> = FORM) => ({
  title: "Email to kim@contoso.test",
  detail: "From Work · Rates",
  request_id: id,
  reply,
});
const taskCard = { title: "Create this task?", detail: "«Fix the extruder»", request_id: TASK };

function run(actions: ConfirmationAction[], start: ConfirmationQueueState = EMPTY_CONFIRMATIONS) {
  return actions.reduce(confirmationReducer, start);
}

const kept = (entries: [string, KeptDraft][]) => new Map(entries);

describe("the snapshot on a steer", () => {
  const form = replyFromEvent(FORM, OLD)!;
  const ticks = startTicks(form);

  it("is the member's version after an edit, and none before one", () => {
    // Mutation: send a snapshot of a card that nobody edited → red.
    expect(cardDraftOf(form, OLD, { subject: form.subject, body: form.body, ticks })).toBeNull();
    const draft = cardDraftOf(form, OLD, { subject: "Rates for May", body: "Shorter.", ticks });
    expect(draft).toEqual({
      request_id: OLD, subject: "Rates for May", body: "Shorter.",
      to: ["kim@contoso.test"], cc: ["geo@acme.test"], bcc: [],
    });
    // The keys are the gateway's (`redraft.CardDraft`), no more and no fewer.
    expect(Object.keys(draft!).sort()).toEqual([...CARD_DRAFT_KEYS].sort());
    // An untick alone is an edit too, and the version names only the ticked.
    const off = toggleTick(ticks, true, "geo@acme.test");
    expect(cardDraftOf(form, OLD, { subject: form.subject, body: form.body, ticks: off })?.cc).toEqual([]);
    // A reply card and a card id that the gateway did not mint give none.
    expect(cardDraftOf({ ...form, kind: undefined }, OLD, { subject: "x", body: "y", ticks })).toBeNull();
    expect(cardDraftOf(form, "r-1", { subject: "x", body: "y", ticks })).toBeNull();
  });

  it("a steer takes a version of EACH open new-mail card, edited or not", () => {
    // P2. Mutation: take only the newest edit, or skip an unedited card → red.
    const state = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "requested", value: mailCard(NEW) },
      { type: "requested", value: taskCard },
    ]);
    const a = cardDraftOf(replyFromEvent(FORM, OLD)!, OLD, { subject: "A", body: "a", ticks })!;
    const b = cardDraftOf(replyFromEvent(FORM, NEW)!, NEW, { subject: "B", body: "b", ticks })!;
    // Both edited: the card that a steer closes keeps its OWN edit.
    expect(pickCardDrafts(state.cards, kept([[OLD, { draft: a, seq: 1 }], [NEW, { draft: b, seq: 2 }]]))).toEqual([a, b]);
    // One edited, one at its start: the start goes too, so it replaces an old edit.
    const newForm = replyFromEvent(FORM, NEW)!;
    const start = snapshotOf(newForm, NEW, { subject: FORM.subject, body: FORM.body, ticks: startTicks(newForm) })!;
    expect(pickCardDrafts(state.cards, kept([[OLD, { draft: a, seq: 3 }]]))).toEqual([a, start]);
    expect(start.body).toBe(FORM.body);
    // A task card gives none. A slot's version goes no more.
    expect(pickCardDrafts(run([{ type: "requested", value: taskCard }]).cards, kept([]))).toEqual([]);
    const slotted = run([{ type: "resolved", requestId: OLD, answer: REVISED_ANSWER }], state);
    expect(pickCardDrafts(slotted.cards, kept([[OLD, { draft: a, seq: 9 }]]))).toEqual([start]);
  });

  it("each steer of the chat carries it, and the chat proxy passes it on", () => {
    // Mutation: drop any hop of the field → red.
    const hook = read("hooks/useAgentChat.ts");
    expect(hook).toContain("const draft = cardDraftRef.current?.() ?? [];");
    expect(hook).toContain("...(draft.length ? { cardDraft: draft } : {}),");
    const chat = read("components/AgentChat.tsx");
    expect(chat).toContain("cardDraft: takeCardDraft,");
    expect(chat).toContain("onDraftChange={onCardDraft}");
    expect(chat).toContain("pickCardDrafts(cardsRef.current, cardDraftsRef.current)");
    const route = read("app/api/agent/chat/route.ts");
    expect(route).toContain("...(cardDraft ? { card_draft: cardDraft } : {}),");
    // The form reports each edit, and the queue passes it up by the card's key.
    const form = read("components/NewMailCardForm.tsx");
    expect(form).toContain('report.current?.(cardDraftOf(form, form.requestId ?? "", { subject, body, ticks }));');
    expect(read("components/ConfirmationQueue.tsx")).toContain("onDraftChange(card.key, d)");
  });
});

describe("the slot of a revised card", () => {
  it("a new-mail card that a chat message closed stays, with no buttons", () => {
    // Mutation: remove the card on REVISED → red.
    const state = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
    ]);
    expect(state.cards.map((c) => [c.key, c.redraft])).toEqual([[OLD, "updating"]]);
    // It waits on nobody, and a replay of the old card does not reopen it.
    expect(liveCards(state.cards)).toEqual([]);
    expect(run([{ type: "requested", value: mailCard(OLD) }], state).cards[0].redraft).toBe("updating");
    const html = renderToStaticMarkup(createElement(ConfirmationCard, {
      title: "Email to kim@contoso.test", reply: state.cards[0].reply, slot: "updating",
      onApprove: () => {}, onReject: () => {},
    }));
    expect(html).toContain(UPDATING_LINE);
    expect(html).toContain('data-redraft-slot="updating"');
    expect(html).toContain('role="status"');
    expect(html).not.toContain("<button");
    expect(html).not.toContain("<textarea");
  });

  it("two cards wait and one steer closes both: only the new-mail card keeps a slot", () => {
    // A task card beside the new-mail card. "make it shorter" names neither,
    // so the server closes both (`_steer_target`). Mutation: keep a slot for
    // a card of any kind → red.
    const state = run([
      { type: "requested", value: taskCard },
      { type: "requested", value: mailCard(OLD) },
      { type: "resolved", requestId: TASK, answer: REVISED_ANSWER },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
    ]);
    expect(state.cards.map((c) => [c.key, c.redraft])).toEqual([[OLD, "updating"]]);
    // A plain answer closes a new-mail card as before.
    const answered = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "resolved", requestId: OLD, answer: "APPROVE" },
    ]);
    expect(answered.cards).toEqual([]);
  });
});

describe("the new draft takes the slot, as Draft <n>", () => {
  const revising = { ...FORM, subject: "Rates", body: "Hi Kim, the rates.", revises: OLD, draft: 2 };

  it("replaces the slot in place, in the same tab, and keeps the member's version", () => {
    // Mutation: append the new card in place of the slot → red.
    const state = run([
      { type: "requested", value: taskCard },
      { type: "requested", value: mailCard(OLD) },
      { type: "memberVersion", key: OLD, version: { subject: "Rates for May", body: "My text." } },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
      { type: "requested", value: mailCard(NEW, revising) },
    ]);
    expect(state.cards.map((c) => c.key)).toEqual([TASK, NEW]);
    const card = state.cards[1];
    expect(card.redraft).toBeUndefined();
    expect(card.reply?.draft).toBe(2);
    expect(card.before).toEqual({
      subject: "Rates for May", body: "My text.",
      people: { ticked: ["kim@contoso.test", "geo@acme.test"] },
    });
    // A replay of the new card keeps what the client knows of it.
    expect(run([{ type: "requested", value: mailCard(NEW, revising) }], state).cards[1].before)
      .toEqual(card.before);
  });

  it("with no edit, the member's last version is the old card's own text", () => {
    const state = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
      { type: "requested", value: mailCard(NEW, revising) },
    ]);
    expect(state.cards[0].before).toEqual({
      subject: FORM.subject, body: FORM.body,
      people: { ticked: ["kim@contoso.test", "geo@acme.test"] },
    });
  });

  it("a card that names no slot of the queue is a new card", () => {
    const state = run([{ type: "requested", value: mailCard(NEW, revising) }]);
    expect(state.cards.map((c) => c.key)).toEqual([NEW]);
    expect(state.cards[0].before).toBeUndefined();
  });

  it("reads the link only from a new-mail form, and only a good one", () => {
    // Mutation: take any `revises` value → red.
    expect(replyFromEvent(revising, NEW)?.revises).toBe(OLD);
    expect(replyFromEvent({ ...revising, revises: "x" }, NEW)?.revises).toBeUndefined();
    expect(replyFromEvent({ ...revising, draft: 1 }, NEW)?.draft).toBeUndefined();
    expect(replyFromEvent({ ...revising, draft: 2.5 }, NEW)?.draft).toBeUndefined();
    const reply = { ...revising, kind: undefined, message_id: "m-1" };
    expect(replyFromEvent(reply, NEW)?.revises).toBeUndefined();
  });

  it("draws the label from Draft 2 on, and none for a first draft", () => {
    expect(draftLabel(2)).toBe("Draft 2");
    expect(draftLabel(1)).toBe("");
    expect(draftLabel(undefined)).toBe("");
    const noop = () => {};
    const two = renderToStaticMarkup(createElement(NewMailCardForm, {
      form: replyFromEvent(revising, NEW)!, onAnswer: noop, onDiscard: noop, armed: true,
    }));
    expect(two).toContain('data-redraft-label=""');
    expect(two).toContain("Draft 2");
    const one = renderToStaticMarkup(createElement(NewMailCardForm, {
      form: replyFromEvent(FORM, OLD)!, onAnswer: noop, onDiscard: noop, armed: true,
    }));
    expect(one).not.toContain("data-redraft-label");
  });
});

describe("the Changed: line", () => {
  it("names the words that went and came, from the client's own copy", () => {
    // Mutation: compare the new text with itself, or drop a side → red.
    const c = changesOf(
      { subject: "Rates", body: "Hi Kim, please find the rates attached." },
      { subject: "Rates for May", body: "Hi Kim, the rates are attached." },
    );
    expect(c.none).toBe(false);
    expect(c.runs[0]).toEqual({ where: "subject", removed: "", added: "for May" });
    expect(c.runs.slice(1)).toEqual([
      { where: "text", removed: "please find", added: "" },
      { where: "text", removed: "", added: "are" },
    ]);
    expect(changedText(c)).toBe("Changed: subject added “for May”, removed “please find”, added “are”.");
    // The same words, with other spaces: no change.
    const same = changesOf({ subject: "Rates", body: "a  b\nc" }, { subject: "Rates", body: "a b c" });
    expect(same.none).toBe(true);
  });

  it("caps the line and says how many more changes there are", () => {
    const c = changesOf({ subject: "", body: "a x b x c x d x e x f" }, { subject: "", body: "a y b y c y d y e y f" });
    expect(c.runs).toHaveLength(4);
    expect(c.more).toBe(1);
    expect(changedText(c)).toContain(", and 1 more change.");
  });

  it("the card draws it with the removed words struck and the new words marked", () => {
    const noop = () => {};
    const html = renderToStaticMarkup(createElement(NewMailCardForm, {
      form: replyFromEvent({ ...FORM, body: "Hi Kim, the rates are here.", revises: OLD, draft: 2 }, NEW)!,
      before: { subject: "Rates", body: "Hi Kim, the rates are attached." },
      onAnswer: noop, onDiscard: noop, armed: true,
    }));
    expect(html).toContain('data-redraft-changed=""');
    expect(html).toMatch(/<del[^>]*>attached\.<\/del>/);
    expect(html).toMatch(/<mark[^>]*>here\.<\/mark>/);
    // The line uses the house tokens only: no colour of its own.
    expect(html).not.toMatch(/#[0-9a-f]{3,6}\b|rgb\(|hsl\(/i);
  });

  it("uses the one word diff of the product", () => {
    expect(read("lib/redraftCard.ts")).toContain('import { diffKeys } from "@/app/knowledge/lib/wordDiff";');
  });
});

describe("the end with no new card", () => {
  it("the slot says the draft did not change, and then closes", () => {
    // Mutation: drop the slot at the run's end, or never close it → red.
    const ended = run([
      { type: "requested", value: taskCard },
      { type: "requested", value: mailCard(OLD) },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
      { type: "runFinalized" },
    ]);
    expect(ended.cards.map((c) => [c.key, c.redraft])).toEqual([[OLD, "unchanged"]]);
    const html = renderToStaticMarkup(createElement(ConfirmationCard, {
      title: "Email to kim@contoso.test", reply: ended.cards[0].reply, slot: "unchanged",
      onApprove: () => {}, onReject: () => {},
    }));
    expect(html).toContain(UNCHANGED_LINE);
    expect(html).not.toContain("<button");
    expect(run([{ type: "closeUnchanged" }], ended).cards).toEqual([]);
    // The chat closes it a moment later, as a card that closed with no answer.
    const chat = read("components/AgentChat.tsx");
    expect(chat).toContain('setTimeout(() => dispatchConfirmation({ type: "closeUnchanged" }), UNCHANGED_CLOSE_MS)');
    expect(chat).toMatch(/type: "resolved", requestId: String\(rid\),\s*\.\.\.\(typeof answer === "string" \? \{ answer \} : \{\}\)/);
  });
});

describe("the composer names the email that a message changes", () => {
  it("shows Changing: Email to <name> while a new-mail card is open", () => {
    // Mutation: show the line for a slot, or for a card of another kind → red.
    const open = run([{ type: "requested", value: taskCard }, { type: "requested", value: mailCard(OLD) }]);
    const card = changingCard(open.cards);
    expect(card?.key).toBe(OLD);
    expect(changingLine(card!)).toBe("Changing: Email to Kim");
    expect(changingCard(run([{ type: "requested", value: taskCard }]).cards)).toBeNull();
    const slotted = run([{ type: "resolved", requestId: OLD, answer: REVISED_ANSWER }], open);
    expect(changingCard(slotted.cards)).toBeNull();
    const chat = read("components/AgentChat.tsx");
    expect(chat).toContain("const changing = changingCard(confirmations.cards);");
    expect(chat).toContain('data-redraft-changing=""');
    expect(chat).toContain("{changingLine(changing)}");
    // At 390 px (fix round 2): the status starts at its own width and can
    // shrink, so a long step cuts its words and the ring stays in the row. The
    // name shrinks four times as fast, so it is cut first. Mutation: give the
    // status `min-w-fit` again (the ring leaves the row), or `flex-1` (the
    // name squeezes the status to nothing) → red.
    expect(chat).toContain('<div className="flex min-w-0 flex-auto items-center gap-2">');
    expect(chat).toContain('className="min-w-0 shrink-4 truncate text-[10px] text-primary"');
    expect(chat).not.toContain("min-w-fit flex-1");
  });
});

describe("rule 7: the people the member unticked stay unticked, on every path", () => {
  // The gateway's Draft 2 (fix round 2): the tool names Geo again (an
  // always-copy colleague), and the gateway starts Geo unticked and names Geo.
  const KIM = "kim@contoso.test";
  const GEO = "geo@acme.test";
  const start = { to: [KIM], cc: [] as string[] };
  const serverTicks = { ticked: { reply: start, reply_all: start }, unticked: [GEO] };
  const revising = { ...FORM, body: "Hi Kim, the rates.", revises: OLD, draft: 2, ...serverTicks };
  const unlinked = { ...FORM, body: "Hi Kim, the rates.", ...serverTicks };
  const sentOff = { subject: "Rates", body: "My text.", to: [KIM], cc: [] as string[], bcc: [] as string[] };
  const noop = () => {};
  const draw = (form: Parameters<typeof NewMailCardForm>[0]["form"], before?: Parameters<typeof NewMailCardForm>[0]["before"]) =>
    renderToStaticMarkup(createElement(NewMailCardForm, { form, before, onAnswer: noop, onDiscard: noop, armed: true }));
  // The tag of a person's tick box, as the card draws it, or "".
  const box = (html: string, who: string) =>
    html.split("<").find((tag) => tag.startsWith("input") && tag.includes(`aria-label="Send to ${who}"`)) ?? "";

  it("reads the server's start and names from the form, and drops a bad name", () => {
    // Mutation: stop reading `unticked` in `replyFromEvent` → red.
    const form = replyFromEvent(revising, NEW)!;
    expect(startTicks(form).reply_all).toEqual([KIM]);
    expect(form.unticked).toEqual([GEO]);
    expect(keptOutOf(form)).toEqual([GEO]);
    // The card still OFFERS Geo, so one tick brings Geo back.
    expect(form.recipients.reply_all.cc).toEqual([GEO]);
    // A name that is not a person of the card, or that starts ticked, is dropped.
    expect(replyFromEvent({ ...revising, unticked: ["x@evil.example", KIM] }, NEW)?.unticked).toBeUndefined();
    // With no ticked start, each person starts ticked, so no name can stand.
    expect(replyFromEvent({ ...FORM, unticked: [GEO] }, NEW)?.unticked).toBeUndefined();
  });

  it("the slot path: Draft 2 starts Geo unticked, and the card says so", () => {
    // FAILS WITHOUT THE FIX: Geo was ticked again, and one click sent to Geo.
    // Mutation: draw the note only with a `before`, or drop `KeptOutLine` → red.
    const state = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "memberVersion", key: OLD, version: sentOff },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
      { type: "requested", value: mailCard(NEW, revising) },
    ]);
    const card = state.cards[0];
    expect(startTicks(card.reply!).reply_all).toEqual([KIM]);
    expect(card.before?.people).toEqual({ ticked: [KIM] });
    const html = draw(card.reply!, card.before);
    expect(html).toContain(`Still unticked from your last draft: ${GEO}.`);
    // The box of each person, as the card draws it: Kim ticked, Geo not.
    expect(box(html, GEO)).not.toBe("");
    expect(box(html, GEO)).not.toContain('checked=""');
    expect(box(html, KIM)).toContain('checked=""');
    // The people did not change: the member's version had Kim only.
    const c = changesOf(card.before!, {
      subject: card.reply!.subject, body: card.reply!.body,
      people: { all: [KIM, GEO], ticked: startTicks(card.reply!).reply_all },
    });
    expect(c.people).toEqual({ added: [], removed: [] });
    expect(changedText(c)).not.toContain(GEO);
  });

  it("the client ticks and unticks nobody: the ticks are the server's", () => {
    // The member unticked Geo, but this server answer starts Geo ticked (a
    // fault kept nothing). The client draws the server's start as it is: one
    // source of ticks. Mutation: narrow the ticks in `redrafted` again → red.
    const plain = { ...FORM, body: "Hi Kim, the rates.", revises: OLD, draft: 2 };
    const state = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "memberVersion", key: OLD, version: sentOff },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
      { type: "requested", value: mailCard(NEW, plain) },
    ]);
    expect(state.cards[0].reply).toEqual(replyFromEvent(plain, NEW));
    expect(keptOutOf(state.cards[0].reply)).toEqual([]);
  });

  it("two revised cards (many): each new card takes the slot of its own people, with the server's ticks", () => {
    // Path 1 of the review. One message closed Kim's card and Bob's card. The
    // gateway linked neither new card. Fix round 3: Bob's draft takes Bob's
    // slot, though Kim's slot waits first. Mutation: append an unlinked card
    // in place of taking a slot, or take the first slot that waits → red.
    const bobList = { to: ["bob@contoso.test"], cc: [] as string[] };
    const bob = { ...FORM, recipients: { reply: bobList, reply_all: bobList } };
    const THIRD = "d".repeat(32);
    const FOURTH = "e".repeat(32);
    const slots = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "requested", value: mailCard(NEW, bob) },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
      { type: "resolved", requestId: NEW, answer: REVISED_ANSWER },
    ]);
    expect(slots.cards.map((c) => c.redraft)).toEqual(["updating", "updating"]);
    const one = run([{ type: "requested", value: mailCard(THIRD, bob) }], slots);
    expect(one.cards.map((c) => [c.key, c.redraft])).toEqual([[OLD, "updating"], [THIRD, undefined]]);
    const both = run([{ type: "requested", value: mailCard(FOURTH, unlinked) }], one);
    expect(both.cards.map((c) => [c.key, c.redraft])).toEqual([[FOURTH, undefined], [THIRD, undefined]]);
    const kim = both.cards[0];
    expect(startTicks(kim.reply!).reply_all).toEqual([KIM]);
    expect(kim.before).toBeUndefined();
    const html = draw(kim.reply!);
    expect(html).toContain(`Still unticked from your last draft: ${GEO}.`);
    expect(html).not.toContain("data-redraft-changed");
    expect(box(html, GEO)).not.toBe("");
    expect(box(html, GEO)).not.toContain('checked=""');
  });

  it("a remount: the card log draws Draft 2 again, with Geo still unticked", () => {
    // Path 2 of the review. The reducer starts empty. The log closes the old
    // card with no answer, so it takes no slot, and the member's `sent` is
    // gone. The server's form still starts Geo unticked. Mutation: drop the
    // server's `unticked` and `ticked` from the form → red.
    const log = [
      { name: "confirmation_requested", value: mailCard(OLD) },
      { name: CONFIRMATION_RESOLVED, value: { request_id: OLD, answer: REVISED_ANSWER } },
      { name: "confirmation_requested", value: mailCard(NEW, revising) },
    ];
    const actions: ConfirmationAction[] = [];
    for (const c of planCardRedraw(log)) {
      actions.push({ type: "requested", value: c.value });
      if (!c.waiting) actions.push({ type: "resolved", requestId: c.requestId, ...(c.answer ? { answer: c.answer } : {}) });
    }
    const state = run(actions);
    expect(state.cards.map((c) => c.key)).toEqual([NEW]);
    const card = state.cards[0];
    expect(startTicks(card.reply!).reply_all).toEqual([KIM]);
    const html = draw(card.reply!, card.before);
    expect(html).toContain("Draft 2");
    expect(html).toContain(`Still unticked from your last draft: ${GEO}.`);
    expect(box(html, GEO)).not.toBe("");
    expect(box(html, GEO)).not.toContain('checked=""');
  });

  it("a replay of Draft 2 in the same mount keeps the start, the note and the line", () => {
    // P3 of the review: the replay put the server's form back and the line
    // said "people added geo". The server's form IS the start now. Mutation:
    // let a replay drop `before` → red.
    const state = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "memberVersion", key: OLD, version: sentOff },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
      { type: "requested", value: mailCard(NEW, revising) },
      { type: "requested", value: mailCard(NEW, revising) },
    ]);
    const card = state.cards[0];
    expect(startTicks(card.reply!).reply_all).toEqual([KIM]);
    expect(card.before?.people).toEqual({ ticked: [KIM] });
    const html = draw(card.reply!, card.before);
    expect(html).toContain(`Still unticked from your last draft: ${GEO}.`);
    expect(html).not.toContain(`Now also to ${GEO}`);
  });

  it("names the people who changed when 'Also email Bob' replaces Kim's card", () => {
    // Mutation: compare the text only → red.
    const bobList = { to: ["bob@acme.test"], cc: [] as string[] };
    const bob = { ...FORM, recipients: { reply: bobList, reply_all: bobList }, revises: OLD, draft: 2 };
    const state = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "memberVersion", key: OLD, version: { subject: FORM.subject, body: FORM.body, to: [KIM], cc: [GEO], bcc: [] } },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
      { type: "requested", value: mailCard(NEW, bob) },
    ]);
    const card = state.cards[0];
    const c = changesOf(card.before!, {
      subject: card.reply!.subject, body: card.reply!.body,
      people: { all: ["bob@acme.test"], ticked: startTicks(card.reply!).reply_all },
    });
    expect(c.none).toBe(false);
    expect(c.people).toEqual({ added: ["bob@acme.test"], removed: [KIM, GEO] });
    expect(changedText(c)).toBe(`Changed: the people. Now also to bob@acme.test. No longer to ${KIM}, ${GEO}.`);
    expect(draw(card.reply!, card.before)).toContain("Now also to bob@acme.test.");
  });
});

describe("the Changed: line reads cleanly (fix round 2)", () => {
  const strip = (html: string) => html.replace(/<[^>]+>/g, "").replace(/&quot;/g, '"').replace(/&amp;/g, "&");

  it("the card's words are the words of its accessible name, with no semicolon", () => {
    // The review saw "The ; rates are": a separator beside a marked word with
    // padding, and no word of the change between. Mutation: join the pieces
    // with "; ", or pad the mark again → red.
    const c = changesOf(
      { subject: "Rates for May", body: "Hi Kim,\n\nPlease find the rates for May attached. Call me on Friday." },
      { subject: "Rates for May", body: "Hi Kim,\n\nThe May rates are attached. Call me on Friday." },
    );
    const label = changedText(c);
    expect(label).not.toContain(";");
    expect(label).not.toMatch(/ [,.]/);
    expect(label.startsWith("Changed: ")).toBe(true);
    const html = renderToStaticMarkup(createElement(NewMailCardForm, {
      form: replyFromEvent({ ...FORM, body: "Hi Kim,\n\nThe May rates are attached. Call me on Friday.", revises: OLD, draft: 2 }, NEW)!,
      before: { subject: FORM.subject, body: "Hi Kim,\n\nPlease find the rates for May attached. Call me on Friday." },
      onAnswer: () => {}, onDiscard: () => {}, armed: true,
    }));
    const line = html.match(/<p[^>]*data-redraft-changed=""[^>]*>([\s\S]*?)<\/p>/)?.[1] ?? "";
    expect(strip(line)).toBe(label.replace(/[“”]/g, ""));
    expect(line).not.toContain("px-0.5");
    // Each phrase says what happened: "x to y", "added y" or "removed x". A
    // separator always has a phrase on each side of it.
    const pieces = linePieces(c);
    pieces.forEach((p, i) => {
      if (!("text" in p) || p.text !== ", ") return;
      expect(i).toBeGreaterThan(1);
      expect(pieces[i + 1] && !("text" in pieces[i + 1] && /^[,.]/.test((pieces[i + 1] as { text: string }).text))).toBe(true);
    });
    expect(label).not.toMatch(/[,;] *[,.;]/);
  });

  it("the note on the kept unticks is its own sentence", () => {
    expect(keptOutText([])).toBe("");
    expect(keptOutText(["geo@acme.test"])).toBe("Still unticked from your last draft: geo@acme.test.");
  });
});

describe("fix round 3: the slot goes only to a card to the same people", () => {
  // The member unticks Geo on Kim's card, then writes "first email Carol
  // that I am late, then make this shorter". The model draws Carol's card
  // first. The gateway gives it no link, because its To is not Kim's.
  const CAROL = "carol@contoso.test";
  const carolList = { to: [CAROL], cc: ["geo@acme.test"] };
  const carolStart = { to: [CAROL], cc: [] as string[] };
  const carol = {
    ...FORM, subject: "Late", body: "Hi Carol, I am late.", recipients: { reply: carolList, reply_all: carolList },
    ticked: { reply: carolStart, reply_all: carolStart }, unticked: ["geo@acme.test"],
  };
  const kimStart = { to: ["kim@contoso.test"], cc: [] as string[] };
  const redraft = {
    ...FORM, body: "Hi Kim, the rates.", revises: OLD, draft: 2,
    ticked: { reply: kimStart, reply_all: kimStart }, unticked: ["geo@acme.test"],
  };
  const CAROL_ID = "f".repeat(32);

  it("a new mail to another person shows as its own card, and Kim's draft takes Kim's slot", () => {
    // P1 of the review (its label half), and Finding 1 of the verifier.
    // Mutation: let a card with no link take any slot that waits → red.
    const slotted = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "memberVersion", key: OLD, version: { subject: FORM.subject, body: "My text.", to: ["kim@contoso.test"], cc: [], bcc: [] } },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
    ]);
    const first = run([{ type: "requested", value: { ...mailCard(CAROL_ID, carol), title: `Email to ${CAROL}` } }], slotted);
    expect(first.cards.map((c) => [c.key, c.redraft])).toEqual([[OLD, "updating"], [CAROL_ID, undefined]]);
    expect(first.cards[1].before).toBeUndefined();
    const both = run([{ type: "requested", value: mailCard(NEW, redraft) }], first);
    expect(both.cards.map((c) => [c.key, c.redraft])).toEqual([[NEW, undefined], [CAROL_ID, undefined]]);
    expect(both.cards[0].before?.body).toBe("My text.");
    // Carol's card draws no label and no "Changed:" line, and names Geo.
    const noop = () => {};
    const html = renderToStaticMarkup(createElement(NewMailCardForm, {
      form: both.cards[1].reply!, before: both.cards[1].before, onAnswer: noop, onDiscard: noop, armed: true,
    }));
    expect(html).not.toContain("data-redraft-label");
    expect(html).not.toContain("data-redraft-changed");
    expect(html).toContain("Still unticked from your last draft: geo@acme.test.");
  });

  it("the To compare ignores the case, and a card of other people takes no slot", () => {
    // Finding 2 of the verifier. Mutation: compare the case → red.
    const shout = { ...FORM, recipients: { reply: { to: ["KIM@Contoso.TEST"], cc: [] }, reply_all: { to: ["KIM@Contoso.TEST"], cc: [] } } };
    expect(sharesTo(replyFromEvent(FORM, OLD), replyFromEvent(shout, NEW))).toBe(true);
    expect(sharesTo(replyFromEvent(FORM, OLD), replyFromEvent(carol, NEW))).toBe(false);
    expect(sharesTo(replyFromEvent(FORM, OLD), replyFromEvent({ ...shout, kind: undefined, message_id: "m-1" }, NEW))).toBe(false);
    const taken = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
      { type: "requested", value: mailCard(NEW, shout) },
    ]);
    expect(taken.cards.map((c) => [c.key, c.redraft])).toEqual([[NEW, undefined]]);
  });

  it("a slot that no card takes waits for the run's end, then closes with no false line", () => {
    // The member asked for a change, and the run drew only Carol's card. The
    // slot waits while Carol's card waits. At the run's end it closes with no
    // line, because a new mail came. Mutation: keep "Updating the draft…"
    // after the run's end → red.
    const ended = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
      { type: "requested", value: mailCard(CAROL_ID, carol) },
    ]);
    expect(ended.cards.map((c) => c.redraft)).toEqual(["updating", undefined]);
    const after = run([{ type: "runFinalized" }], ended);
    expect(after.cards.some((c) => c.redraft !== undefined)).toBe(false);
  });
});

describe("P3: a slot with a newer card closes with no false line", () => {
  it("closes at the run's end when a new-mail card arrived after it", () => {
    // A new card with no link (two revised cards, or a failed link write).
    // Mutation: drop `newerCard` from `runFinalized` → red.
    const ended = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
      { type: "requested", value: mailCard(NEW) },
      { type: "runFinalized" },
    ]);
    expect(ended.cards.some((c) => c.redraft !== undefined)).toBe(false);
  });

  it("keeps the line when no new-mail card came", () => {
    const ended = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
      { type: "requested", value: taskCard },
      { type: "runFinalized" },
    ]);
    expect(ended.cards.map((c) => [c.key, c.redraft])).toEqual([[OLD, "unchanged"]]);
  });
});

describe("the tab count leaves out the slot", () => {
  const slot = run([
    { type: "requested", value: mailCard(OLD) },
    { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
  ]).cards[0];
  const card = run([{ type: "requested", value: taskCard }]).cards[0];
  const row = (tabs: ReturnType<typeof cardTab>[]) =>
    renderToStaticMarkup(createElement(CardTabs, { uid: "t", index: 0, onGo: () => {}, tabs }));

  it("a slot's tab is marked as updating, and a card's tab is not", () => {
    // Mutation: drop `updating` from `cardTab` → red.
    expect(cardTab(slot).updating).toBe(true);
    expect(cardTab(card).updating).toBeUndefined();
  });

  it("one slot and one card read 1 to approve", () => {
    // Mutation: count `tabs.length` in the row → red.
    const html = row([cardTab(slot), cardTab(card)]);
    expect(html).toContain("1 to approve");
    expect(html).toContain('aria-label="1 approvals to approve.');
  });

  it("two slots and no card never read 0 to approve", () => {
    // Path 1 of the review, before the new drafts come. Mutation: drop the
    // `updating` words of `tabsCount` → red.
    const two = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "requested", value: mailCard(NEW) },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
      { type: "resolved", requestId: NEW, answer: REVISED_ANSWER },
    ]).cards;
    const html = row(two.map(cardTab));
    expect(html).not.toContain("0 to approve");
    expect(html).toContain("Updating 2 drafts");
  });
});

describe("P2: a steer carries at most MAX_CARD_DRAFTS versions, the edited ones first", () => {
  it("puts each edited card first, then a card with kept unticks, and cuts the rest", () => {
    // Mutation: cut in the queue's order, or send more than the cap → red.
    const ids = Array.from({ length: MAX_CARD_DRAFTS + 3 }, (_, i) => i.toString(16).padStart(32, "0"));
    const start = { to: ["kim@contoso.test"], cc: [] as string[] };
    const keptForm = { ...FORM, ticked: { reply: start, reply_all: start }, unticked: ["geo@acme.test"] };
    const last = ids.length - 1;
    const state = run(ids.map((id, i) => ({ type: "requested" as const, value: mailCard(id, i === last - 1 ? keptForm : FORM) })));
    const ticks = startTicks(replyFromEvent(FORM, ids[last])!);
    const edited = cardDraftOf(replyFromEvent(FORM, ids[last])!, ids[last], { subject: "Mine", body: "mine", ticks })!;
    const out = pickCardDrafts(state.cards, kept([[ids[last], { draft: edited, seq: 1 }]]));
    expect(out).toHaveLength(MAX_CARD_DRAFTS);
    expect(out[0]).toEqual(edited);
    expect(out[1].request_id).toBe(ids[last - 1]);
    expect(out[1].cc).toEqual([]);
    expect(out.slice(2).map((d) => d.request_id)).toEqual(ids.slice(0, MAX_CARD_DRAFTS - 2));
  });

  it("the proxy keeps the first ones, in place of dropping the whole list", () => {
    const route = read("app/api/agent/chat/route.ts");
    expect(route).toContain("body.cardDraft.slice(0, MAX_CARD_DRAFTS)");
    expect(route).not.toContain("body.cardDraft.length <= 8");
  });
});
