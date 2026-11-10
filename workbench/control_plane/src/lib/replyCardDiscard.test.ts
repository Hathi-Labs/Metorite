// The member's own Discard of a reply card's draft (owner, 2026-10-22).
//
// The tool cannot tell a Discard from a timeout, so it trashes nothing. The
// card's Discard button posts the trash on the member's click, naming the
// one draft that the card wrote. R7: these tests fail if the click stops
// posting, or if a card with no draft id posts anything.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";

import { discardReplyDraft, replyFromEvent, type ReplyForm } from "@/lib/replyCard";

const VALUE = {
  message_id: "m-1",
  recipients: { reply: { to: ["kim@contoso.test"], cc: [] }, reply_all: { to: ["kim@contoso.test"], cc: [] } },
  body: "Hi Kim",
  draft_id: "00000000-0000-4000-8000-000000000001",
};

describe("the reply card's draft id", () => {
  it("travels from the event to the form", () => {
    expect(replyFromEvent(VALUE)!.draftId).toBe(VALUE.draft_id);
    expect(replyFromEvent({ ...VALUE, draft_id: undefined })!.draftId).toBe("");
  });

  it("the member's Discard posts the card's draft only", async () => {
    const post = vi.fn(async () => new Response("{}"));
    await discardReplyDraft(replyFromEvent(VALUE)!, post as unknown as typeof fetch);
    expect(post).toHaveBeenCalledTimes(1);
    const [url, init] = post.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/email/reply-card/discard");
    expect(JSON.parse(String(init.body))).toEqual({ message_id: "m-1", draft_id: VALUE.draft_id });
  });

  it("a card with no draft posts nothing, and a failure throws nothing", async () => {
    const post = vi.fn(async () => {
      throw new Error("offline");
    });
    const form = replyFromEvent(VALUE)!;
    await discardReplyDraft({ ...form, draftId: "" } as ReplyForm, post as unknown as typeof fetch);
    expect(post).not.toHaveBeenCalled();
    await expect(discardReplyDraft(form, post as unknown as typeof fetch)).resolves.toBeUndefined();
  });

  it("the Discard button calls it before it ends the card", () => {
    const src = readFileSync(fileURLToPath(new URL("../components/ReplyCardForm.tsx", import.meta.url)), "utf-8");
    expect(src).toMatch(/void discardReplyDraft\(form\);\s*onDiscard\(\);/);
  });
});
