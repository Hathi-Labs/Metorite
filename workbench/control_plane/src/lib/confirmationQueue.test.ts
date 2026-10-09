/**
 * The confirmation queue, the answer POST and the card (2026-10-06).
 *
 * Production: a turn made ten card-gated calls in parallel. The chat had one
 * card slot, so nine cards were never shown and the run never ended. A replay
 * put the slot on a card the member had already approved, every Approve got a
 * 409, and the card came back after each 409.
 *
 * Each block names the rule it fences and a mutation it catches.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import ConfirmationCard, { cardSummary, isHiddenField, parseCardBody } from "@/components/ConfirmationCard";
import ConfirmationQueue, { pickShown } from "@/components/ConfirmationQueue";
import {
  EMPTY_CONFIRMATIONS,
  ROWS_ANSWER_PREFIX,
  approvalFor,
  approveRows,
  approvedCard,
  cardFromEvent,
  confirmationReducer,
  rowSummary,
  settleAnswer,
  withTicked,
  NO_TICKS,
  rowTicksReducer,
  rowsView,
  ticksOf,
  type RowTicks,
  type ConfirmationAction,
  type ConfirmationQueueState,
} from "@/lib/confirmationQueue";
import { respondFailure, sendRespondInput } from "@/lib/respondInput";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

const PROJECT = "0f8fad5b-d9cb-469f-a165-70867728950e";
const STATUS = "7c9e6679-7425-40de-944b-e07fc1f90ae7";

function requested(i: number) {
  return {
    type: "requested" as const,
    value: {
      title: "Create this task?",
      detail: `«Task ${i}» · status Open`,
      context: [
        "This change is recorded as yours, made through the Projects assistant.",
        `project_id: «${PROJECT}»`,
        `title: «Task ${i}»`,
        `status_id: «${STATUS}»`,
        "status: Open",
      ].join("\n"),
      request_id: `rid-${i}`,
    },
  };
}

const run = (actions: ConfirmationAction[], from: ConfirmationQueueState = EMPTY_CONFIRMATIONS) =>
  actions.reduce(confirmationReducer, from);

const TEN = Array.from({ length: 10 }, (_, i) => requested(i + 1));

// ── The queue ────────────────────────────────────────────────────────────────

describe("every parallel card is kept", () => {
  // Mutation caught: a `requested` that replaces the list (the old single
  // slot) leaves one card.
  it("ten parallel events make ten cards, in order", () => {
    const s = run(TEN);
    expect(s.cards.map((c) => c.key)).toEqual(TEN.map((a) => a.value.request_id));
  });

  // Mutation caught: no dedupe — a replay of the same ten makes twenty.
  it("a replay of the same events adds no card", () => {
    expect(run([...TEN, ...TEN]).cards).toHaveLength(10);
  });

  // Mutation caught: a queue view that renders only the first card, or a
  // pager that cannot reach the last.
  it("every card is reachable through the pager", () => {
    const cards = run(TEN).cards;
    for (let i = 0; i < cards.length; i++) {
      const html = renderToStaticMarkup(
        createElement(ConfirmationQueue, { cards, onAnswer: () => {}, initialIndex: i }),
      );
      expect(html).toContain(`Task ${i + 1}`);
      expect(html).toContain(`${i + 1} of 10`);
    }
  });
});

describe("answering one card removes only that card", () => {
  // Mutation caught: `answering` that clears the whole list.
  it("ten minus one answered leaves nine", () => {
    const s = run([...TEN, { type: "answering", key: "rid-3" }, settleAnswer(run(TEN).cards[2], "ok")]);
    expect(s.cards).toHaveLength(9);
    expect(s.cards.some((c) => c.key === "rid-3")).toBe(false);
  });

  // Mutation caught: a replay that arrives while the POST is in flight
  // brings the card back, and the member answers it twice.
  it("a replay during the POST does not reopen the card", () => {
    const s = run([...TEN, { type: "answering", key: "rid-3" }, ...TEN]);
    expect(s.cards.some((c) => c.key === "rid-3")).toBe(false);
  });
});

describe("confirmation_resolved closes a card, live and on a replay", () => {
  it("a replay with a resolved event leaves the answered card out", () => {
    const resolved = { type: "resolved" as const, requestId: "rid-1" };
    // The stream order: ten requests, the answer, then a since=0-0 replay.
    const s = run([...TEN, resolved, ...TEN, resolved]);
    expect(s.cards.map((c) => c.key)).not.toContain("rid-1");
    expect(s.cards).toHaveLength(9);
  });

  // Mutation caught: `resolved` that only filters and does not remember.
  it("a request that comes after its own resolved event stays closed", () => {
    const s = run([{ type: "resolved", requestId: "rid-1" }, requested(1)]);
    expect(s.cards).toEqual([]);
  });

  it("a finished run clears the blocking cards", () => {
    expect(run([...TEN, { type: "runFinalized" }]).cards).toEqual([]);
  });
});

// ── The answer POST ──────────────────────────────────────────────────────────

function fakeFetch(status: number | "throw") {
  return vi.fn(async () => {
    if (status === "throw") throw new TypeError("Failed to fetch");
    return new Response(status === 200 ? "{}" : "{\"detail\":\"x\"}", { status });
  }) as unknown as typeof fetch;
}

const BODY = { request_id: "rid-2", answer: "APPROVE", was_freeform: false, thread_id: "t-1" };

async function answerThrough(status: number | "throw") {
  const silence = [vi.spyOn(console, "error").mockImplementation(() => {}), vi.spyOn(console, "warn").mockImplementation(() => {})];
  try {
    const card = run(TEN).cards[1];
    const outcome = await sendRespondInput(BODY, fakeFetch(status));
    // The card left the queue on the click, then the outcome settles it.
    const s = run([...TEN, { type: "answering", key: card.key }, settleAnswer(card, outcome)]);
    return { outcome, s };
  } finally {
    silence.forEach((s) => s.mockRestore());
  }
}

describe("the answer POST: restore only on a failure a retry can pass", () => {
  // Mutation caught: restoring on every failure (the 2026-10-06 loop).
  it("a 409 drops the card", async () => {
    const { outcome, s } = await answerThrough(409);
    expect(outcome).toBe("drop");
    expect(s.cards.some((c) => c.key === "rid-2")).toBe(false);
    expect(s.answering.has("rid-2")).toBe(false);
  });

  // Mutation caught: a 4xx that remembers the id. The gateway also answers
  // 409 when the control bus is slow, and then the tool still waits, so only
  // the server's resolved event may close a card for good.
  it("after a 409, only the server's resolved event keeps the card closed", async () => {
    const { s } = await answerThrough(409);
    const replay = confirmationReducer(s, requested(2));
    expect(replay.cards.some((c) => c.key === "rid-2")).toBe(true);
    const closed = run([{ type: "resolved", requestId: "rid-2" }, requested(2)], s);
    expect(closed.cards.some((c) => c.key === "rid-2")).toBe(false);
  });

  // Mutation caught: dropping on every failure, which strands a parked run
  // with no card after a gateway blip.
  it("a 5xx restores the card, first in the queue", async () => {
    const { outcome, s } = await answerThrough(502);
    expect(outcome).toBe("retry");
    expect(s.cards[0].key).toBe("rid-2");
    expect(s.cards).toHaveLength(10);
  });

  it("a network fault restores the card", async () => {
    const { outcome, s } = await answerThrough("throw");
    expect(outcome).toBe("retry");
    expect(s.cards.some((c) => c.key === "rid-2")).toBe(true);
  });

  it("a 2xx is ok, and the card stays gone", async () => {
    const { outcome, s } = await answerThrough(200);
    expect(outcome).toBe("ok");
    expect(s.cards).toHaveLength(9);
  });

  it("every 4xx drops, every 5xx retries", () => {
    expect([400, 401, 403, 404, 409, 422].map(respondFailure)).toEqual(Array(6).fill("drop"));
    expect([500, 502, 503, 504].map(respondFailure)).toEqual(Array(4).fill("retry"));
    expect(respondFailure(undefined)).toBe("retry");
  });

  // Mutation caught: a second fetch to respond-input that skips the rule.
  it("the one fetch of respond-input is the helper's", () => {
    expect(read("components/AgentChat.tsx")).not.toContain('fetch("/api/agent/respond-input"');
    expect(read("lib/respondInput.ts").match(/"\/api\/agent\/respond-input"/g)).toHaveLength(1);
  });
});

describe("the resolved event is kept off the message and out of the panel", () => {
  // Mutation caught: the event lands on message.customEvents and draws a
  // raw "Interactive view" fold under every answered card.
  it("both client lists name confirmation_resolved", () => {
    expect(read("hooks/useAgentChat.ts")).toMatch(/HITL_CONTROL_EVENTS[\s\S]*?"confirmation_resolved"[\s\S]*?\]\)/);
    expect(read("components/GenerativeUIPanel.tsx")).toMatch(/PANEL_HIDDEN_EVENTS[\s\S]*?"confirmation_resolved"[\s\S]*?\]\)/);
  });
});

describe("the card under the member's eye stays put", () => {
  // Mutation caught: a pager that holds only a numeric index. A restore puts
  // a card first, and a different card slides under the cursor.
  it("a restore above the shown card does not change it", () => {
    const cards = run(TEN).cards;
    const shown = { key: "rid-4", index: 3 };
    const restored = [{ ...cards[0], key: "rid-x", requestId: "rid-x" }, ...cards];
    expect(restored[pickShown(restored, shown)].key).toBe("rid-4");
  });

  it("when the shown card leaves, the card now at its place shows", () => {
    const cards = run([...TEN, { type: "answering", key: "rid-4" }]).cards;
    expect(cards[pickShown(cards, { key: "rid-4", index: 3 })].key).toBe("rid-5");
    expect(pickShown(cards.slice(0, 2), { key: "rid-9", index: 8 })).toBe(1);
  });

  // Mutation caught: buttons live on mount. In a queue the next card mounts
  // where the answered one was, so a double-click signs a card nobody read.
  it("a new card is not armed on its first render", () => {
    const html = renderToStaticMarkup(
      createElement(ConfirmationQueue, { cards: run(TEN).cards, onAnswer: () => {} }),
    );
    const approve = /<button[^>]*data-confirmation-approve[^>]*>/.exec(html)?.[0] ?? "";
    expect(approve).toContain('aria-disabled="true"');
  });
});

// ── The card ─────────────────────────────────────────────────────────────────

describe("the card reads as a sentence, then rows", () => {
  it("the summary joins the title and the name", () => {
    expect(cardSummary("Create this task?", "«Fix the extruder» · status Open")).toEqual({
      summary: "Create task «Fix the extruder»",
      rest: "status Open",
    });
    // A title that is not "<verb> this <thing>?" stays as it is.
    expect(cardSummary("Send this email?", "From a · To b")).toEqual({
      summary: "Send this email?",
      rest: "From a · To b",
    });
  });

  // Mutation caught: a UUID field drawn as a row (the old monospace dump).
  it("a *_id field that holds a UUID is hidden, and the names stay", () => {
    const body = parseCardBody(requested(1).value.context);
    expect(body.fields.map((f) => f.label)).toEqual(["Title", "Status"]);
    // The value keeps the tool's marks. The card draws them (`FencedText`).
    expect(body.fields[0].value).toBe("«Task 1»");
    expect(body.notes).toEqual(["This change is recorded as yours, made through the Projects assistant."]);
    expect(isHiddenField("status_id", `«${STATUS}» → «${PROJECT}»`)).toBe(true);
    expect(isHiddenField("task_number", PROJECT)).toBe(false);
    expect(isHiddenField("parent_id", "not a uuid")).toBe(false);
  });

  // Mutation caught: the label kept the list hyphen ("- From", "- To").
  // Follow-up 3 of #766: the email cards write their targets as list rows.
  it("a list row reads by its key, without the hyphen", () => {
    const send = parseCardBody(
      "The mailbox and each recipient:\n- From: Fracktal · dana@fracktal.in\n- To: geo@fracktal.test",
    );
    expect(send.fields.map((f) => f.label)).toEqual(["From", "To"]);
    expect(send.fields[0].value).toBe("Fracktal · dana@fracktal.in");
    expect(send.notes).toEqual(["The mailbox and each recipient:"]);
    const draft = parseCardBody(
      "Each recipient of this draft:\n- To: a@b.test\n- Cc: c@b.test\n- Bcc: d@b.test",
    );
    expect(draft.fields.map((f) => f.label)).toEqual(["To", "Cc", "Bcc"]);
    // A key with no hyphen keeps its label, and a hyphen inside a key stays.
    expect(parseCardBody("status: Open\nfollow-up: soon").fields.map((f) => f.label)).toEqual([
      "Status",
      "Follow-up",
    ]);
    const html = renderToStaticMarkup(
      createElement(ConfirmationCard, {
        title: "Send this email?",
        context: "The mailbox and each recipient:\n- From: Box\n- To: geo@fracktal.test",
        onApprove: () => {},
        onReject: () => {},
      }),
    );
    expect(html).toContain(">From</dt>");
    expect(html).not.toContain(">- From<");
  });

  it("an email body stays text, in the UI font", () => {
    const body = parseCardBody("Hi Bob,\n\nThe report is attached.\nThanks: Ann");
    expect(body.fields).toEqual([]);
    expect(body.text).toContain("The report is attached.");
    // A field whose value spans lines (a CRM description) stays text too,
    // so no line is lost to a row it does not belong to.
    const crm = parseCardBody("lead_name: Ann\ndescription: line one\nline two");
    expect(crm.fields).toEqual([]);
    expect(crm.text).toContain("line two");
    const html = renderToStaticMarkup(
      createElement(ConfirmationCard, {
        title: "Send this email?",
        context: "Hi Bob,\n\nThe report is attached.",
        onApprove: () => {},
        onReject: () => {},
      }),
    );
    expect(html).not.toMatch(/font-mono|<pre/);
  });
});

describe("the card wears the product tokens", () => {
  const src = read("components/ConfirmationCard.tsx");
  const html = renderToStaticMarkup(
    createElement(ConfirmationCard, {
      title: "Create this task?",
      detail: "«Task 1» · status Open",
      context: requested(1).value.context,
      onApprove: () => {},
      onReject: () => {},
    }),
  );

  // Mutation caught: the amber card, the warning tone or a hover:bg-emerald.
  it("no raw palette class, no warning tone, no emoji", () => {
    expect(src).not.toMatch(/\b(?:bg|text|border|ring)-(?:amber|emerald|yellow|orange|red|green)-\d/);
    expect(html).not.toMatch(/warning/);
    expect(html).not.toMatch(/\p{Extended_Pictographic}/u);
  });

  // Mutation caught: Approve painted with a status hue (bg-success) instead
  // of the primary action colour, which an accent change must move.
  it("Approve is the primary Button, and the surface is neutral", () => {
    const approve = /<button[^>]*data-confirmation-approve[^>]*>/.exec(html)?.[0] ?? "";
    expect(approve).toContain("bg-primary");
    expect(approve).not.toMatch(/bg-success|bg-warning|bg-destructive/);
    expect(html).toMatch(/border-border bg-card/);
  });

  it("no UUID reaches the page", () => {
    expect(html).not.toContain(PROJECT);
    expect(html).not.toContain(STATUS);
  });
});

// ── A card with rows: several tasks, ONE card (WS-46 P13 one-card) ───────────

describe("a card with rows", () => {
  const ROWS = [
    { id: "row-1", label: "Book the caterer", hint: "Priya (priya@x.io) · due Fri 9 Oct 2026", checked: true },
    { id: "row-2", label: "Print the badges", hint: "nobody assigned", checked: true },
    { id: "row-3", label: "Send the invites", hint: "made 3 min ago, so it starts unticked", checked: false },
  ];
  const event = { title: "Create 3 tasks in «Ops»?", detail: "one batch", request_id: "rid-rows", rows: ROWS };
  const card = (rows = ROWS) =>
    renderToStaticMarkup(
      createElement(ConfirmationCard, {
        title: event.title,
        detail: event.detail,
        context: "project: «Ops»\ntasks: 3",
        rows,
        fenced: true,
        onApprove: () => {},
        onReject: () => {},
      }),
    );

  // Mutation caught: a parse that adds `rows` to every card changes the old cards.
  it("a card with no rows is unchanged", () => {
    expect("rows" in (cardFromEvent(requested(1).value) ?? {})).toBe(false);
    expect(approvalFor({}, ["row-1"])).toBe("APPROVE");
    const html = renderToStaticMarkup(
      createElement(ConfirmationCard, {
        title: "Create this task?",
        detail: "«Task 1»",
        onApprove: () => {},
        onReject: () => {},
      }),
    );
    expect(html).not.toContain("data-confirmation-rows");
    expect(html).not.toMatch(/type="checkbox"/);
  });

  it("the event's rows reach the card, ticked as the tool asks", () => {
    expect(cardFromEvent(event)?.rows).toEqual(ROWS);
    const html = card();
    expect(html.match(/type="checkbox"/g)).toHaveLength(3);
    expect(html.match(/checked=""/g)).toHaveLength(2);
    expect(html).toContain("cc-checkbox");
    expect(html).toContain("Send the invites");
    expect(html).toContain("made 3 min ago, so it starts unticked");
  });

  // Mutation caught: a summary that says the tool's count, not the ticked one.
  it("the summary counts the ticked rows", () => {
    // The marks never show: the name is a quiet emphasis (owner, 2026-10-07).
    expect(card()).toContain('aria-label="Create 2 of 3 tasks in Ops?"');
    expect(card()).not.toContain("«");
    expect(rowSummary("Create 4 tasks in «Ops»?", 4, 4)).toBe("Create 4 tasks in «Ops»?");
    expect(rowSummary("Create 4 tasks in «Ops»?", 1, 4)).toBe("Create 1 of 4 tasks in «Ops»?");
    expect(rowSummary("Pick the rows", 1, 4)).toBe("Pick the rows (1 of 4)");
  });

  // Mutation caught: Approve live with nothing ticked.
  it("Approve is off when no row is ticked", () => {
    const none = card(ROWS.map((r) => ({ ...r, checked: false })));
    const approve = /<button[^>]*data-confirmation-approve[^>]*>/.exec(none)?.[0] ?? "";
    expect(approve).toMatch(/\sdisabled=""/);
    expect(none).toContain("Create 0 of 3 tasks");
    const some = /<button[^>]*data-confirmation-approve[^>]*>/.exec(card())?.[0] ?? "";
    expect(some).not.toMatch(/\sdisabled=""/);
  });

  // Mutation caught: an answer that names an unticked row, or one the card
  // never offered, or that approves with none.
  it("Approve names exactly the ticked rows the card offered, in one answer", () => {
    const pending = cardFromEvent(event)!;
    expect(approvalFor(pending, ["row-2", "row-1"])).toBe('APPROVE {"rows":["row-1","row-2"]}');
    expect(approvalFor(pending, ["row-1", "row-9"])).toBe('APPROVE {"rows":["row-1"]}');
    expect(approvalFor(pending, [])).toBeNull();
    expect(approvalFor(pending, undefined)).toBeNull();
    expect(approveRows(["row-3"]).startsWith(ROWS_ANSWER_PREFIX)).toBe(true);
  });

  // Review round 1. Mutation caught: the queue answers with the card as the
  // tool sent it, so a restore after a 5xx shows the tool's ticks again.
  it("a restore after a failed POST keeps the member's ticks", () => {
    const pending = cardFromEvent(event)!;
    // The queue's Approve path: the answer and the card it hands on.
    const approved = approvedCard(pending, ["row-1"])!;
    expect(approved.answer).toBe('APPROVE {"rows":["row-1"]}');
    const answered = approved.card;
    expect(approvedCard(pending, [])).toBeNull();
    const state = run([
      { type: "requested", value: event },
      { type: "answering", key: pending.key },
      settleAnswer(answered, "retry"),
    ]);
    expect(state.cards[0].rows?.map((r) => r.checked)).toEqual([true, false, false]);
    const html = renderToStaticMarkup(
      createElement(ConfirmationQueue, { cards: state.cards, onAnswer: () => {} }),
    );
    expect(html.match(/checked=""/g)).toHaveLength(1);
    expect(html).toContain("Create 1 of 3 tasks");
    expect(withTicked({ key: "k", title: "t" }, ["a"])).toEqual({ key: "k", title: "t" });
  });

  it("a rows list the card cannot read gives no row, so it cannot be approved", () => {
    const bad = cardFromEvent({ ...event, rows: [{ id: "a" }, { id: "a" }] })!;
    expect(bad.rows).toEqual([]);
    expect(approvalFor(bad, ["a"])).toBeNull();
  });

  it("the card keeps the queue and the arming delay", () => {
    const html = renderToStaticMarkup(
      createElement(ConfirmationQueue, {
        cards: [cardFromEvent(event)!, ...run(TEN).cards],
        onAnswer: () => {},
      }),
    );
    expect(html).toContain("1 of 11");
    const approve = /<button[^>]*data-confirmation-approve[^>]*>/.exec(html)?.[0] ?? "";
    expect(approve).toContain('aria-disabled="true"');
  });
});

// ── The ticks are the member's (PR #691 review) ──────────────────────────────
//
// The card and the queue hold no tick logic. The logic is pure, and these
// tests drive it the way the card's checkboxes and the pager do. Two
// mutations of the review, each one red here:
//   (a) Approve sends the tool's default ticks, not the member's
//       (`ticksOf` ignores the kept ticks) -> "Approve sends the member's ticks".
//   (b) Paging away and back resets the ticks (`rowTicksReducer` keeps one
//       card only) -> "a page away and back keeps each card's ticks".
describe("the ticks are the member's", () => {
  const rows = (prefix: string) => [
    { id: `${prefix}-1`, label: "One", checked: true },
    { id: `${prefix}-2`, label: "Two", checked: true },
    { id: `${prefix}-3`, label: "Three", checked: false },
  ];
  const cardA = { key: "rid-a", title: "Create 3 tasks in «Ops»?", requestId: "rid-a", rows: rows("a") };
  const cardB = { key: "rid-b", title: "Create 3 tasks in «Web»?", requestId: "rid-b", rows: rows("b") };
  const toggle = (ticks: RowTicks, card: typeof cardA, id: string, on: boolean) =>
    rowTicksReducer(ticks, { type: "toggle", card, id, on });

  it("Approve sends the member's ticks, never the tool's defaults", () => {
    let ticks = toggle(NO_TICKS, cardA, "a-1", false);
    ticks = toggle(ticks, cardA, "a-3", true);
    const view = rowsView(cardA, ticks);
    expect(view.approved?.answer).toBe('APPROVE {"rows":["a-2","a-3"]}');
    expect(view.rows?.map((r) => r.checked)).toEqual([false, true, true]);
    // The card the answer path restores carries the same ticks.
    expect(view.approved?.card.rows?.map((r) => r.checked)).toEqual([false, true, true]);
    // With no tick yet, the tool's defaults stand.
    expect(rowsView(cardA, NO_TICKS).approved?.answer).toBe('APPROVE {"rows":["a-1","a-2"]}');
  });

  it("a page away and back keeps each card's ticks", () => {
    let ticks = toggle(NO_TICKS, cardA, "a-2", false);
    ticks = toggle(ticks, cardB, "b-1", false); // the member pages to B
    ticks = toggle(ticks, cardB, "b-3", true);
    expect(ticksOf(cardA, ticks)).toEqual(["a-1"]); // and back to A
    expect(ticksOf(cardB, ticks)).toEqual(["b-2", "b-3"]);
    const html = renderToStaticMarkup(
      createElement(ConfirmationCard, {
        title: cardA.title,
        rows: rowsView(cardA, ticks).rows,
        onApprove: () => {},
        onReject: () => {},
      }),
    );
    expect(html.match(/checked=""/g)).toHaveLength(1);
    expect(html).toContain("Create 1 of 3 tasks");
  });

  it("unticking every row leaves nothing to send", () => {
    let ticks = toggle(NO_TICKS, cardA, "a-1", false);
    ticks = toggle(ticks, cardA, "a-2", false);
    expect(rowsView(cardA, ticks).approved).toBeNull();
  });

  it("a tick of a row the card does not offer changes nothing", () => {
    expect(toggle(NO_TICKS, cardA, "zz", true)).toBe(NO_TICKS);
    expect(rowTicksReducer(NO_TICKS, { type: "toggle", card: { key: "k" }, id: "a-1", on: true }))
      .toBe(NO_TICKS);
  });

  it("a card with no rows is the plain Approve, untouched by any tick", () => {
    const plain = { key: "rid-p", title: "Send this email?", requestId: "rid-p" };
    expect(rowsView(plain, NO_TICKS)).toEqual({ approved: { card: plain, answer: "APPROVE" } });
  });

  // The components call these functions, and hold no logic of their own.
  // A mutation that puts tick logic back in a component fails here.
  it("the card holds no tick state, and the queue answers from rowsView", () => {
    // The component itself, not the pure parse helpers above it in the file.
    const source = read("components/ConfirmationCard.tsx");
    const card = source.slice(source.indexOf("export default function ConfirmationCard"));
    expect(card.length).toBeGreaterThan(100);
    expect(card).not.toMatch(/useState<[^>]*Set/);
    expect(card).not.toMatch(/\.filter\(/);
    expect(card).toContain("rowsSummary(base.summary, rows)");
    expect(card).toContain("canApprove(rows)");
    const queue = read("components/ConfirmationQueue.tsx");
    expect(queue).toContain("useReducer(rowTicksReducer, NO_TICKS)");
    expect(queue).toContain("const view = rowsView(card, ticks);");
    expect(queue).toContain("rows={view.rows}");
    expect(queue).toContain("onAnswer(view.approved.card, view.approved.answer)");
    expect(queue).not.toMatch(/\.filter\(|\.checked/);
  });
});
