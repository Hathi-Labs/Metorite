// One reply draft per conversation (owner directive, 2026-10-22): "We should
// be able to start in chat, and go to email and finish and send."
//
// A reply drafted in the chat lands in Drafts as the one draft of the
// conversation. Reply in the reading pane then continues THAT draft and does
// not start a second one. R7: `draftToContinue` and `continuedBody` hold the
// decision, and the scan below holds the wiring in `startReply`. Vitest runs
// with no DOM here, so it cannot mount EmailDetail.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { continuedBody, draftToContinue, newerDraft } from "./draftAutosave";

type Row = { id: string; receivedAt: string; folder: string };
const isDraft = (m: Row) => m.folder === "drafts";
const row = (id: string, receivedAt: string, folder = "drafts"): Row => ({ id, receivedAt, folder });

describe("draftToContinue", () => {
  it("picks the newest draft of the thread", () => {
    const thread = [
      row("mail", "2026-10-22T08:00:00Z", "inbox"),
      row("old", "2026-10-22T09:00:00Z"),
      row("new", "2026-10-22T10:00:00Z"),
    ];
    expect(draftToContinue(thread, [], isDraft)?.id).toBe("new");
  });

  it("skips a draft that a send or a discard ended", () => {
    const thread = [row("a", "2026-10-22T09:00:00Z"), row("b", "2026-10-22T10:00:00Z")];
    expect(draftToContinue(thread, ["b"], isDraft)?.id).toBe("a");
  });

  it("answers null with no draft or no thread", () => {
    expect(draftToContinue([row("mail", "2026-10-22T08:00:00Z", "inbox")], [], isDraft)).toBeNull();
    expect(draftToContinue(null, [], isDraft)).toBeNull();
  });
});

describe("newerDraft: the one rule of the gateway and the pane", () => {
  it("is the later receivedAt, compared as time, not as text", () => {
    // The same instant in two spellings is a tie, broken by the id.
    const a = { id: "b", receivedAt: "2026-10-22T10:00:00+00:00" };
    const b = { id: "a", receivedAt: "2026-10-22T10:00:00.000Z" };
    expect(newerDraft(a, b)).toBe(true);
    expect(newerDraft(b, a)).toBe(false);
    expect(newerDraft({ id: "a", receivedAt: "2026-10-22T11:00:00Z" }, a)).toBe(true);
  });

  it("matches the ORDER BY of _THREAD_DRAFT_SQL in drafting.py", () => {
    const py = readFileSync(
      join(__dirname, "../../../../../../apps/services/gateway/gateway/routes/email/automation/drafting.py"),
      "utf-8",
    );
    expect(py).toContain('" ORDER BY received_at DESC NULLS LAST, id DESC")');
    // Each save moves received_at, so the draft saved last is the newest.
    expect(py).toContain("received_at = now(),");
  });
});

describe("continuedBody", () => {
  const quote = "On Mon, Kim wrote:\n> Ok?";

  it("takes the quote off a draft that the inline reply saved", () => {
    expect(continuedBody(`Hi Kim\n\n${quote}`, quote)).toEqual({ text: "Hi Kim", quote });
  });

  it("keeps a chat draft whole, with no quote added", () => {
    expect(continuedBody("Hi Kim,\n\nYes.", quote)).toEqual({ text: "Hi Kim,\n\nYes.", quote: "" });
  });
});

describe("startReply wiring", () => {
  const src = readFileSync(join(__dirname, "../components/EmailDetail.tsx"), "utf-8");
  const start = src.slice(src.indexOf("const startReply = ("), src.indexOf("const switchReplyMode"));

  it("seeds the draft id and the composer record from the held draft", () => {
    expect(start).toContain("draftToContinue(thread, composerDrafts.gone, isReplyDraft)");
    expect(start).toContain("draftIdRef.current = held?.id ?? null;");
    expect(start).toContain("ownDraft(newReplyDrafts(d), held.id)");
    expect(start).toContain("continuedBody(held.bodyText");
  });

  it("keeps the Bcc of a continued draft, so the first save does not clear it", () => {
    expect(start).toContain("setReplyBcc(held ? (held.bcc ?? []).map((a) => a.email)");
    expect(start).not.toContain('setReplyBcc("");');
  });

  it("never continues a reply draft for a forward", () => {
    expect(start).toMatch(/mode === "forward" \? null : draftToContinue/);
  });
});

describe("Reply never continues a forward draft (review P2)", () => {
  it("reads a forward subject in English and the common localised prefixes", async () => {
    const { isForwardSubject } = await import("./draftAutosave");
    for (const s of ["Fwd: x", "FW: x", " fw : x", "WG: x", "TR: x", "RV: x", "Enc: x"]) {
      expect(isForwardSubject(s)).toBe(true);
    }
    for (const s of ["Re: Fwd: x", "Re: x", "Forward planning", "", null]) {
      expect(isForwardSubject(s)).toBe(false);
    }
  });

  it("the pane passes the reply-only predicate", () => {
    const src = readFileSync(join(__dirname, "../components/EmailDetail.tsx"), "utf-8");
    expect(src).toContain("draftToContinue(thread, composerDrafts.gone, isReplyDraft)");
    expect(src).toContain("isDraftEmail(m) && !isForwardSubject(m.subject)");
  });
});
