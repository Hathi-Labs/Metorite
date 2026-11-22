import { describe, expect, it } from "vitest";

import { holderLine, sinceText } from "./agentHolder";
import { type TaskHeldBy } from "./agentHolder";
import { HOLD_FILTERS, isHeldByAgent, matchesAgentFocus } from "./agentRuns";
import { agentReadParams } from "./paging";

const NOW = Date.parse("2026-11-11T12:00:00Z");
const held: TaskHeldBy = {
  member: "Priya@Example.test",
  member_name: "Priya Rao",
  client_via: "mcp:claude.ai:claude-code",
  client_name: "Claude Code",
  since: "2026-11-11T09:14:00Z",
};

describe("sinceText", () => {
  it("reads the clock for a hold of today and the date for an older one", () => {
    expect(sinceText("2026-11-11T09:14:00Z", NOW, "UTC")).toBe("09:14");
    expect(sinceText("2026-11-03T09:14:00Z", NOW, "UTC")).toBe("3 Nov");
  });
  it("gives null for a stamp that does not parse", () => {
    expect(sinceText("soon", NOW, "UTC")).toBeNull();
    expect(sinceText(null, NOW, "UTC")).toBeNull();
  });
});

describe("holderLine", () => {
  it("names the client, the member and the start", () => {
    expect(holderLine(held, undefined, NOW, "UTC")).toBe(
      "Worked by Claude Code for Priya Rao since 09:14",
    );
  });
  it("prefers the label, then the member name, then the address", () => {
    const labels = new Map([["priya@example.test", "Priya R."]]);
    expect(holderLine(held, labels, NOW, "UTC")).toContain("for Priya R.");
    expect(holderLine({ ...held, member_name: null }, undefined, NOW, "UTC")).toContain(
      "for Priya@Example.test",
    );
  });
  it("falls back to the client stamp and drops since with no start", () => {
    expect(holderLine({ ...held, client_name: null, since: null }, undefined, NOW, "UTC")).toBe(
      "Worked by Claude Code for Priya Rao",
    );
  });
});

describe("the hold filters", () => {
  it("offers Held by an agent and Free", () => {
    expect(HOLD_FILTERS.map((f) => f.label)).toEqual(["Held by an agent", "Free"]);
  });
  it("sends the focus to the server and nothing else", () => {
    expect(agentReadParams(false, "held", false)).toEqual({ agent_focus: "held" });
    expect(agentReadParams(false, "free", false)).toEqual({ agent_focus: "free" });
  });
  it("guards the paint by held_by, and a stale lease counts as free", () => {
    expect(isHeldByAgent({ held_by: held })).toBe(true);
    expect(isHeldByAgent({ held_by: { ...held, stale: true } })).toBe(false);
    expect(matchesAgentFocus({ held_by: held }, "held", "a@b.test")).toBe(true);
    expect(matchesAgentFocus({ held_by: held }, "free", "a@b.test")).toBe(false);
    expect(matchesAgentFocus({ held_by: null }, "free", "a@b.test")).toBe(true);
  });
});
