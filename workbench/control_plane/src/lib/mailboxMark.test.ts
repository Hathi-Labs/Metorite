// D99, navigation_shell.md §7.1.1 rule 6 — the one assistant's mailbox mark.
//
// R7 fence `one-assistant-mailbox-mark`: an `email_mailbox_mark` custom event
// on a saved turn draws "Now working on <label>" above that turn, in the
// same idiom as the Email app's own switch. The fold of raw custom events
// does not show it a second time. There is no picker.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import MailboxMark from "@/components/MailboxMark";
import { MAILBOX_MARK_EVENT, mailboxMarksOf } from "@/lib/mailboxMark";

const src = (p: string) => readFileSync(join(__dirname, "..", "..", p), "utf-8");

describe("mailboxMarksOf", () => {
  it("gives the label of each mark of the turn, in order", () => {
    expect(mailboxMarksOf([
      { name: "artifact_created", value: { path: "x" } },
      { name: MAILBOX_MARK_EVENT, value: { label: "Personal", account_id: "a2" } },
      { name: MAILBOX_MARK_EVENT, value: { label: "  Work\n  ", account_id: "a1" } },
    ])).toEqual(["Personal", "Work"]);
  });

  it("gives nothing for a turn with no mark, or a mark with no label", () => {
    expect(mailboxMarksOf(undefined)).toEqual([]);
    expect(mailboxMarksOf([{ name: MAILBOX_MARK_EVENT, value: null }])).toEqual([]);
  });

  it("keeps the event name equal to the server's", () => {
    const py = src("../../packages/acb_skills/acb_skills/email_chat_context.py");
    expect(py).toContain(`MAILBOX_MARK_EVENT = "${MAILBOX_MARK_EVENT}"`);
  });
});

describe("the mark draws as the Email app's switch does", () => {
  it("renders one line with the label", () => {
    const html = renderToStaticMarkup(createElement(MailboxMark, { label: "Personal" }));
    expect(html).toContain("data-mailbox-mark");
    expect(html.replace(/<!-- -->/g, "")).toContain("Now working on Personal");
  });

  it("AgentChat draws the saved mark above its turn, and the panel hides it", () => {
    const chat = src("src/components/AgentChat.tsx");
    expect(chat).toContain("mailboxMarksOf(msg.customEvents)");
    expect(chat.indexOf("mailboxMarksOf(msg.customEvents)")).toBeLessThan(
      chat.indexOf("<MessageBubble message={msg}"),
    );
    expect(src("src/components/GenerativeUIPanel.tsx")).toContain('"email_mailbox_mark"');
  });
});
