/**
 * The markup of the waiting dock (WD-1, `projects_ai_chat.md` §24.11).
 *
 * Mutations caught, by test:
 * - an approval and a question draw as two cards, not as tabs of ONE card ->
 *   "one card with tabs";
 * - the questions do not come first (D4) -> "one card with tabs";
 * - the count of a mixed row says "to approve" -> "one card with tabs";
 * - a question card unmounts on a change of tab, so its picks are lost ->
 *   "every question card stays mounted";
 * - one item draws no header row, so Expand and Fold have no place (D1) ->
 *   "the header row";
 * - the Compact view shows Send or Approve for an item it does not show
 *   whole (D3) -> "Compact and Expanded";
 * - the Expanded Send does not name its count -> "Compact and Expanded";
 * - the fold row draws Approve, Reject, Send or Submit -> "the fold row";
 * - the dock has a close control, or no live region -> "the frame";
 * - the flag off draws a dock shape -> "the flag off".
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/chat" }));

import ConfirmationQueue, { pickShown, reconcileShown, type DockQuestion } from "@/components/ConfirmationQueue";
import ElicitationCard from "@/components/ElicitationCard";
import WaitingDock, { WaitingDockLive, type DockRender } from "@/components/WaitingDock";
import { questionTabLabel } from "@/lib/cardTabs";
import { EMPTY_CONFIRMATIONS, REVISED_ANSWER, confirmationReducer, type PendingConfirmation } from "@/lib/confirmationQueue";
import type { DockMode } from "@/lib/waitingDock";

const approval: PendingConfirmation = { key: "c1", title: "Create this task?", detail: "«Fix the extruder»" };
const approval2: PendingConfirmation = { key: "c2", title: "Set this reminder?", detail: "«Call Rajendra»" };
/** An approval with more than its title: fields in its text. */
const fieldsCard: PendingConfirmation = {
  key: "c3",
  title: "Create this task?",
  detail: "«Ship the pack» · Launch",
  context: "project: «Launch»\nname: «Ship the pack»\ndue_at: 2026-12-01",
  fenced: true,
};

const mailForm = (cc: string[], body = "Hi Kim,") => ({
  kind: "new", message_id: "", mailbox: "Work", subject: "Rates", body,
  recipients: {
    reply: { to: ["kim@contoso.test", "lena@contoso.test"], cc },
    reply_all: { to: ["kim@contoso.test", "lena@contoso.test"], cc },
  },
  files: [], notices: [], note: "",
});
const mail = (key: string, cc: string[], body?: string): PendingConfirmation => ({
  key,
  title: "Send this new email?",
  detail: "From Work · Rates",
  reply: mailForm(cc, body) as never,
});

const question = (key: string, text: string): DockQuestion => ({
  key,
  label: questionTabLabel(text),
  title: text,
  render: (slot) =>
    createElement(ElicitationCard, {
      key,
      id: key,
      questions: [{ header: "Pick", question: text, multiSelect: false, options: [{ label: "Yes" }, { label: "No" }] }],
      onSubmit: () => {},
      onDecline: () => {},
      ...slot,
    }),
});

function dock(opts: { cards?: PendingConfirmation[]; questions?: DockQuestion[]; folded?: boolean; count?: number; mode?: DockMode }) {
  return renderToStaticMarkup(
    createElement(WaitingDock, {
      count: opts.count ?? 2,
      phone: false,
      initialFolded: opts.folded,
      children: (d: DockRender) =>
        createElement(ConfirmationQueue, {
          cards: opts.cards ?? [approval],
          onAnswer: () => {},
          questions: opts.questions ?? [question("ask:q1", "Which printer?")],
          folded: d.folded,
          onUnfold: d.unfold,
          dockControls: d.controls,
          dockMode: opts.mode ?? d.mode,
          onExpand: d.expand,
        }),
    }),
  );
}

describe("one card with tabs (WD-D3, D4)", () => {
  it("a question and an approval are two tabs of one card, the question first", () => {
    const html = dock({});
    expect(html.match(/data-card-tabs=/g)?.length).toBe(1);
    expect(html.match(/role="tab"/g)?.length).toBe(2);
    expect(html.indexOf("Question: Which printer?")).toBeLessThan(html.indexOf("Task: Fix the extruder"));
  });

  it("a row with a question says 'N waiting', a row of approvals says 'N to approve'", () => {
    expect(dock({})).toContain("2 waiting");
    expect(dock({})).not.toContain("to approve");
    expect(dock({ cards: [approval, approval2], questions: [] })).toContain("2 to approve");
  });

  it("a question tab has the question icon, and an unopened tab a dot", () => {
    const html = dock({});
    expect(html).toContain("lucide-message-circle-question");
    expect(html).toContain("data-unseen");
  });

  it("every question card stays mounted: a closed tab is hidden, not gone", () => {
    const html = dock({ questions: [question("ask:q1", "Which printer?"), question("ask:q2", "Which day?")] });
    expect(html).toContain('data-dock-question="ask:q1"');
    expect(html).toContain('data-dock-question="ask:q2"');
    expect(html).toContain("Which day?");
    expect(html).toMatch(/<div hidden="" data-dock-question="ask:q2">/);
  });
});

describe("the header row (D1)", () => {
  it("one item draws the header too: its count, its tab, Expand and Fold", () => {
    for (const html of [dock({ cards: [], count: 1 }), dock({ questions: [], count: 1 })]) {
      const row = html.slice(html.indexOf("data-card-tabs="), html.indexOf("data-card-tabs-probe"));
      expect(row).toContain("data-card-tabs-trailing");
      expect(row).toContain('aria-label="Fold the card to one row"');
      expect(row).toContain('aria-label="Expand the card"');
    }
  });

  it("no row of controls sits above the card", () => {
    const html = dock({});
    expect(html.indexOf("data-dock-controls")).toBeGreaterThan(html.indexOf("data-dock-body"));
  });
});

// ── The consent rule over every card shape (24.11, 2026-11-27) ──────────────
//
// Each fixture is the RAW event value of a real gateway card, read through
// the client's own reducer (`confirmationReducer`), as the chat reads it:
// - the held new mail of `held_card.new_mail_form` (file_rows, ticked,
//   classes, marks, max_body, notices, note);
// - the held forward of `held_card._person_card` (a rows card);
// - the reply card of `reply_card.py` (kind reply, original, reply_all);
// - the `create_task` card with its edit form (EAC-2);
// - a rows card of many tasks (WS-46 P13);
// - a plain card with a long detail and fields;
// - a plain card and a new mail that name an earlier draft (task 3978);
// - the slot of a redraft (§18.11).
const GW = {
  newMail: {
    title: "Send this new email?", detail: "From Work · jane@acme.test · Rates for May", request_id: "a".repeat(32),
    reply: {
      kind: "new", mailbox: "Work · jane@acme.test", subject: "Rates for May", body: "Hi Kim,\n\nThe rates are attached.\nOne.\nTwo.",
      max_body: 20000,
      recipients: { reply: { to: ["kim@contoso.test", "lena@contoso.test"], cc: ["geo@acme.test"], bcc: [] }, reply_all: { to: ["kim@contoso.test", "lena@contoso.test"], cc: ["geo@acme.test"], bcc: [] } },
      ticked: { reply: { to: ["kim@contoso.test", "lena@contoso.test"], cc: ["geo@acme.test"], bcc: [] }, reply_all: { to: ["kim@contoso.test", "lena@contoso.test"], cc: ["geo@acme.test"], bcc: [] } },
      classes: { "kim@contoso.test": "known", "lena@contoso.test": "known", "geo@acme.test": "new" },
      marks: { "geo@acme.test": "New address" },
      file_rows: [{ id: "f1", label: "rates-may.pdf", hint: "From the chat", checked: true }],
      notices: ["geo@acme.test: a new address"],
      note: "",
    },
  },
  forward: {
    title: "Forward this email?", detail: "Re: Quote · to 2 people", request_id: "b".repeat(32),
    context: "Each recipient of this email:",
    rows: [
      { id: "r1", label: "To: kim@contoso.test", checked: true, kind: "to" },
      { id: "r2", label: "Cc: geo@acme.test", checked: true, kind: "cc", cls: "new" },
    ],
  },
  reply: {
    title: "Reply to Kim <kim@contoso.test>?", detail: "From Work · jane@acme.test · Re: Quote", request_id: "c".repeat(32),
    reply: {
      message_id: "0b0b0b0b-0000-4000-8000-00000000000b",
      notices: ["Reply goes to desk@contoso.test, not to the sender kim@contoso.test."],
      mailbox: "Work · jane@acme.test", subject: "Re: Quote",
      original: { from: "Kim <kim@contoso.test>", subject: "Quote", received: "2026-10-19T09:00:00Z", excerpt: "Can you confirm?" },
      recipients: { reply: { to: ["kim@contoso.test"], cc: [] }, reply_all: { to: ["kim@contoso.test", "lee@contoso.test"], cc: ["ops@contoso.test"] } },
      reply_all: false, body: "Hi Kim,\n\nYes.", note: "",
    },
  },
  task: {
    title: "Create this task?", detail: "«Ship the pack» · in Launch · status Doing", request_id: "d".repeat(32),
    context: "Check each field, then approve.\nproject: «Launch»\nname: «Ship the pack»\ndue_at: 2026-12-01", fenced: true,
    edit: { kind: "create_task", project_id: "11111111-1111-1111-1111-111111111111", values: {
      due: "2026-12-01", start: "", status: "Doing", important: null, leveraged: false, description: "Ship the pack.", assignees: ["ann@acme.test"],
    } },
  },
  rows: {
    title: "Create 3 tasks in «Ops»?", detail: "one batch", request_id: "e".repeat(32),
    rows: [{ id: "t1", label: "«Fix the extruder»", checked: true }, { id: "t2", label: "«Order filament»", checked: true }, { id: "t3", label: "«Call Rajendra»", checked: false }],
  },
  plainLong: {
    title: "Archive this project?", request_id: "f".repeat(32),
    detail: `«Launch» · ${"a very long detail that names every part of the project ".repeat(6)}`,
    context: "owner: ann@acme.test\ntasks: 42\nnote: Archiving hides the project from each list.",
  },
  plainEarlier: {
    title: "Edit this draft?", detail: "«Rates for May»", request_id: "g".repeat(32),
    earlier: "The chat saved a draft «Rates for May» at 10:02.", earlier_effect: "Delete moves it to Trash.",
  },
};

const GW_MAIL_EARLIER = {
  ...GW.newMail,
  request_id: "h".repeat(32),
  reply: { ...GW.newMail.reply, earlier: "The chat saved a draft «Rates for May» at 10:02." },
};

/** The cards the chat holds for a list of raw events. */
function cardsOf(values: unknown[], revised: string[] = []): PendingConfirmation[] {
  let st = EMPTY_CONFIRMATIONS;
  for (const v of values) st = confirmationReducer(st, { type: "requested", value: v });
  for (const r of revised) st = confirmationReducer(st, { type: "resolved", requestId: r, answer: REVISED_ANSWER });
  return st.cards;
}

/** A control that commits: Send, Save draft, Approve, or an earlier-draft choice. */
const COMMIT = /data-reply-send|data-reply-save|data-confirmation-approve|data-earlier-keep|data-earlier-delete/;

const SHAPES: [string, () => PendingConfirmation[]][] = [
  ["held new mail (file_rows, notices)", () => cardsOf([GW.newMail])],
  ["held forward (rows card)", () => cardsOf([GW.forward])],
  ["reply card", () => cardsOf([GW.reply])],
  ["create_task with its edit form", () => cardsOf([GW.task])],
  ["rows card of many tasks", () => cardsOf([GW.rows])],
  ["plain card with a long detail and fields", () => cardsOf([GW.plainLong])],
  ["plain card that names an earlier draft", () => cardsOf([GW.plainEarlier])],
  ["new mail that names an earlier draft", () => cardsOf([GW_MAIL_EARLIER])],
];

describe("the consent rule: Compact holds no commit, for every card shape (24.11)", () => {
  for (const [name, make] of SHAPES) {
    it(name, () => {
      const cards = make();
      expect(cards.length, "the reducer read the gateway's card").toBe(1);
      const compact = dock({ cards, questions: [], count: 1, mode: "compact" });
      expect(compact).not.toMatch(COMMIT);
      expect(compact).toContain("data-dock-review");
      const expanded = dock({ cards, questions: [], count: 1, mode: "expanded" });
      expect(expanded).toMatch(/data-reply-send|data-confirmation-approve/);
      expect(expanded).not.toContain("data-dock-review");
    });
  }

  it("the slot of a redraft holds no commit and no Review", () => {
    const cards = cardsOf([GW.newMail], [GW.newMail.request_id]);
    expect(cards[0]?.redraft).toBeTruthy();
    const html = dock({ cards, questions: [], count: 0, mode: "compact" });
    expect(html).not.toMatch(COMMIT);
  });

  it("a form stays mounted, hidden and inert, in Compact", () => {
    for (const cards of [cardsOf([GW.newMail]), cardsOf([GW.reply]), cardsOf([GW.task])]) {
      const html = dock({ cards, questions: [], count: 1, mode: "compact" });
      expect(html).toMatch(/<div hidden="" inert="" [^>]*data-dock-full="">/);
    }
  });

  it("Expanded names the count on Send, for a new mail and a reply", () => {
    expect(dock({ cards: cardsOf([GW.newMail]), questions: [], count: 1, mode: "expanded" })).toMatch(/data-reply-send=""[^>]*>[\s\S]*?Send to 3</);
    // The reply card ticks each person of its one list: "Sends to 3 of 3".
    expect(dock({ cards: cardsOf([GW.reply]), questions: [], count: 1, mode: "expanded" })).toMatch(/data-reply-send=""[^>]*>[\s\S]*?Send to 3</);
  });

  it("a question keeps Submit and Not now in Compact", () => {
    const html = dock({ cards: [], count: 1, mode: "compact" });
    expect(html).toContain("data-elicitation-submit");
    expect(html).toContain(">Not now<");
  });

  it("outside the dock each card is as before: its commit, and no Review", () => {
    for (const [, make] of SHAPES) {
      const html = renderToStaticMarkup(createElement(ConfirmationQueue, { cards: make(), onAnswer: () => {} }));
      expect(html).not.toContain("data-dock-review");
      expect(html).not.toContain("data-dock-full");
      expect(html).not.toMatch(/Send to \d/);
    }
  });
});

describe("the fold row (WD-D2, audit rule 2)", () => {
  it("one row: the tabs, the open item and a button that only opens it", () => {
    const html = dock({ folded: true });
    expect(html).toContain('data-waiting-dock="folded"');
    expect(html).toContain("data-dock-fold-row");
    // D4: the question is the first tab.
    expect(html).toContain('data-dock-unfold="question"');
    expect(html).toContain(">Answer<");
    expect(dock({ folded: true, questions: [], count: 1 })).toContain(">Review<");
  });

  it("the row itself holds no Approve, Reject, Send or Submit", () => {
    const html = dock({ folded: true });
    const row = html.slice(html.indexOf("data-dock-fold-row"), html.indexOf('<div hidden=""'));
    expect(row).not.toMatch(/data-confirmation-approve|data-elicitation-submit|>Approve<|>Reject<|>Send<|>Submit</);
  });

  it("the fold keeps each card mounted, hidden, and the header draws once", () => {
    const html = dock({ folded: true, questions: [], count: 1 });
    expect(html).toContain("data-confirmation-card");
    expect(html.match(/data-card-tabs=/g)?.length).toBe(1);
    expect(html).not.toContain("data-dock-controls");
  });
});

describe("WD-D2: no X on a question in the dock", () => {
  it("the dock's question draws Not now and no X", () => {
    const html = dock({ cards: [], count: 1 });
    expect(html).not.toContain("data-not-now-close");
    expect(html).toContain(">Not now<");
  });

  it("outside the dock the question keeps its X", () => {
    const html = renderToStaticMarkup(
      createElement(ElicitationCard, {
        questions: [{ header: "Pick", question: "Which?", multiSelect: false, options: [{ label: "Yes" }] }],
        onSubmit: () => {},
        onDecline: () => {},
      }),
    );
    expect(html).toContain("data-not-now-close");
  });
});

describe("the frame", () => {
  it("a fold control and no close control that drops an item", () => {
    const html = dock({});
    expect(html).toContain("data-dock-fold");
    expect(html).not.toMatch(/aria-label="(Close|Dismiss)/);
  });

  it("one polite live region: the first arrival speaks, and an empty dock says nothing", () => {
    const live = (count: number) => renderToStaticMarkup(createElement(WaitingDockLive, { count }));
    expect(live(2)).toMatch(/role="status" aria-live="polite"[^>]*data-dock-live="">2 items wait for you</);
    expect(live(1)).toContain(">1 item waits for you<");
    expect(live(0)).toMatch(/data-dock-live=""><\/p>/);
    expect(dock({})).not.toContain("data-dock-live");
  });

  it("the chat mounts the region while the flag is on, outside the dock", () => {
    const chat = readFileSync(join(__dirname, "AgentChat.tsx"), "utf-8");
    const host = chat.slice(chat.indexOf("<div ref={dockHostRef}"), chat.indexOf("<form onSubmit={handleSubmit}"));
    expect(host).toContain("<WaitingDockLive count={dockN} />");
    expect(chat).toContain("{dockOn && (");
  });

  it("the body is the one scroll box, with a cap", () => {
    expect(dock({})).toMatch(/data-dock-body="" tabindex="-1" class="overflow-y-auto[^"]*" style="max-height:/);
  });

  it("every control is a native button", () => {
    expect(dock({})).not.toMatch(/role="button"/);
  });
});

describe("the flag off: the queue draws as before", () => {
  it("no dock shape, today's count words, and every person of an email", () => {
    const html = renderToStaticMarkup(createElement(ConfirmationQueue, { cards: [approval, approval2], onAnswer: () => {} }));
    expect(html).not.toContain("data-dock-queue");
    expect(html).toContain("2 to approve");
    expect(html).not.toContain("waiting");
    const email = renderToStaticMarkup(createElement(ConfirmationQueue, { cards: [mail("m4", ["geo@acme.test"])], onAnswer: () => {} }));
    expect(email).toContain('aria-label="Recipients"');
    expect(email).toMatch(/data-reply-send=""[^>]*>[\s\S]*?Send</);
    expect(email).not.toMatch(/Send to \d/);
    expect(email).not.toContain("data-dock-review");
  });
});

describe("the shown tab across both kinds (D4, audit rule 3)", () => {
  it("a new approval never takes the view from the question the member reads", () => {
    const q = new Set(["ask:q1"]);
    const next = reconcileShown(["ask:q1"], [{ key: "ask:q1" }, { key: "c1" }], { key: "ask:q1", index: 0 }, q);
    expect(next.shown.key).toBe("ask:q1");
  });

  it("a new question never takes the view from the approval the member reads", () => {
    const q = new Set(["ask:q1"]);
    const next = reconcileShown(["c1"], [{ key: "ask:q1" }, { key: "c1" }], { key: "c1", index: 0 }, q);
    expect(next.shown.key).toBe("c1");
  });

  it("an Undo of an approval still opens the restored card", () => {
    const q = new Set(["ask:q1"]);
    const next = reconcileShown(["ask:q1", "c2"], [{ key: "ask:q1" }, { key: "c1" }, { key: "c2" }], { key: "ask:q1", index: 0 }, q);
    expect(next.shown.key).toBe("c1");
  });

  it("an answered question hands the view to the next tab, as for an approval", () => {
    const items = [{ key: "ask:q2" }, { key: "c1" }];
    expect(pickShown(items, { key: "ask:q1", index: 0 })).toBe(0);
  });
});

describe("a failed send keeps the question's picks and typed text", () => {
  const q = [{ header: "Depot", question: "Which depot?", multiSelect: false, options: [{ label: "North" }, { label: "South" }] }];

  it("a card that mounts again starts from the chat's draft", () => {
    const html = renderToStaticMarkup(
      createElement(ElicitationCard, {
        questions: q,
        onSubmit: () => {},
        initialDraft: { answers: { Depot: { selected: ["South"] } }, freeformTexts: { Depot: "via the ring road" } },
      }),
    );
    expect(html).toContain("via the ring road");
    expect(html).toMatch(/aria-pressed="true"[^>]*>[\s\S]*?South/);
  });

  it("the chat holds the draft by request id, for both question cards", () => {
    const chat = readFileSync(join(__dirname, "AgentChat.tsx"), "utf-8");
    expect(chat).toContain("{...draftProps(elicitation.requestId)}");
    expect(chat).toContain("{...draftProps(userInput.requestId)}");
    expect(chat).toContain("questionDraftsRef.current.set(rid, d)");
  });
});
