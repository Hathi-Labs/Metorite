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

  it("a server close while in flight settles the line too", () => {
    const done = run([answering("approved"), { type: "resolved", requestId: "r1" }], asked);
    expect(done.receipts[0].kind).toBe("approved");
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
  it("turns the line into Not applied, with what to change", async () => {
    const res = await sendRespondInputResult(
      { request_id: "r1", answer: "APPROVE", was_freeform: false, thread_id: "t" },
      (async () => new Response(JSON.stringify({ detail: "The due date is in the past." }), { status: 422 })) as typeof fetch,
    );
    expect(res.outcome).toBe("drop");
    expect(res.reason).toBe("The server refused the answer: The due date is in the past.");

    const done = run([answering("approved"), settleAnswer(card, "drop", res.reason)], asked);
    expect(lines(done)).toEqual([
      'Not applied · Create task "Call the supplier". The server refused the answer: The due date is in the past.',
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
