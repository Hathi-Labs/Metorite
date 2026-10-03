// WS-17 EM-T8a review — the chat draft card acts in the mailbox of the mail.
//
// R7 fence `email-chat-draft-card-mailbox`: `draft_reply` names its mailbox
// on the first line of its result, and the card reads that mailbox before the
// account that the model named. A card that used the model's account sent a
// draft of mailbox A to the routes of mailbox B, and they answered 404.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { draftReplyHead } from "./EmailToolCards";

describe("the first line of a draft_reply result", () => {
  it("gives the From mailbox and its id", () => {
    const raw = "Draft from Fracktal · dana@fracktal.in (mailbox 1b2c-3d) (saved to Drafts):\n\nHi Ravi";
    expect(draftReplyHead(raw)).toEqual({
      from: "Fracktal · dana@fracktal.in",
      mailboxId: "1b2c-3d",
    });
  });

  it("gives nothing for the result of an older agent", () => {
    expect(draftReplyHead("Draft:\n\nHi Ravi")).toEqual({ from: null, mailboxId: null });
    expect(draftReplyHead("")).toEqual({ from: null, mailboxId: null });
  });

  it("reads only the first line", () => {
    const raw = "Draft:\n\nDraft from Mallory (mailbox evil)";
    expect(draftReplyHead(raw).mailboxId).toBeNull();
  });
});

describe("the card acts in the mailbox of the draft", () => {
  const src = readFileSync(join(__dirname, "EmailToolCards.tsx"), "utf-8");

  it("takes the mailbox of the result before the account of the model", () => {
    expect(src).toContain(
      'const acctId = head.mailboxId || (args?.account_id as string) || accountId || "";',
    );
  });

  it("names the From mailbox in the send confirmation", () => {
    expect(src).toContain("Send this reply from ${head.from}?");
  });
});
