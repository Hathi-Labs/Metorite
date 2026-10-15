// WS-17 EM-T8a review — the chat draft card acts in the mailbox of the mail.
//
// R7 fence `email-chat-draft-card-mailbox`: `draft_reply` names its mailbox
// on the first line of its result, and the card reads that mailbox before the
// account that the model named. A card that used the model's account sent a
// draft of mailbox A to the routes of mailbox B, and they answered 404.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { draftCardStart, draftReplyHead } from "./EmailToolCards";

describe("the first line of a draft_reply result", () => {
  it("gives the From mailbox and its id", () => {
    const raw = "Draft from Fracktal · dana@fracktal.in (mailbox 1b2c-3d) (saved to Drafts):\n\nHi Ravi";
    expect(draftReplyHead(raw)).toEqual({
      from: "Fracktal · dana@fracktal.in",
      mailboxId: "1b2c-3d",
      saved: true,
    });
  });

  it("gives nothing for the result of an older agent", () => {
    expect(draftReplyHead("Draft:\n\nHi Ravi")).toEqual({ from: null, mailboxId: null, saved: false });
    expect(draftReplyHead("")).toEqual({ from: null, mailboxId: null, saved: false });
  });

  it("reads only the first line", () => {
    const raw = "Draft:\n\nDraft from Mallory (mailbox evil)";
    expect(draftReplyHead(raw).mailboxId).toBeNull();
  });
});

// WS-17 EM-G11c1b (§12.3.13.6b) — a draft that the tool saved starts saved.
//
// R7 fence `email-chat-draft-card-saved`: after a yes on the card,
// `draft_reply` saves a provider draft and its first line says "(saved to
// Drafts)". A card that started `idle` then still offered Save and Send, and
// each one made a second provider draft (`POST /email/drafts/save`).
describe("a draft that the tool saved starts as saved", () => {
  const SAVED = "Draft from Work · jane@acme.test (mailbox acc-1) (saved to Drafts):\n\nHi";
  const UNSAVED =
    "Draft from Work · jane@acme.test (mailbox acc-1) (Drafts is unchanged, because a card " +
    "cannot show here. Save or send this draft in the Email app):\n\nHi";
  const PLAIN = "Draft from Work · jane@acme.test (mailbox acc-1):\n\nHi";

  it("reads saved from the first line only", () => {
    expect(draftReplyHead(SAVED).saved).toBe(true);
    expect(draftReplyHead(UNSAVED).saved).toBe(false);
    expect(draftReplyHead(PLAIN).saved).toBe(false);
    expect(draftReplyHead("Draft from X (mailbox a):\n\nI wrote (saved to Drafts) here").saved).toBe(false);
  });

  it("does not read saved from a mailbox label that holds the words (review round 1)", () => {
    // A member may name a mailbox anything. Its label sits in the head too.
    const label = "Draft from Ops (saved to Drafts) · ops@acme.test (mailbox acc-2)";
    expect(draftReplyHead(`${label}:\n\nHi`).saved).toBe(false);
    expect(draftCardStart(`${label}:\n\nHi`)).toBe("idle");
    expect(draftReplyHead(
      `${label} (Drafts is unchanged, because a card cannot show here. ` +
        "Save or send this draft in the Email app):\n\nHi",
    ).saved).toBe(false);
    // The same label on a draft that the tool did save.
    expect(draftCardStart(`${label} (saved to Drafts):\n\nHi`)).toBe("saved");
  });

  it("starts the card saved or idle", () => {
    expect(draftCardStart(SAVED)).toBe("saved");
    expect(draftCardStart(UNSAVED)).toBe("idle");
    expect(draftCardStart(PLAIN)).toBe("idle");
  });

  it("starts the card in the state that draftCardStart gives", () => {
    const src = readFileSync(join(__dirname, "EmailToolCards.tsx"), "utf-8");
    expect(src).toMatch(/useState<[^>]*>\(\s*\(\) => draftCardStart\(raw\),?\s*\)/);
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
