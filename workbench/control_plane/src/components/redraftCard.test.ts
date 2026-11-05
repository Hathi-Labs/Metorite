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
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ConfirmationCard from "@/components/ConfirmationCard";
import NewMailCardForm from "@/components/NewMailCardForm";
import {
  EMPTY_CONFIRMATIONS,
  REVISED_ANSWER,
  confirmationReducer,
  liveCards,
  type ConfirmationAction,
  type ConfirmationQueueState,
} from "@/lib/confirmationQueue";
import {
  CARD_DRAFT_KEYS,
  UNCHANGED_LINE,
  UPDATING_LINE,
  cardDraftOf,
  changedText,
  changesOf,
  changingCard,
  changingLine,
  draftLabel,
  pickCardDrafts,
  snapshotOf,
  withUnticked,
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
      people: { ticked: ["kim@contoso.test", "geo@acme.test"], unticked: [] },
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
      people: { ticked: ["kim@contoso.test", "geo@acme.test"], unticked: [] },
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
    expect(changedText(c)).toBe("Changed: subject added “for May”; removed “please find”; added “are”.");
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
  });
});

describe("P1: the people of a redraft", () => {
  const revising = { ...FORM, body: "Hi Kim, the rates.", revises: OLD, draft: 2 };
  const sentOff = {
    subject: "Rates", body: "My text.", to: ["kim@contoso.test"], cc: [] as string[], bcc: [] as string[],
  };

  it("starts Draft 2 with a person the member unticked still unticked, and says so", () => {
    // FAILS WITHOUT THE FIX: the gateway ticks the always-copy colleague
    // again, and one click sent the mail to a person the member removed.
    // Mutation: drop `withUnticked` in `redrafted` → red.
    const state = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "memberVersion", key: OLD, version: sentOff },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
      { type: "requested", value: mailCard(NEW, revising) },
    ]);
    const card = state.cards[0];
    expect(startTicks(card.reply!).reply_all).toEqual(["kim@contoso.test"]);
    expect(card.before?.people).toEqual({ ticked: ["kim@contoso.test"], unticked: ["geo@acme.test"] });
    // The card still OFFERS the colleague, so one tick brings the colleague back.
    expect(card.reply?.recipients.reply_all.cc).toEqual(["geo@acme.test"]);
    const c = changesOf(card.before!, {
      subject: card.reply!.subject, body: card.reply!.body,
      people: { all: ["kim@contoso.test", "geo@acme.test"], ticked: startTicks(card.reply!).reply_all },
    });
    expect(c.people.keptOut).toEqual(["geo@acme.test"]);
    expect(changedText(c)).toContain("Still unticked: geo@acme.test.");
  });

  it("only unticks: it never ticks a person and never adds one", () => {
    // Mutation: tick from the member's version in place of narrowing → red.
    const ticked = { ...revising, ticked: { reply: { to: [], cc: [] }, reply_all: { to: ["kim@contoso.test"], cc: [] } } };
    const form = replyFromEvent(ticked, NEW)!;
    expect(startTicks(form).reply_all).toEqual(["kim@contoso.test"]);
    expect(withUnticked(form, [])).toBe(form);
    const narrowed = withUnticked(form, ["kim@contoso.test", "nobody@else.test"]);
    expect(startTicks(narrowed).reply_all).toEqual([]);
    expect(narrowed.recipients.reply_all.to).toEqual(["kim@contoso.test"]);
    expect(narrowed.recipients.reply_all.cc).toEqual(["geo@acme.test"]);
  });

  it("names the people who changed when 'Also email Bob' replaces Kim's card", () => {
    // Mutation: compare the text only → red.
    const bobList = { to: ["bob@acme.test"], cc: [] as string[] };
    const bob = { ...FORM, recipients: { reply: bobList, reply_all: bobList }, revises: OLD, draft: 2 };
    const state = run([
      { type: "requested", value: mailCard(OLD) },
      { type: "memberVersion", key: OLD, version: { subject: FORM.subject, body: FORM.body, to: ["kim@contoso.test"], cc: ["geo@acme.test"], bcc: [] } },
      { type: "resolved", requestId: OLD, answer: REVISED_ANSWER },
      { type: "requested", value: mailCard(NEW, bob) },
    ]);
    const card = state.cards[0];
    const c = changesOf(card.before!, {
      subject: card.reply!.subject, body: card.reply!.body,
      people: { all: ["bob@acme.test"], ticked: startTicks(card.reply!).reply_all },
    });
    expect(c.none).toBe(false);
    expect(c.people).toEqual({ added: ["bob@acme.test"], removed: ["kim@contoso.test", "geo@acme.test"], keptOut: [] });
    expect(changedText(c)).toBe("Changed: people added bob@acme.test; people removed kim@contoso.test, geo@acme.test.");
    const html = renderToStaticMarkup(createElement(NewMailCardForm, {
      form: card.reply!, before: card.before, onAnswer: () => {}, onDiscard: () => {}, armed: true,
    }));
    expect(html).toContain("data-redraft-people");
    expect(html).toContain("bob@acme.test");
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
  it("counts the cards that wait, and not the slot", () => {
    // Mutation: count `tabs.length` in the pill → red.
    const src = read("components/ui/CardTabs.tsx");
    expect(src).toContain("const waiting = tabs.filter((t) => !t.updating).length;");
    expect(src).toContain("tabsCount(waiting)");
    expect(src).not.toContain("tabsCount(total)");
    expect(read("components/ConfirmationQueue.tsx")).toContain("...(card.redraft ? { updating: true } : {}),");
  });
});
