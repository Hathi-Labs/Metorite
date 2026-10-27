import { describe, expect, it } from "vitest";

import { KnowledgeError } from "./api";
import { reviewHref } from "./links";
import {
  acceptLabel,
  alreadyDecided,
  decisionFailure,
  groupProposals,
  isAgentDraft,
  kindLabel,
  nextAfter,
  originKind,
  pendingEditFor,
  proposerLine,
  readingOrder,
  type Proposal,
} from "./review";

function p(over: Partial<Proposal>): Proposal {
  return {
    id: "p1",
    kind: "edit",
    page_id: "pg1",
    space_id: "s1",
    page_title: "Refunds",
    base_version: 2,
    title: "Refunds",
    body_md: "",
    summary: "",
    proposer_email: "asha.rao@x.com",
    via: "chat",
    status: "pending",
    created_at: "2026-10-22T10:00:00Z",
    ...over,
  };
}

describe("the words of the buttons", () => {
  it("says Publish for a page and Apply edit for an edit", () => {
    expect(acceptLabel("page")).toBe("Publish");
    expect(acceptLabel("edit")).toBe("Apply edit");
    expect(kindLabel("page")).toBe("New page");
    expect(kindLabel("edit")).toBe("Edit");
  });
  it("names who proposed", () => {
    expect(proposerLine(p({}))).toBe("Proposed by the assistant for asha.rao");
    expect(proposerLine(p({ via: "mcp" }))).toBe("Proposed by an outside agent for asha.rao");
    expect(proposerLine(p({ via: "web" }))).toBe("Proposed by asha.rao");
  });
});

describe("the list", () => {
  const list = [
    p({ id: "e2", created_at: "2026-10-22T12:00:00Z" }),
    p({ id: "n1", kind: "page", created_at: "2026-10-22T11:00:00Z" }),
    p({ id: "e1", created_at: "2026-10-22T09:00:00Z" }),
  ];
  it("groups new pages first and each group oldest first", () => {
    const groups = groupProposals(list);
    expect(groups.map((g) => g.label)).toEqual(["New pages", "Edits"]);
    expect(groups[1].items.map((i) => i.id)).toEqual(["e1", "e2"]);
    expect(readingOrder(list).map((i) => i.id)).toEqual(["n1", "e1", "e2"]);
  });
  it("leaves out an empty group", () => {
    expect(groupProposals([p({})]).map((g) => g.kind)).toEqual(["edit"]);
    expect(groupProposals([])).toEqual([]);
  });
  it("opens the next proposal, else the one before, else none", () => {
    expect(nextAfter(list, "n1")?.id).toBe("e1");
    expect(nextAfter(list, "e2")?.id).toBe("e1");
    expect(nextAfter([p({})], "p1")).toBeNull();
    expect(nextAfter(list, "gone")?.id).toBe("n1");
  });
  it("finds the pending edit of a page", () => {
    expect(pendingEditFor(list, "pg1")?.id).toBe("e2");
    expect(pendingEditFor(list, "other")).toBeNull();
    expect(pendingEditFor(undefined, "pg1")).toBeNull();
  });
  it("links to a proposal", () => {
    expect(reviewHref()).toBe("/knowledge/review");
    expect(reviewHref("a b")).toBe("/knowledge/review?p=a%20b");
  });
});

describe("the origin of a page", () => {
  it("reads an object and a JSON string", () => {
    expect(originKind({ kind: "agent_proposal" })).toBe("agent_proposal");
    expect(originKind('{"kind":"agent_proposal"}')).toBe("agent_proposal");
    expect(originKind(null)).toBeNull();
    expect(originKind("not json")).toBeNull();
  });
  it("marks a draft only, never a published page", () => {
    const origin = { kind: "agent_proposal" };
    expect(isAgentDraft({ state: "draft", origin })).toBe(true);
    expect(isAgentDraft({ state: "published", origin })).toBe(false);
    expect(isAgentDraft({ state: "draft", origin: null })).toBe(false);
  });
});

describe("a refused decision", () => {
  it("keeps the server's words on a 409", () => {
    const f = decisionFailure(new KnowledgeError("The page changed after the proposal was made. Ask for a new one.", 409));
    expect(f).toEqual({
      detail: "The page changed after the proposal was made. Ask for a new one.",
      conflict: true,
      gone: false,
    });
    expect(alreadyDecided(f.detail)).toBe(false);
  });
  it("tells a decided proposal from a moved page", () => {
    expect(alreadyDecided("The proposal is decided.")).toBe(true);
    expect(alreadyDecided("The proposal is accepted.")).toBe(true);
  });
  it("reads a 404 and a plain error", () => {
    expect(decisionFailure(new KnowledgeError("Not found", 404)).gone).toBe(true);
    expect(decisionFailure(new Error("boom"))).toEqual({ detail: "boom", conflict: false, gone: false });
  });
});
