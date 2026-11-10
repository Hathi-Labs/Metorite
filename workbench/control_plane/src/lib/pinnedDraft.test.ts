import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { draftPin, draftsOf } from "./pinnedDraft";

const ev = (id: string, name = "draft_reply", result = "Draft from a:\n\nHello", status = "done") => ({
  id,
  name,
  status,
  result,
});

describe("draftsOf", () => {
  it("lists the done drafts with a body, oldest first, across turns", () => {
    const msgs = [
      { toolEvents: [ev("a"), ev("r", "read_email")] },
      { toolEvents: [ev("b", "draft_email"), ev("c", "draft_reply", "", "done"), ev("d", "draft_reply", "x", "running")] },
    ];
    expect(draftsOf(msgs).map((e) => e.id)).toEqual(["a", "b"]);
  });

  it("drops a duplicate id and a result that did not act", () => {
    const msgs = [{ toolEvents: [ev("a"), ev("a"), ev("q", "draft_reply", "QUESTION")] }];
    expect(draftsOf(msgs, (e) => e.result !== "QUESTION").map((e) => e.id)).toEqual(["a"]);
  });
});

describe("draftPin", () => {
  it("pins the newest draft and marks the older ones replaced", () => {
    const pin = draftPin([ev("a"), ev("b"), ev("c")], null);
    expect(pin.pinned?.id).toBe("c");
    expect([...pin.superseded]).toEqual(["a", "b"]);
  });

  it("a closed pin stays closed until a new draft comes", () => {
    expect(draftPin([ev("a"), ev("b")], "b").pinned).toBeNull();
    expect(draftPin([ev("a"), ev("b"), ev("c")], "b").pinned?.id).toBe("c");
  });

  it("no draft, no pin", () => {
    const pin = draftPin([], null);
    expect(pin.pinned).toBeNull();
    expect(pin.superseded.size).toBe(0);
  });
});

describe("the chat wiring", () => {
  const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf-8");

  it("AgentChat pins no draft above the composer, and scopes the thread (owner, 2026-10-23)", () => {
    const chat = src("components/AgentChat.tsx");
    expect(chat).not.toContain("<PinnedDraftCard");
    expect(chat).toContain("pinnedId: null, superseded: draftPin(drafts, null).superseded");
    expect(chat).toContain("<DraftPinContext.Provider value={pinScope}>");
  });

  it("the thread draws one line for a pinned or replaced draft", () => {
    const cards = src("components/email/EmailToolCards.tsx");
    expect(cards).toMatch(/pin\.pinnedId \|\| pin\.superseded\.has\(e\.id\)/);
  });

  it("the run status draws once, above the composer", () => {
    const chat = src("components/AgentChat.tsx");
    expect(chat.match(/<RunStatusRow /g)?.length).toBe(1);
    expect(chat).not.toContain('placement="thread"');
  });
});

describe("no card pins above the composer (owner report, 2026-10-23)", () => {
  const chat = readFileSync(join(__dirname, "..", "components/AgentChat.tsx"), "utf-8");
  const form = chat.slice(chat.indexOf("<form onSubmit={handleSubmit}"));

  it("the waiting cards draw in the thread, after its last message", () => {
    expect(chat).toContain("{msg.id === hitlAnchorId && renderHitlCards()}");
    expect(chat).toContain("visibleMessages[visibleMessages.length - 1].id");
    expect(chat).not.toContain("data-pinned-ask");
  });

  it("the composer form draws no card", () => {
    expect(form).not.toContain("renderHitlCards()");
    expect(form).not.toMatch(/Draft(Result)?Card|ConfirmationCard|ElicitationCard/);
  });
});
