/**
 * The reply card (EM-CR, `email_app_master_plan.md` §18): the form of the
 * event, the toggle, and the one answer of Send and Save to Drafts.
 *
 * The rules this file holds (R7):
 *
 * 1. The client derives no recipient. Reply and Reply all show the server's
 *    two lists, and the answer names the list the card showed.
 * 2. The answer holds exactly the keys the server reads, and the body goes
 *    as typed. An empty or long body sends nothing.
 * 3. A form the card cannot read is no form. The queue keeps it as `null`,
 *    and the card then offers Discard only.
 * 4. The card is the one confirmation card: `ConfirmationCard` draws the
 *    form, and no second card file draws a reply.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { cardFromEvent } from "@/lib/confirmationQueue";
import {
  MAX_REPLY_BODY,
  bodyCanGo,
  recipientsFor,
  replyAllAddsSomeone,
  replyAnswer,
  replyFromEvent,
  replyHint,
} from "@/lib/replyCard";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8").replace(/\r\n/g, "\n");

const EVENT = {
  title: "Reply to Kim <kim@contoso.test>?",
  detail: "From Work · jane@acme.test · Re: Quote",
  request_id: "rid-1",
  reply: {
    mailbox: "Work · jane@acme.test",
    subject: "Re: Quote",
    original: { from: "Kim <kim@contoso.test>", subject: "Quote", received: "2026-10-19T09:00:00Z", excerpt: "Can you confirm?" },
    recipients: {
      reply: { to: ["kim@contoso.test"], cc: [] },
      reply_all: { to: ["kim@contoso.test", "lee@contoso.test"], cc: ["ops@contoso.test"] },
    },
    reply_all: false,
    body: "Hi Kim,\n\nYes.",
    note: "",
  },
};

describe("the reply form of the event", () => {
  it("reads the server's two recipient lists, and derives none", () => {
    const form = replyFromEvent(EVENT.reply)!;
    expect(recipientsFor(form, false)).toEqual({ to: ["kim@contoso.test"], cc: [] });
    expect(recipientsFor(form, true)).toEqual({
      to: ["kim@contoso.test", "lee@contoso.test"],
      cc: ["ops@contoso.test"],
    });
    expect(form.replyAll).toBe(false);
    expect(form.body).toBe("Hi Kim,\n\nYes.");
  });

  it("is no form when a list has no recipient or is not a list", () => {
    expect(replyFromEvent({ ...EVENT.reply, recipients: { reply: { to: [] }, reply_all: { to: ["a@b.test"] } } })).toBeNull();
    expect(replyFromEvent({ ...EVENT.reply, recipients: { reply: { to: "kim@contoso.test" }, reply_all: { to: [] } } })).toBeNull();
    expect(replyFromEvent("Reply")).toBeNull();
  });

  it("travels on the queue's card, and a broken form stays a reply card with no form", () => {
    expect(cardFromEvent(EVENT)!.reply).toEqual(replyFromEvent(EVENT.reply));
    expect(cardFromEvent({ ...EVENT, reply: { body: "x" } })!.reply).toBeNull();
    expect(cardFromEvent({ title: "Archive?", request_id: "r2" })).not.toHaveProperty("reply");
  });

  it("knows when Reply all adds no one", () => {
    const form = replyFromEvent(EVENT.reply)!;
    expect(replyAllAddsSomeone(form)).toBe(true);
    const same = replyFromEvent({
      ...EVENT.reply,
      recipients: { reply: { to: ["kim@contoso.test"], cc: [] }, reply_all: { to: ["Kim@contoso.test"], cc: [] } },
    })!;
    expect(replyAllAddsSomeone(same)).toBe(false);
  });
});

describe("the answer of Send and Save to Drafts", () => {
  const form = replyFromEvent(EVENT.reply)!;

  it("names the shown list, the choice and the body as typed", () => {
    const a = replyAnswer(form, { action: "send", replyAll: true, body: "  Edited\n\n" })!;
    expect(a.startsWith("APPROVE ")).toBe(true);
    expect(JSON.parse(a.slice("APPROVE ".length))).toEqual({
      reply: {
        action: "send",
        reply_all: true,
        body: "  Edited\n\n",
        to: ["kim@contoso.test", "lee@contoso.test"],
        cc: ["ops@contoso.test"],
      },
    });
  });

  it("holds exactly the keys the server reads", () => {
    const a = replyAnswer(form, { action: "save", replyAll: false, body: "Hi" })!;
    const body = JSON.parse(a.slice("APPROVE ".length));
    expect(Object.keys(body)).toEqual(["reply"]);
    expect(Object.keys(body.reply).sort()).toEqual(["action", "body", "cc", "reply_all", "to"]);
  });

  it("sends nothing for an empty or a long body", () => {
    expect(replyAnswer(form, { action: "send", replyAll: false, body: "  \n" })).toBeNull();
    expect(replyAnswer(form, { action: "send", replyAll: false, body: "x".repeat(MAX_REPLY_BODY + 1) })).toBeNull();
    expect(bodyCanGo("x".repeat(MAX_REPLY_BODY))).toBe(true);
  });

  it("says what each button does, and names no assistant", () => {
    const hint = replyHint(form);
    expect(hint).toContain("Send replies now from Work · jane@acme.test");
    expect(hint).toContain("Save to Drafts");
    expect(hint).toContain("Discard");
    expect(hint).not.toMatch(/jev/i);
  });
});

describe("one card renderer", () => {
  it("ConfirmationCard draws the reply form, and the queue hands it the answer", () => {
    const card = read("components/ConfirmationCard.tsx");
    expect(card).toContain("<ReplyCardForm");
    const queue = read("components/ConfirmationQueue.tsx");
    expect(queue).toMatch(/reply: card\.reply, onReply:/);
  });

  it("no other component draws a reply form", () => {
    const users = ["components/email/EmailToolCards.tsx", "components/genUITemplates.tsx", "components/MessageBubble.tsx"];
    for (const file of users) expect(read(file)).not.toContain("ReplyCardForm");
  });

  it("the form uses the shared controls and the client derives no recipient", () => {
    const form = read("components/ReplyCardForm.tsx");
    expect(form).toContain('from "@/components/ui/Button"');
    expect(form).toContain('from "@/components/ui/Input"');
    expect(form).not.toMatch(/<(button|textarea|input)\b/);
    // The two lists come from the server. No address is split, parsed or added here.
    expect(read("lib/replyCard.ts")).not.toMatch(/\.concat\(|\.push\([^)]*to\b|parseaddr/);
  });
});
