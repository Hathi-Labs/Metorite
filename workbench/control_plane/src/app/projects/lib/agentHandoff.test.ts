/**
 * SW-24, the handoff card. Mutations caught, by test:
 * - the byline loses the client or the member;
 * - a `response` with no note, or a note of another type, reads as a note;
 * - the newest note is not the one shown;
 * - an empty text shows as a line.
 */

import { describe, expect, it } from "vitest";

import { handoffByline, handoffOf, newestHandoff } from "./agentHandoff";
import type { AgentActivity } from "./agentRuns";

function act(seq: number, over: Partial<AgentActivity>): AgentActivity {
  return { id: `a${seq}`, seq, type: "response", content: {}, ...over };
}

describe("handoffByline", () => {
  it("names the client and the member", () => {
    expect(
      handoffByline({ actor: "priya.shah@acme.test", actor_via: "mcp:claude.ai:claude-code" }),
    ).toBe("Handed off by Claude Code for Priya Shah");
  });

  it("uses the directory label of the member", () => {
    const labels = new Map([["priya.shah@acme.test", "Priya S."]]);
    expect(
      handoffByline({ actor: "priya.shah@acme.test", actor_via: "mcp:claude.ai:claude-code" }, labels),
    ).toBe("Handed off by Claude Code for Priya S.");
  });

  it("drops the member when the row has none", () => {
    expect(handoffByline({ actor: null, actor_via: "mcp:claude.ai:claude-code" })).toBe(
      "Handed off by Claude Code",
    );
  });
});

describe("handoffOf and newestHandoff", () => {
  const note = { done: "Built it", left: " ", next: "Open the PR" };

  it("reads the filled texts only", () => {
    expect(handoffOf(act(1, { content: { handoff: note } }))).toEqual({
      done: "Built it",
      next: "Open the PR",
    });
  });

  it("ignores a response with no note and a note on another type", () => {
    expect(handoffOf(act(1, { content: { body: "hi" } }))).toBeNull();
    expect(handoffOf(act(1, { type: "thought", content: { handoff: note } }))).toBeNull();
    expect(handoffOf(act(1, { content: { handoff: { done: "  " } } }))).toBeNull();
  });

  it("picks the newest note", () => {
    const rows = [
      act(1, { content: { handoff: { done: "old" } } }),
      act(2, { content: { handoff: { done: "new" } } }),
      act(3, { content: { body: "later, no note" } }),
    ];
    expect(newestHandoff(rows)?.id).toBe("a2");
    expect(newestHandoff([])).toBeNull();
  });
});
