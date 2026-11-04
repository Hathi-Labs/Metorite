/**
 * The one email card of a NEW mail (`email_app_master_plan.md` §18.10, owner
 * 2026-11-03).
 *
 * The rules this file holds (R7). Each test names the mutation that turns it red.
 *
 * 1. The card reads a new-mail form, which names no email that it answers.
 * 2. The answer holds exactly the keys that the server reads, the people the
 *    member left ticked, and the subject and the text as typed. An empty
 *    text, a subject with a line break and no ticked To send nothing.
 * 3. The card draws the subject field, the text box, the nudge line with two
 *    chips, and Send, Save draft and Discard. It has no box to type a person.
 * 4. A chip puts its words in the chat box and sends nothing.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import ConfirmationCard from "@/components/ConfirmationCard";
import NewMailCardForm, { NudgeLine } from "@/components/NewMailCardForm";
import Button from "@/components/ui/Button";
import {
  MAX_SUBJECT,
  NUDGE_CHIPS,
  NUDGE_EDITED,
  isNewMail,
  newMailEdited,
  newMailLimitLine,
  newMailAnswer,
  newMailSummary,
  replyFromEvent,
  startFileTicks,
  startTicks,
  subjectCanGo,
  toggleFileTick,
  toggleTick,
} from "@/lib/replyCard";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8").replace(/\r\n/g, "\n");

const PEOPLE = { to: ["kim@contoso.test"], cc: ["geo@acme.test"] };
const VALUE = {
  kind: "new",
  message_id: "",
  mailbox: "Work · jane@acme.test",
  subject: "Rates",
  body: "Hi Kim,\n\nThe rates are attached.",
  recipients: { reply: PEOPLE, reply_all: PEOPLE },
  files: ['File: "rates.pdf" (4 KB)'],
  notices: [],
  note: "",
};

function elements(node: ReactNode): ReactElement[] {
  const out: ReactElement[] = [];
  const walk = (n: ReactNode) => {
    if (Array.isArray(n)) n.forEach(walk);
    else if (isValidElement(n)) {
      out.push(n);
      walk((n.props as { children?: ReactNode }).children);
    }
  };
  walk(node);
  return out;
}

describe("the card reads a new-mail form", () => {
  it("takes a form that names no email, and keeps its files", () => {
    // Mutation: require message_id for every form → red.
    const form = replyFromEvent(VALUE, "f".repeat(32));
    expect(form).not.toBeNull();
    expect(isNewMail(form)).toBe(true);
    expect(form?.files).toEqual(['File: "rates.pdf" (4 KB)']);
    // A reply form still needs the email it answers.
    expect(replyFromEvent({ ...VALUE, kind: undefined })).toBeNull();
  });
});

describe("the answer of a new-mail card", () => {
  const form = replyFromEvent(VALUE)!;
  const ticks = startTicks(form);

  it("holds exactly the keys the server reads, and the edits as typed", () => {
    // Mutation: send form.body in place of the typed text → red.
    const a = newMailAnswer(form, { action: "send", subject: "Rates for May", body: "Edited.  ", ticks });
    expect(a).not.toBeNull();
    const reply = JSON.parse(a!.slice("APPROVE ".length)).reply;
    expect(Object.keys(reply).sort()).toEqual(["action", "bcc", "body", "cc", "kind", "subject", "to"]);
    expect(reply).toEqual({
      kind: "new", action: "send", subject: "Rates for May", body: "Edited.  ",
      to: ["kim@contoso.test"], cc: ["geo@acme.test"], bcc: [],
    });
  });

  it("names only the people the member left ticked", () => {
    // Mutation: name the server's whole list → red.
    const off = toggleTick(ticks, true, "geo@acme.test");
    const reply = JSON.parse(newMailAnswer(form, { action: "save", subject: "Rates", body: "x", ticks: off })!.slice(8)).reply;
    expect(reply.cc).toEqual([]);
    expect(reply.action).toBe("save");
  });

  it("sends nothing with no text, a bad subject or no ticked To", () => {
    expect(newMailAnswer(form, { action: "send", subject: "Rates", body: "   ", ticks })).toBeNull();
    expect(newMailAnswer(form, { action: "send", subject: "Rates\nBcc: x@evil.example", body: "x", ticks })).toBeNull();
    // Fix round 1: the Unicode line and paragraph separators break a line too.
    expect(newMailAnswer(form, { action: "send", subject: "Rates\u2028Bcc: x", body: "x", ticks })).toBeNull();
    expect(newMailAnswer(form, { action: "send", subject: "Rates\u2029Bcc: x", body: "x", ticks })).toBeNull();
    expect(subjectCanGo("\u26a0\ufe0f Invoice due")).toBe(true);
    expect(newMailAnswer(form, { action: "send", subject: "x".repeat(MAX_SUBJECT + 1), body: "x", ticks })).toBeNull();
    const noTo = toggleTick(ticks, true, "kim@contoso.test");
    expect(newMailAnswer(form, { action: "send", subject: "Rates", body: "x", ticks: noTo })).toBeNull();
    expect(newMailSummary(form, noTo)).toBe("No one in To is ticked, so the email cannot go");
  });

  it("is never built for a reply form", () => {
    const reply = { ...form, kind: undefined, messageId: "m-1" };
    expect(newMailAnswer(reply, { action: "send", subject: "x", body: "x", ticks })).toBeNull();
    expect(subjectCanGo("")).toBe(true);
  });
});

describe("the card draws the parts the owner approved", () => {
  const form = replyFromEvent(VALUE)!;
  const noop = () => {};
  const html = renderToStaticMarkup(
    createElement(NewMailCardForm, { form, onAnswer: noop, onDiscard: noop, onSuggest: noop, armed: true }),
  );

  it("draws the subject field, the text box, the nudge chips and the three buttons", () => {
    // Mutation: drop any part → red.
    expect(html).toMatch(/<input[^>]*aria-label="Subject"[^>]*value="Rates"/);
    expect(html).toMatch(/<textarea[^>]*aria-label="Email text"/);
    expect(html).toContain("Want it changed?");
    expect(html).toContain("Tell me in the chat below.");
    for (const chip of NUDGE_CHIPS) expect(html).toContain(`data-nudge-chip="${chip}"`);
    expect(NUDGE_CHIPS).toEqual(["make it shorter", "make it more formal"]);
    expect(html).toContain("data-reply-send");
    expect(html).toContain("Save draft");
    expect(html).toContain("data-reply-discard");
    expect(html).toContain("rates.pdf");
    // The send adds the mailbox signature after the card, and the card says so.
    expect(html).toContain("Your mailbox signature, if you set one, goes below the text.");
    expect(html).toContain("data-reply-tick=\"kim@contoso.test\"");
  });

  it("has no box to type a person: a new person is asked for in the chat", () => {
    // Owner decision. Mutation: add the Bcc box of the reply card → red.
    expect(html).not.toContain("data-reply-bcc-add");
    expect(html).not.toMatch(/type="email"/);
    expect(html).toContain("To add a person, ask in the chat.");
  });

  it("is what ConfirmationCard draws for a new-mail form, under the title New email", () => {
    const card = renderToStaticMarkup(
      createElement(ConfirmationCard, { title: "Email to kim@contoso.test", onApprove: noop, onReject: noop, reply: form }),
    );
    expect(card).toContain("data-new-mail-card");
    expect(card).toContain("New email");
    expect(card).not.toContain("data-reply-mode");
  });
});

describe("the note and the text limit of a new-mail card (fix round 1, P3)", () => {
  it("keeps a long note whole, so no look-alike warning loses its end", () => {
    // Mutation: read the note with the 500-character cut → red.
    const note = "x@contos0.test: the domain looks like contoso.test, which you know. ".repeat(20).trim();
    expect(replyFromEvent({ ...VALUE, note })!.note).toBe(note);
  });

  it("caps the text at the act's limit, and says so before Send", () => {
    // Mutation: cap the box at MAX_REPLY_BODY on a held card → red.
    const form = replyFromEvent({ ...VALUE, max_body: 3000 })!;
    const ticks = startTicks(form);
    expect(newMailAnswer(form, { action: "send", subject: "Rates", body: "x".repeat(3000), ticks })).not.toBeNull();
    expect(newMailAnswer(form, { action: "send", subject: "Rates", body: "x".repeat(3001), ticks })).toBeNull();
    expect(newMailLimitLine(form, "x".repeat(120))).toBe("This email can hold 3,000 characters of text. It holds 120 now.");
    const html = renderToStaticMarkup(
      createElement(NewMailCardForm, { form, onAnswer: () => {}, onDiscard: () => {}, armed: true }),
    );
    expect(html).toMatch(/<textarea[^>]*maxLength="3000"/i);
    expect(html).toContain("data-new-mail-limit");
    // A card with the full limit draws no limit line.
    expect(newMailLimitLine(replyFromEvent(VALUE)!, "x")).toBe("");
  });
});

describe("the file rows of a held new mail (fix round 1, §21.1 item 3)", () => {
  const ROWS = [
    { id: "file-0", label: 'File: "quote.pdf" (4 KB)', hint: 'From the mail "Quote" of your own mailbox.', checked: true },
    { id: "file-1", label: 'File: "rates.xlsx" (2 KB)', hint: "From Knowledge, a file that you can see.", checked: true },
  ];
  const form = replyFromEvent({ ...VALUE, files: [], file_rows: ROWS })!;
  const ticks = startTicks(form);

  it("draws each file with its tick and its hint", () => {
    const html = renderToStaticMarkup(
      createElement(NewMailCardForm, { form, onAnswer: () => {}, onDiscard: () => {}, armed: true }),
    );
    expect(html).toContain('data-new-mail-file-tick="file-0"');
    expect(html).toContain('data-new-mail-file-tick="file-1"');
    expect(html).toContain("From Knowledge, a file that you can see.");
  });

  it("names only the ticked files, and none when each is unticked", () => {
    // Mutation: name every file row whatever the ticks → red.
    const all = JSON.parse(newMailAnswer(form, { action: "send", subject: "Rates", body: "x", ticks })!.slice(8)).reply;
    expect(all.files).toEqual(["file-0", "file-1"]);
    const one = toggleFileTick(startFileTicks(form), "file-0");
    const some = JSON.parse(newMailAnswer(form, { action: "send", subject: "Rates", body: "x", ticks, fileTicks: one })!.slice(8)).reply;
    expect(some.files).toEqual(["file-1"]);
    const none = JSON.parse(newMailAnswer(form, { action: "send", subject: "Rates", body: "x", ticks, fileTicks: [] })!.slice(8)).reply;
    expect(none.files).toEqual([]);
  });

  it("adds no files key to a card with fixed file lines", () => {
    const plain = replyFromEvent(VALUE)!;
    const reply = JSON.parse(newMailAnswer(plain, { action: "send", subject: "Rates", body: "x", ticks: startTicks(plain) })!.slice(8)).reply;
    expect("files" in reply).toBe(false);
  });

  it("refuses a file row it cannot read", () => {
    expect(replyFromEvent({ ...VALUE, file_rows: [{ id: "", label: "x" }] })).toBeNull();
    expect(replyFromEvent({ ...VALUE, file_rows: [ROWS[0], ROWS[0]] })).toBeNull();
  });
});

describe("a chip puts its words in the chat box and sends nothing", () => {
  it("calls only onSuggest, with the chip's words", () => {
    // Mutation: make a chip answer the card, or send → red.
    const onSuggest = vi.fn();
    const chips = elements(NudgeLine({ onSuggest })).filter((el) => el.type === Button);
    expect(chips).toHaveLength(2);
    (chips[0].props as { onClick: () => void }).onClick();
    expect(onSuggest).toHaveBeenCalledTimes(1);
    expect(onSuggest).toHaveBeenCalledWith("make it shorter");
  });

  it("says that the edits go with a chat request, and keeps the chips, once the member edited (§18.11)", () => {
    // A chat message closes the card, and since §18.11 it carries the
    // member's version (`lib/redraftCard.ts`). Fix round 1 of §18.10 hid the
    // chips while the edits were lost. Mutation: hide the chips again, or
    // say that the request starts from the assistant's draft → red.
    const form = replyFromEvent(VALUE)!;
    expect(newMailEdited(form, form.subject, form.body)).toBe(false);
    expect(newMailEdited(form, "Rates for May", form.body)).toBe(true);
    expect(newMailEdited(form, form.subject, `${form.body} More.`)).toBe(true);
    const onSuggest = vi.fn();
    const chips = elements(NudgeLine({ onSuggest, edited: true })).filter((el) => el.type === Button);
    expect(chips).toHaveLength(2);
    const html = renderToStaticMarkup(createElement(NudgeLine, { onSuggest, edited: true }));
    expect(html).toContain(NUDGE_EDITED);
    expect(html).toContain('data-new-mail-nudge="edited"');
    expect(html).not.toContain("Want it changed?");
    expect(NUDGE_EDITED).not.toMatch(/starts again|assistant's draft/);
    // Two short sentences at most, and Save draft keeps the member's version.
    expect(NUDGE_EDITED.split(". ").length).toBeLessThanOrEqual(2);
    expect(NUDGE_EDITED).toContain("Your edits go with a request in the chat.");
    expect(NUDGE_EDITED).toContain("Save draft keeps your version.");
    // The card passes the edit state to the line.
    expect(read("components/NewMailCardForm.tsx")).toContain("edited={newMailEdited(form, subject, body)}");
  });

  it("draws no chip where no chat box can take the words", () => {
    const html = renderToStaticMarkup(createElement(NudgeLine, {}));
    expect(html).toContain("Want it changed?");
    expect(html).not.toContain("data-nudge-chip");
  });

  it("the chat fills its box, focuses it, and never sends from a chip", () => {
    // AgentChat is the one composer seam. Mutation: call submitText → red.
    const chat = read("components/AgentChat.tsx");
    const seam = /const suggestToComposer = useCallback\(\(text: string\) => \{([\s\S]*?)\n {2}\}, \[\]\);/.exec(chat);
    expect(seam, "AgentChat defines suggestToComposer").not.toBeNull();
    expect(seam![1]).toContain("setInput(");
    expect(seam![1]).toContain("inputRef.current?.focus()");
    expect(seam![1]).not.toMatch(/submitText|sendMessage|answerConfirmation|onAnswer/);
    expect(chat).toContain("onSuggest={suggestToComposer}");
  });
});
