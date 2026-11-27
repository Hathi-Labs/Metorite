/**
 * UXD-3 — an approval card never leaves with no word, and a failure never goes
 * only to the console.
 *
 * R7 fences named here:
 *   * `approve-card-receipt`: an answer leaves one line, "Applying…", then
 *     "Approved" or "Rejected" once the server took it.
 *   * `approve-card-retry-reason`: a network fault or a 5xx brings the card
 *     back with the reason beside its buttons. The next answer clears it.
 *   * `approve-card-refused-reason`: a 4xx turns the line into "Not applied"
 *     with what to change.
 *   * `email-cards-no-browser-confirm`: the email tool cards ask in the
 *     shared `ConfirmDialog`, never with `confirm()`.
 *   * `held-sandbox-card-row-results` (SBX-3b done-when 24): the receipt of a
 *     held sandbox card says what happened to each row, through `receiptText`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { describe, expect, it } from "vitest";

import ConfirmationQueue from "@/components/ConfirmationQueue";
import ConfirmationReceipts from "@/components/ConfirmationReceipts";
import {
  EMPTY_CONFIRMATIONS,
  confirmationReducer,
  receiptText,
  rowResultsFromEvent,
  settleAnswer,
  type ConfirmationAction,
  type ConfirmationQueueState,
  type PendingConfirmation,
} from "@/lib/confirmationQueue";
import { respondFailureText, sendRespondInputResult } from "@/lib/respondInput";

const card: PendingConfirmation = {
  key: "r1",
  title: "Confirm action",
  detail: "Create task \"Call the supplier\"",
  requestId: "r1",
};

const run = (actions: ConfirmationAction[], from: ConfirmationQueueState = EMPTY_CONFIRMATIONS) =>
  actions.reduce(confirmationReducer, from);

const asked = run([{ type: "requested", value: { request_id: "r1", title: card.title, detail: card.detail } }]);

const answering = (intent: "approved" | "rejected"): ConfirmationAction => ({
  type: "answering",
  key: "r1",
  label: card.detail!,
  intent,
  afterId: "m7",
});

const lines = (s: ConfirmationQueueState) => s.receipts.map(receiptText);

describe("approve-card-receipt", () => {
  it("says Applying while the answer is in flight, then Approved", () => {
    const flying = run([answering("approved")], asked);
    expect(flying.cards).toHaveLength(0);
    expect(lines(flying)).toEqual(['Applying… Create task "Call the supplier"']);
    expect(flying.receipts[0].afterId).toBe("m7");

    const done = run([settleAnswer(card, "ok")], flying);
    expect(lines(done)).toEqual(['Approved · Create task "Call the supplier"']);
  });

  it("says Rejected for a Reject", () => {
    const done = run([answering("rejected"), settleAnswer(card, "ok")], asked);
    expect(lines(done)).toEqual(['Rejected · Create task "Call the supplier"']);
  });

  it("a server close waits for the POST, so a timeout never reads Approved", () => {
    const closed = run([answering("approved"), { type: "resolved", requestId: "r1" }], asked);
    expect(closed.receipts[0].kind).toBe("applying");
    expect(run([settleAnswer(card, "ok")], closed).receipts[0].kind).toBe("approved");
    // The POST failed after the close: the card cannot come back, so the line says so.
    const lost = run([settleAnswer(card, "retry", "x")], closed);
    expect(lost.cards).toEqual([]);
    expect(lines(lost)[0]).toMatch(/^Closed · /);
  });

  it("a late retry never deletes a line that settled", () => {
    const done = run([answering("approved"), settleAnswer(card, "ok"), settleAnswer(card, "retry", "x")], asked);
    expect(lines(done)).toEqual(['Approved · Create task "Call the supplier"']);
  });

  it("draws one line per answer, with its look", () => {
    const done = run([answering("approved"), settleAnswer(card, "ok")], asked);
    const html = renderToStaticMarkup(createElement(ConfirmationReceipts, { receipts: done.receipts }));
    expect(html).toContain('data-confirmation-receipt="approved"');
    expect(html).toContain("Approved · Create task");
    expect(renderToStaticMarkup(createElement(ConfirmationReceipts, { receipts: [] }))).toBe("");
  });

  it("a resend drops the line, since its notice says what happened", () => {
    const done = run([answering("approved"), settleAnswer(card, "drop")], asked);
    expect(done.receipts).toEqual([]);
  });
});

describe("approve-card-changed-since (task 3827)", () => {
  const notDone: ConfirmationAction = { type: "notDone", requestId: "r1" };
  const words = 'Not done · Create task "Call the supplier". The item changed after the card showed, so nothing was changed.';

  it("a refused approve reads Not done, and the POST answer after it keeps that", () => {
    // The stream beats the POST answer.
    const early = run([answering("approved"), notDone, settleAnswer(card, "ok")], asked);
    expect(lines(early)).toEqual([words]);
    expect(early.receipts[0].kind).toBe("notDone");
    // The POST answer comes first.
    const late = run([answering("approved"), settleAnswer(card, "ok"), notDone], asked);
    expect(lines(late)).toEqual([words]);
  });

  it("draws it in the warning tone, with its own marker", () => {
    const done = run([answering("approved"), settleAnswer(card, "ok"), notDone], asked);
    const html = renderToStaticMarkup(createElement(ConfirmationReceipts, { receipts: done.receipts }));
    expect(html).toContain('data-confirmation-receipt="notDone"');
    expect(html).toContain("text-warning");
    expect(html).not.toContain("Approved");
  });

  it("only the receipt of that card changes, and a Reject never does", () => {
    const other: ConfirmationAction = { type: "notDone", requestId: "r9" };
    const approved = run([answering("approved"), settleAnswer(card, "ok"), other], asked);
    expect(lines(approved)).toEqual(['Approved · Create task "Call the supplier"']);
    const rejected = run([answering("rejected"), settleAnswer(card, "ok"), notDone], asked);
    expect(lines(rejected)).toEqual(['Rejected · Create task "Call the supplier"']);
    // No receipt of that key: nothing is made up.
    expect(run([notDone], asked).receipts).toEqual([]);
  });
});

describe("approve-card-retry-reason", () => {
  it("brings the card back with the reason, and the next answer clears it", () => {
    const reason = respondFailureText(503);
    const back = run([answering("approved"), settleAnswer(card, "retry", reason)], asked);
    expect(back.cards.map((c) => c.key)).toEqual(["r1"]);
    expect(back.errors.get("r1")).toBe("The server could not take the answer. Try again.");
    // The "Applying" line goes: the card itself is back.
    expect(back.receipts).toEqual([]);

    const html = renderToStaticMarkup(
      createElement(ConfirmationQueue, { cards: back.cards, errors: back.errors, onAnswer: () => {} }),
    );
    expect(html).toContain("data-confirmation-error");
    expect(html).toContain("The server could not take the answer. Try again.");
    expect(html).toMatch(/data-confirmation-approve=""[^>]*>[\s\S]*?Retry/);

    const again = run([answering("approved")], back);
    expect(again.errors.has("r1")).toBe(false);
  });

  it("a network fault names the connection", async () => {
    const res = await sendRespondInputResult(
      { request_id: "r1", answer: "APPROVE", was_freeform: false, thread_id: "t" },
      (() => Promise.reject(new TypeError("offline"))) as unknown as typeof fetch,
    );
    expect(res.outcome).toBe("retry");
    expect(res.reason).toMatch(/could not be reached/);
  });
});

describe("approve-card-refused-reason", () => {
  it("a 422 keeps the card open with what to change", async () => {
    const res = await sendRespondInputResult(
      { request_id: "r1", answer: "APPROVE", was_freeform: false, thread_id: "t" },
      (async () => new Response(JSON.stringify({ detail: "The due date is in the past." }), { status: 422 })) as typeof fetch,
    );
    expect(res.outcome).toBe("retry");
    expect(res.reason).toBe("The server refused the answer: The due date is in the past.");
    const back = run([answering("approved"), settleAnswer(card, res.outcome, res.reason)], asked);
    expect(back.cards).toEqual([card]);
    expect(back.receipts).toEqual([]);
    expect(back.errors.get("r1")).toBe(res.reason);
  });

  it("a 403 still turns the line into Not applied", async () => {
    const res = await sendRespondInputResult(
      { request_id: "r1", answer: "APPROVE", was_freeform: false, thread_id: "t" },
      (async () => new Response(JSON.stringify({ detail: "Not yours." }), { status: 403 })) as typeof fetch,
    );
    expect(res.outcome).toBe("drop");
    expect(res.reason).toMatch(/^You cannot answer/);

    const done = run([answering("approved"), settleAnswer(card, "drop", res.reason)], asked);
    expect(lines(done)).toEqual([
      'Not applied · Create task "Call the supplier". You cannot answer this request. Ask its owner to answer it.',
    ]);
    expect(done.cards).toEqual([]);
  });

  it("a 409 says nothing waits on the answer", () => {
    expect(respondFailureText(409)).toMatch(/^Nothing waits on this answer now/);
  });
});

describe("email-cards-no-browser-confirm", () => {
  it("asks in ConfirmDialog", () => {
    const src = readFileSync(
      fileURLToPath(new URL("../components/email/EmailToolCards.tsx", import.meta.url)),
      "utf-8",
    );
    expect(src).not.toMatch(/(^|[^.\w])confirm\(/m);
    expect(src.match(/<ConfirmDialog\b/g)?.length).toBe(3);
  });
});

describe("held-sandbox-card-row-results (SBX-3b done-when 24)", () => {
  const rid = "a".repeat(32);
  const event = {
    request_id: rid,
    rows: [
      { id: "row-0", status: 200 },
      { id: "row-1", status: 403, code: "sandbox_write_refused" },
      { id: "row-2", status: 403, code: "sandbox_approval_timeout" },
      { id: "row-3", status: 404 },
    ],
  };
  const held = run([
    { type: "requested", value: { request_id: rid, title: "Let a sandbox app make 4 changes?" } },
    { type: "answering", key: rid, label: "Let a sandbox app make 4 changes?", intent: "approved", afterId: "m7" },
    { type: "answered", key: rid },
  ]);

  it("shows a result for each row on the line of its card", () => {
    const results = rowResultsFromEvent(event);
    expect(results).not.toBeNull();
    const done = run([{ type: "rowResults", ...results! }], held);
    expect(lines(done)).toEqual([
      "Approved · Let a sandbox app make 4 changes? Results: row 1 done; row 2 not done, "
        + "not approved; row 3 not done, the card timed out; row 4 failed (404).",
    ]);
    const html = renderToStaticMarkup(createElement(ConfirmationReceipts, { receipts: done.receipts }));
    expect(html).toContain("Results: row 1 done; row 2 not done, not approved;");
  });

  it("changes no line of another card, and takes no body", () => {
    const other = rowResultsFromEvent({ ...event, request_id: "b".repeat(32) });
    expect(lines(run([{ type: "rowResults", ...other! }], held))).toEqual(lines(held));
    const withBody = rowResultsFromEvent({ request_id: rid, rows: [{ id: "row-0", status: 200, body: { secret: "x" } }] });
    expect(withBody?.rows).toEqual([{ id: "row-0", status: 200 }]);
  });

  it("refuses a value that is not one", () => {
    for (const bad of [null, "x", { request_id: "r1", rows: event.rows }, { request_id: rid, rows: [] },
      { request_id: rid, rows: [{ id: "x", status: 200 }] }, { request_id: rid, rows: [{ id: "row-0", status: 99 }] }]) {
      expect(rowResultsFromEvent(bad)).toBeNull();
    }
  });
});
