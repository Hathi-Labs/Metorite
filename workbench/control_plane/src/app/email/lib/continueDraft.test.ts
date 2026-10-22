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

import { continuedBody, draftToContinue } from "./draftAutosave";

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
    expect(start).toContain("draftToContinue(thread, composerDrafts.gone, isDraftEmail)");
    expect(start).toContain("draftIdRef.current = held?.id ?? null;");
    expect(start).toContain("ownDraft(newReplyDrafts(d), held.id)");
    expect(start).toContain("continuedBody(held.bodyText");
  });

  it("never continues a reply draft for a forward", () => {
    expect(start).toMatch(/mode === "forward" \? null : draftToContinue/);
  });
});
