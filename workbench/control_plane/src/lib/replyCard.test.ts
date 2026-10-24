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
  replySummary,
  leftOut,
  leftOutText,
  lineCount,
  markFor,
  onlySenderTicks,
  replyAllTicks,
  sendsCount,
  startTicks,
  tickAllTicks,
  tickedFor,
  toggleTick,
  withBcc,
  addBcc,
  BCC_LABEL,
} from "@/lib/replyCard";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8").replace(/\r\n/g, "\n");

const EVENT = {
  title: "Reply to Kim <kim@contoso.test>?",
  detail: "From Work · jane@acme.test · Re: Quote",
  request_id: "rid-1",
  reply: {
    message_id: "0b0b0b0b-0000-4000-8000-00000000000b",
    notices: ["Reply goes to desk@contoso.test, not to the sender kim@contoso.test."],
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

  it("is no form when it names no email", () => {
    expect(replyFromEvent({ ...EVENT.reply, message_id: "" })).toBeNull();
    expect(replyFromEvent({ ...EVENT.reply, message_id: undefined })).toBeNull();
  });

  it("keeps each notice of the server whole", () => {
    expect(replyFromEvent(EVENT.reply)!.notices).toEqual([
      "Reply goes to desk@contoso.test, not to the sender kim@contoso.test.",
    ]);
  });

  it("is no form when a list has no recipient or is not a list", () => {
    expect(replyFromEvent({ ...EVENT.reply, recipients: { reply: { to: [] }, reply_all: { to: ["a@b.test"] } } })).toBeNull();
    expect(replyFromEvent({ ...EVENT.reply, recipients: { reply: { to: "kim@contoso.test" }, reply_all: { to: [] } } })).toBeNull();
    expect(replyFromEvent("Reply")).toBeNull();
  });

  it("travels on the queue's card, and a broken form stays a reply card with no form", () => {
    expect(cardFromEvent(EVENT)!.reply).toEqual(replyFromEvent(EVENT.reply, "rid-1"))  // §21.14: the card id rides on the form;
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
        bcc: [],
        message_id: "0b0b0b0b-0000-4000-8000-00000000000b",
      },
    });
  });

  it("holds exactly the keys the server reads", () => {
    const a = replyAnswer(form, { action: "save", replyAll: false, body: "Hi" })!;
    const body = JSON.parse(a.slice("APPROVE ".length));
    expect(Object.keys(body)).toEqual(["reply"]);
    // §21.14: `bcc` is the one key that the server may also read.
    expect(Object.keys(body.reply).sort()).toEqual(["action", "bcc", "body", "cc", "message_id", "reply_all", "to"]);
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

describe("nothing on the card hides (security review of #1191)", () => {
  const form = replyFromEvent(EVENT.reply)!;

  it("the summary names each address the reply goes to, in full", () => {
    expect(replySummary(form, false)).toBe("Reply to kim@contoso.test");
    expect(replySummary(form, true)).toBe("Reply to kim@contoso.test, lee@contoso.test");
  });

  it("counts the lines of the body", () => {
    expect(lineCount("a\nb\n\nc")).toBe(4);
  });

  it("the text box grows to the whole text, and the card draws each notice", () => {
    const src = read("components/ReplyCardForm.tsx");
    expect(src).toMatch(/el\.style\.height = `\$\{el\.scrollHeight/);
    expect(src).toContain("rows={Math.max(4, lines)}");
    expect(src).toContain("resize-none overflow-hidden");
    expect(src).not.toMatch(/max-h-|overflow-y-auto/);
    expect(src).toContain("form.notices.map");
    expect(src).toContain("data-reply-lines");
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

describe("the tick boxes (owner, 2026-10-24)", () => {
  const form = replyFromEvent(EVENT.reply)!;

  it("starts each person ticked when the server names no start", () => {
    const t = startTicks(form);
    expect(t.reply).toEqual(["kim@contoso.test"]);
    expect(t.reply_all).toEqual(["kim@contoso.test", "lee@contoso.test", "ops@contoso.test"]);
    expect(sendsCount(form, true, t)).toBe("Sends to 3 of 3");
  });

  it("starts as the server chose, and a new address starts unticked", () => {
    const held = replyFromEvent({
      ...EVENT.reply,
      reply_all: true,
      classes: { "kim@contoso.test": "thread", "lee@contoso.test": "new", "ops@contoso.test": "org" },
      ticked: {
        reply: { to: ["kim@contoso.test"], cc: [] },
        reply_all: { to: ["kim@contoso.test"], cc: ["ops@contoso.test"] },
      },
    })!;
    const t = startTicks(held);
    expect(tickedFor(held, true, t)).toEqual({ to: ["kim@contoso.test"], cc: ["ops@contoso.test"] });
    expect(sendsCount(held, true, t)).toBe("Sends to 2 of 3");
    expect(replySummary(held, true, t)).toBe("Reply to kim@contoso.test");
  });

  it("a start that names a person off the list is a form the card cannot read", () => {
    const bad = { ...EVENT.reply, ticked: { reply: { to: ["spy@evil.test"], cc: [] }, reply_all: { to: [], cc: [] } } };
    expect(replyFromEvent(bad)).toBeNull();
  });

  it("the answer names exactly the ticked people, in the server's order", () => {
    let t = startTicks(form);
    t = toggleTick(t, true, "kim@contoso.test");
    t = toggleTick(t, true, "kim@contoso.test");
    t = toggleTick(t, true, "LEE@contoso.test");
    const a = replyAnswer(form, { action: "send", replyAll: true, body: "Hi", ticks: t })!;
    const reply = JSON.parse(a.slice("APPROVE ".length)).reply;
    expect(reply.to).toEqual(["kim@contoso.test"]);
    expect(reply.cc).toEqual(["ops@contoso.test"]);
  });

  it("a tick of one choice does not change the other", () => {
    const t = toggleTick(startTicks(form), true, "kim@contoso.test");
    expect(t.reply).toEqual(["kim@contoso.test"]);
  });

  it("no ticked To sends nothing", () => {
    const t = toggleTick(startTicks(form), false, "kim@contoso.test");
    expect(replyAnswer(form, { action: "send", replyAll: false, body: "Hi", ticks: t })).toBeNull();
    expect(replySummary(form, false, t)).toMatch(/cannot go/);
  });

  it("the form draws a shared Checkbox for each person, and counts them", () => {
    const src = read("components/ReplyCardForm.tsx");
    expect(src).toContain('from "@/components/ui/Checkbox"');
    expect(src).toContain("data-reply-tick");
    expect(src).toContain("data-reply-count");
    expect(src).toContain("sendsCount(form, replyAll, ticks)");
  });
});

describe("one list, words on each person (owner, 2026-10-24)", () => {
  const base = {
    ...EVENT.reply,
    reply_all: true,
    recipients: {
      reply: { to: ["sagar.kg@jainuniversity.ac.in"], cc: ["kirankumar@fracktal.in"] },
      reply_all: {
        to: ["sagar.kg@jainuniversity.ac.in"],
        cc: ["ananda.babu@jainuniversity.ac.in", "kirankumar@fracktal.in"],
      },
    },
    marks: {
      "ananda.babu@jainuniversity.ac.in": "on Dr Sagar's original email",
      "kirankumar@fracktal.in": "you asked to add them",
      "spy@evil.test": "not on the card",
    },
  };

  it("reads the server's words, only for a person of the list", () => {
    const form = replyFromEvent(base)!;
    expect(markFor(form, "Ananda.Babu@jainuniversity.ac.in")).toBe("on Dr Sagar's original email");
    expect(form.marks).not.toHaveProperty("spy@evil.test");
  });

  it("the shortcuts change the ticks and never the list", () => {
    const form = replyFromEvent(base)!;
    let t = startTicks(form);
    expect(leftOutText(form, t)).toBe("");
    t = onlySenderTicks(form, t);
    expect(tickedFor(form, true, t)).toEqual({ to: ["sagar.kg@jainuniversity.ac.in"], cc: [] });
    expect(leftOut(form, t)).toEqual(["ananda.babu@jainuniversity.ac.in", "kirankumar@fracktal.in"]);
    expect(leftOutText(form, t)).toBe(
      "ananda.babu@jainuniversity.ac.in, kirankumar@fracktal.in will not get this.",
    );
    t = tickAllTicks(form, t);
    const a = replyAnswer(form, { action: "send", replyAll: true, body: "Hi", ticks: t })!;
    expect(JSON.parse(a.slice("APPROVE ".length)).reply.cc).toEqual([
      "ananda.babu@jainuniversity.ac.in",
      "kirankumar@fracktal.in",
    ]);
  });

  it("Reply all ticks each person but a new one, and the answer is the ticks", () => {
    const held = replyFromEvent({
      ...base,
      classes: {
        "sagar.kg@jainuniversity.ac.in": "thread",
        "ananda.babu@jainuniversity.ac.in": "new",
        "kirankumar@fracktal.in": "org",
      },
    })!;
    let t = onlySenderTicks(held, startTicks(held));
    t = replyAllTicks(held, t);
    expect(tickedFor(held, true, t)).toEqual({
      to: ["sagar.kg@jainuniversity.ac.in"],
      cc: ["kirankumar@fracktal.in"],
    });
    // An earlier Reply never wins over the ticks of this card (item 10).
    t = toggleTick(onlySenderTicks(held, t), true, "kirankumar@fracktal.in");
    const a = replyAnswer(held, { action: "send", replyAll: true, body: "Hi", ticks: t })!;
    expect(JSON.parse(a.slice("APPROVE ".length)).reply.cc).toEqual(["kirankumar@fracktal.in"]);
  });

  it("the switch changes only the ticks, and the card names who will not get it", () => {
    const src = read("components/ReplyCardForm.tsx");
    expect(src).toContain('data-reply-mode="reply"');
    expect(src).toContain('data-reply-mode="reply-all"');
    expect(src).not.toContain("setReplyAll");
    // One list: the answer always reads the ticks of the whole list.
    expect(src).toContain("const replyAll = true;");
    expect(src).toContain("setTicks((t) => onlySenderTicks(form, t))");
    expect(src).toContain("setTicks((t) => replyAllTicks(form, t))");
    expect(src).toContain("data-reply-left-out");
    expect(src).toContain("markFor(form, a)");
  });
});


describe("a Bcc on the reply card (§21.14)", () => {
  const BCC_EVENT = {
    ...EVENT.reply,
    recipients: {
      reply: { to: ["kim@contoso.test"], cc: [], bcc: ["veena@acme.test"] },
      reply_all: { to: ["kim@contoso.test"], cc: ["ops@contoso.test"], bcc: ["veena@acme.test"] },
    },
    ticked: {
      reply: { to: ["kim@contoso.test"], cc: [], bcc: ["veena@acme.test"] },
      reply_all: { to: ["kim@contoso.test"], cc: ["ops@contoso.test"], bcc: ["veena@acme.test"] },
    },
    marks: { "veena@acme.test": "added to this reply" },
  };

  it("reads the server's Bcc, its tick and its words", () => {
    const form = replyFromEvent(BCC_EVENT, "rid-9")!;
    expect(form.recipients.reply_all.bcc).toEqual(["veena@acme.test"]);
    expect(startTicks(form).reply_all).toContain("veena@acme.test");
    expect(markFor(form, "veena@acme.test")).toBe("added to this reply");
    expect(form.requestId).toBe("rid-9");
  });

  it("the answer names the ticked Bcc, and an unticked one does not go", () => {
    const form = replyFromEvent(BCC_EVENT)!;
    const on = JSON.parse(replyAnswer(form, { action: "send", replyAll: true, body: "Hi", ticks: startTicks(form) })!.slice(8));
    expect(on.reply.bcc).toEqual(["veena@acme.test"]);
    const off = toggleTick(startTicks(form), true, "veena@acme.test");
    const a = JSON.parse(replyAnswer(form, { action: "send", replyAll: true, body: "Hi", ticks: off })!.slice(8));
    expect(a.reply.bcc).toEqual([]);
    expect(leftOut(form, off)).toEqual(["veena@acme.test"]);
  });

  it("a Bcc tick that names a person off the list is a form the card cannot read", () => {
    const bad = { ...BCC_EVENT, ticked: { ...BCC_EVENT.ticked, reply_all: { ...BCC_EVENT.ticked.reply_all, bcc: ["spy@evil.test"] } } };
    expect(replyFromEvent(bad)).toBeNull();
  });

  it("the shortcuts keep the Bcc ticks as they are", () => {
    const form = replyFromEvent(BCC_EVENT)!;
    expect(onlySenderTicks(form, startTicks(form)).reply_all).toEqual(["kim@contoso.test", "veena@acme.test"]);
    const off = toggleTick(startTicks(form), true, "veena@acme.test");
    expect(tickAllTicks(form, off).reply_all).not.toContain("veena@acme.test");
    expect(sendsCount(form, true, startTicks(form))).toBe("Sends to 3 of 3");
  });

  it("a Bcc that the server took joins both lists once", () => {
    const form = replyFromEvent(EVENT.reply, "rid-1")!;
    const once = withBcc(form, "geo@acme.test", "added to this reply", "org");
    expect(once.recipients.reply_all.bcc).toEqual(["geo@acme.test"]);
    expect(withBcc(once, "GEO@acme.test", "").recipients.reply_all.bcc).toEqual(["geo@acme.test"]);
    expect(tickedFor(once, true, startTicks(once)).bcc).toEqual(["geo@acme.test"]);
  });

  it("a typed Bcc goes to the server with the card's id, and a refusal shows its words", async () => {
    const form = replyFromEvent(EVENT.reply, "rid-1")!;
    const calls: [string, RequestInit][] = [];
    const ok = (async (url: string, init: RequestInit) => {
      calls.push([url, init]);
      return new Response(JSON.stringify({ address: "geo@acme.test", class: "org", mark: "added to this reply" }), { status: 200 });
    }) as unknown as typeof fetch;
    const res = await addBcc(form, "geo@acme.test", ok);
    expect(res).toEqual({ ok: true, address: "geo@acme.test", cls: "org", mark: "added to this reply" });
    expect(calls[0][0]).toBe("/api/email/reply-card/bcc");
    expect(JSON.parse(String(calls[0][1].body))).toEqual({
      message_id: form.messageId, card_request_id: "rid-1", address: "geo@acme.test" });
    const refused = (async () => new Response(JSON.stringify({ detail: "This chat read outside mail." }), { status: 422 })) as unknown as typeof fetch;
    expect(await addBcc(form, "spy@evil.test", refused)).toEqual({ ok: false, error: "This chat read outside mail." });
    expect((await addBcc(replyFromEvent(EVENT.reply)!, "x@y.test", ok)).ok).toBe(false);
  });

  it("the form labels the Bcc row and uses the shared controls", () => {
    const src = read("components/ReplyCardForm.tsx");
    expect(src).toContain('label="Bcc"');
    expect(src).toContain("BCC_LABEL");
    expect(BCC_LABEL).toBe("Bcc (hidden from the others)");
    expect(src).toContain("<Input");
    expect(src).not.toMatch(/<input\b/);
  });
});
