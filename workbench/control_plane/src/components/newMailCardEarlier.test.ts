/**
 * Task 3978 (`email_app_master_plan.md` §18.13.1): Discard of a new-mail card
 * asks "Keep the earlier draft, or delete it?" on the same card, when the
 * form names a draft of this email that the chat saved earlier.
 *
 * The rules this file holds (R7). Each test names the mutation that turns it red.
 *
 * 1. The card reads `earlier` of a new-mail form only.
 * 2. Discard asks only when the form names an earlier draft AND the card can
 *    carry the answer. Else Discard is the Discard of before.
 * 3. The question names the draft. Keep is the plain Discard, Delete sends the
 *    delete answer, and Back sends nothing.
 * 4. The delete answer is a decline, so it can never read as an approval.
 */
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { EarlierQuestion, discardAsks } from "@/components/NewMailCardForm";
import { DELETE_EARLIER_ANSWER, EARLIER_QUESTION, replyFromEvent } from "@/lib/replyCard";

const PEOPLE = { to: ["salil@client.test"], cc: [] };
const VALUE = {
  kind: "new",
  message_id: "",
  mailbox: "Work · jane@acme.test",
  subject: "Proposal",
  body: "Dear Salil,",
  recipients: { reply: PEOPLE, reply_all: PEOPLE },
  files: [],
  notices: [],
  note: "",
};
const LINE = "To salil@client.test: Proposal · saved 2026-11-26 06:05 · starts: Dear Salil,";

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

describe("the form names the earlier draft", () => {
  it("reads earlier on a new mail only", () => {
    // Mutation: drop `earlier` from replyFromEvent → red.
    expect(replyFromEvent({ ...VALUE, earlier: ` ${LINE} ` })?.earlier).toBe(LINE);
    expect(replyFromEvent(VALUE)?.earlier).toBeUndefined();
    expect(replyFromEvent({ ...VALUE, earlier: 7 })?.earlier).toBeUndefined();
    // A reply form never carries it.
    const reply = replyFromEvent({ ...VALUE, kind: undefined, message_id: "m1", earlier: LINE });
    expect(reply?.earlier).toBeUndefined();
  });
});

describe("Discard asks first", () => {
  const withEarlier = replyFromEvent({ ...VALUE, earlier: LINE })!;
  const plain = replyFromEvent(VALUE)!;

  it("asks only with an earlier draft and a way to answer", () => {
    // Mutation: ask on every Discard → red.
    expect(discardAsks(withEarlier, () => {})).toBe(true);
    expect(discardAsks(plain, () => {})).toBe(false);
    expect(discardAsks(withEarlier, undefined)).toBe(false);
  });

  it("names the draft, and each button does its one act", () => {
    const keep = vi.fn();
    const del = vi.fn();
    const back = vi.fn();
    const tree = EarlierQuestion({ line: LINE, onKeep: keep, onDelete: del, onBack: back });
    const html = renderToStaticMarkup(createElement(EarlierQuestion, { line: LINE, onKeep: keep, onDelete: del, onBack: back }));
    expect(html).toContain(EARLIER_QUESTION);
    expect(html).toContain("To salil@client.test: Proposal");
    expect(html).toContain('role="group"');
    const button = (mark: string) =>
      elements(tree).find((e) => (e.props as Record<string, unknown>)[mark] !== undefined)!;
    (button("data-earlier-keep").props as { onClick: () => void }).onClick();
    expect([keep.mock.calls.length, del.mock.calls.length, back.mock.calls.length]).toEqual([1, 0, 0]);
    (button("data-earlier-delete").props as { onClick: () => void }).onClick();
    expect(del).toHaveBeenCalledTimes(1);
    (button("data-earlier-back").props as { onClick: () => void }).onClick();
    expect(back).toHaveBeenCalledTimes(1);
  });
});

describe("the delete answer", () => {
  it("is a decline, never an approval", () => {
    // Mutation: start it with APPROVE → red. The server parses no approval from it.
    expect(DELETE_EARLIER_ANSWER.startsWith("REJECT ")).toBe(true);
    expect(DELETE_EARLIER_ANSWER).toBe('REJECT {"earlier":"delete"}');
  });
});
