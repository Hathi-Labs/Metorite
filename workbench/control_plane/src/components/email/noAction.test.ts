// WS-17 EM-T8e-2 — a tool result that did not act is never drawn as done.
//
// R7 fence `email-chat-no-action-card`: a refused send, a question back to
// the member and a cancel each get the no-action card. Before this, the
// generic card put "Email sent" over "Not sent.", which told the member that
// mail went out. The agent strings are read from the agent source, so a change
// of wording there fails here instead of drawing "Email sent" again.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { isThreadRead, noActionOf } from "./EmailToolCards";

const AGENT = join(
  __dirname, "..", "..", "..", "..", "..", "apps", "agents", "agent-email-assistant", "agents.py",
);

describe("a result that did not act", () => {
  it("is a question when the tool asks which mailbox", () => {
    expect(noActionOf("Send from which mailbox? Nothing was sent.\n- Fracktal · dana@fracktal.in")).toBe("question");
    expect(noActionOf("Which mailbox? A rule or a setting belongs to one mailbox")).toBe("question");
  });

  it("is not-sent when the tool refuses a send", () => {
    expect(noActionOf("Not sent. The email m1 is in the mailbox Fracktal · dana@fracktal.in")).toBe("not-sent");
  });

  it("is unchanged when a tool changed nothing", () => {
    expect(noActionOf("Nothing changed. No email accounts are connected.")).toBe("unchanged");
  });

  it("is cancelled when the member declines", () => {
    expect(noActionOf("Send cancelled — the reply was not sent.")).toBe("cancelled");
    expect(noActionOf("Cancelled — nothing was moved to Trash.")).toBe("cancelled");
  });

  it("is nothing for a result that acted", () => {
    expect(noActionOf("Email sent from Fracktal · dana@fracktal.in to ravi@x.in")).toBeNull();
    expect(noActionOf("Draft from Fracktal · dana@fracktal.in (mailbox 1b2c) (saved to Drafts):")).toBeNull();
    expect(noActionOf("")).toBeNull();
    expect(noActionOf(undefined)).toBeNull();
  });
});

describe("the agent speaks the words the card reads", () => {
  const agent = readFileSync(AGENT, "utf-8");

  it("starts a refusal, a question and a cancel with a word the card knows", () => {
    for (const lead of ['"Not sent. ', '"Send from which mailbox? ', '"Which mailbox? ', '"Nothing changed. ', '"Send cancelled — ', '"Cancelled — ']) {
      expect(agent, lead).toContain(lead);
    }
  });

  it("draws the no-action card before any other card", () => {
    const cards = readFileSync(join(__dirname, "EmailToolCards.tsx"), "utf-8");
    // In the card loop: before the list, thread, info and rule branches.
    const loop = cards.slice(cards.indexOf("for (const e of all) {"));
    const at = loop.indexOf("noActionOf(e.result)");
    expect(at).toBeGreaterThan(0);
    for (const branch of ["if (LIST_TOOLS.has(e.name)) {", "if (e.name === READ_THREAD_TOOL) {", "if (INFO_TOOLS.has(e.name)) {"]) {
      expect(at, branch).toBeLessThan(loop.indexOf(branch));
    }
    // And in renderCard, for the callers that reach it alone.
    const render = cards.slice(cards.indexOf("function renderCard("));
    expect(render.indexOf("noActionOf(e.result)")).toBeLessThan(render.indexOf("DRAFT_TOOLS.has(e.name)"));
  });
});

// R7 fence `email-chat-thread-card-mailbox`: the thread card reads the
// thread in the mailbox of the mail, as `read_thread` does in the agent.
describe("the thread card", () => {
  it("reads the thread in the mailbox of the mail", () => {
    const cards = readFileSync(join(__dirname, "EmailToolCards.tsx"), "utf-8");
    expect(cards).toContain("box = single.accountId || box;");
    expect(cards).toContain("await listThread(box, tid)");
    expect(cards).not.toContain("await listThread(acct, tid)");
  });

  it("fetches nothing for a read that refused", () => {
    const cards = readFileSync(join(__dirname, "EmailToolCards.tsx"), "utf-8");
    expect(cards).toContain("if (!emailId && !isThreadRead(event.result)) {");
    expect(isThreadRead("Thread: Quote — 2 message(s), oldest first:")).toBe(true);
    expect(isThreadRead("This thread_id is in 2 mailboxes.")).toBe(false);
  });
});
