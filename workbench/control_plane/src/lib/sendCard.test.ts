/**
 * The held send card (`email_app_master_plan.md` §21), on the client.
 *
 * A held chat read mail from outside senders. The gateway builds and records
 * the card of each send, classes each person, and starts a new address
 * unticked. The client draws what the server sent and decides nothing. Each
 * block names the rule it fences and a mutation that turns it red.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ConfirmationCard from "@/components/ConfirmationCard";
import ReplyCardForm from "@/components/ReplyCardForm";
import { approvalFor, canApprove, cardFromEvent, rowsFromEvent } from "@/lib/confirmationQueue";
import { classOfAddress, replyFromEvent } from "@/lib/replyCard";
import {
  NEW_MARK,
  THREAD_MARK,
  classOf,
  isSendCard,
  noRecipientHint,
  rowMark,
  sendCanGo,
} from "@/lib/sendCard";

/** The rows of a held card, as `POST /email/held-card` answers them. */
const HELD_ROWS = [
  { id: "to-0", label: "To: kim@contoso.test", hint: "", checked: true, kind: "to", class: "sent" },
  {
    id: "cc-0",
    label: "Cc: x@evil.example",
    hint: NEW_MARK,
    checked: false,
    kind: "cc",
    class: "new",
  },
  {
    id: "cc-1",
    label: "Cc: ravi@fracktai.in",
    hint: NEW_MARK,
    checked: false,
    kind: "cc",
    class: "new",
    warning: "the domain looks like fracktal.in, which you know — check each letter",
  },
];

function held(): ReturnType<typeof cardFromEvent> {
  return cardFromEvent({ title: "Send this draft?", request_id: "a".repeat(32), rows: HELD_ROWS });
}

describe("the rows of a held card are the server's", () => {
  it("reads the kind, the class and the warning, and keeps the server's ticks", () => {
    // Mutation: drop `cls` from rowsFromEvent, or tick every row → red.
    const rows = rowsFromEvent(HELD_ROWS)!;
    expect(rows.map((r) => [r.id, r.kind, r.cls, r.checked])).toEqual([
      ["to-0", "to", "sent", true],
      ["cc-0", "cc", "new", false],
      ["cc-1", "cc", "new", false],
    ]);
    expect(rows[2].warning).toContain("fracktal.in");
  });

  it("an unknown class reads as new, so a drift marks the person", () => {
    expect(classOf("vip")).toBe("new");
    expect(classOf(undefined)).toBeUndefined();
  });

  it("a row with none of the new keys is exactly as before", () => {
    // Mutation: always set `kind` or `cls` → red.
    expect(rowsFromEvent([{ id: "r1", label: "Task one", checked: true }])).toEqual([
      { id: "r1", label: "Task one", hint: undefined, checked: true },
    ]);
  });
});

describe("the mark on a new row", () => {
  it("names a new address in the server's words", () => {
    expect(rowMark("new")).toBe("new address — not in your contacts");
    expect(rowMark("thread")).toBe(THREAD_MARK);
    expect(rowMark("org")).toBe("");
    expect(rowMark(undefined)).toBe("");
  });

  it("the card draws the mark on the new row and the warning of a lookalike", () => {
    // Mutation: remove the mark from ConfirmationCard → red.
    const card = held()!;
    const html = renderToStaticMarkup(
      createElement(ConfirmationCard, {
        title: card.title,
        rows: card.rows,
        onApprove: () => undefined,
        onReject: () => undefined,
      }),
    );
    expect(html.match(/data-send-row-mark="new"/g)?.length).toBe(2);
    expect(html).toContain(NEW_MARK);
    expect(html).toContain("data-send-row-warning");
    expect(html).toContain("looks like fracktal.in");
    // The server's hint is the mark, and the card draws it once.
    expect(html.split(NEW_MARK).length - 1).toBe(2);
  });
});

describe("Send needs a ticked To row", () => {
  it("a send card with no ticked To row cannot approve", () => {
    // Mutation: let canApprove pass any ticked row → red.
    const rows = rowsFromEvent(HELD_ROWS)!.map((r) => ({ ...r, checked: r.kind === "cc" }));
    expect(isSendCard(rows)).toBe(true);
    expect(canApprove(rows)).toBe(false);
    expect(sendCanGo(rows)).toBe(false);
    expect(noRecipientHint(rows)).toMatch(/Tick one To row/);
    expect(approvalFor({ rows }, ["cc-0", "cc-1"])).toBeNull();
  });

  it("a ticked To row lets it go, and the answer names the ticked ids only", () => {
    // Mutation: put an unticked id in the answer → red.
    const card = held()!;
    expect(canApprove(card.rows)).toBe(true);
    expect(approvalFor(card, ["to-0"])).toBe('APPROVE {"rows":["to-0"]}');
    expect(approvalFor(card, ["to-0", "cc-1"])).toBe('APPROVE {"rows":["to-0","cc-1"]}');
  });

  it("a rows card that is not a send card keeps the old rule", () => {
    const rows = rowsFromEvent([{ id: "a", label: "A", checked: true }])!;
    expect(isSendCard(rows)).toBe(false);
    expect(canApprove(rows)).toBe(true);
    expect(noRecipientHint(rows)).toBe("");
  });
});

describe("the held reply card marks each person", () => {
  const event = {
    message_id: "m-1",
    recipients: {
      reply: { to: ["kim@contoso.test"], cc: [] },
      reply_all: { to: ["kim@contoso.test", "x@evil.example"], cc: [] },
    },
    reply_all: false,
    body: "Hi",
    classes: { "kim@contoso.test": "thread", "x@evil.example": "new" },
  };

  it("reads the classes, and a form with none is as before", () => {
    const form = replyFromEvent(event)!;
    expect(classOfAddress(form, "KIM@contoso.test")).toBe("thread");
    expect(classOfAddress(form, "x@evil.example")).toBe("new");
    expect(classOfAddress(form, "other@x.test")).toBe("new");
    const plain = replyFromEvent({ ...event, classes: undefined })!;
    expect(plain.classes).toBeUndefined();
    expect(classOfAddress(plain, "kim@contoso.test")).toBeUndefined();
  });

  it("draws the mark beside each classed address", () => {
    // Mutation: drop the mark from AddressRow → red.
    const form = replyFromEvent({ ...event, reply_all: true })!;
    const html = renderToStaticMarkup(
      createElement(ReplyCardForm, {
        form,
        onAnswer: () => undefined,
        onDiscard: () => undefined,
        armed: true,
      }),
    );
    expect(html).toContain('data-reply-mark="thread"');
    expect(html).toContain('data-reply-mark="new"');
    expect(html).toContain(NEW_MARK);
    const plain = renderToStaticMarkup(
      createElement(ReplyCardForm, {
        form: replyFromEvent({ ...event, classes: undefined })!,
        onAnswer: () => undefined,
        onDiscard: () => undefined,
        armed: true,
      }),
    );
    expect(plain).not.toContain("data-reply-mark");
  });
});
