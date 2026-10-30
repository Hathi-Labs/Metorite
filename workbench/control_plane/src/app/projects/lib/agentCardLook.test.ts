import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { agentCardLook } from "./agentCardLook";

const via = "mcp:claude.ai:claude-code";

describe("agentCardLook", () => {
  it("a task with no open run keeps its card", () => {
    expect(agentCardLook(null)).toEqual({ edge: null, line: null });
    expect(agentCardLook(undefined)).toEqual({ edge: null, line: null });
    expect(agentCardLook("bogus")).toEqual({ edge: null, line: null });
  });

  it("working and answered get the live edge and the live line", () => {
    for (const s of ["working", "answered"]) {
      expect(agentCardLook(s, { clientVia: via, note: " running the unit tests " })).toEqual({
        edge: "live",
        line: "Claude Code · running the unit tests",
      });
    }
  });

  it("a live run with no note still reads a line", () => {
    expect(agentCardLook("working", { clientVia: via }).line).toBe("Claude Code working");
    expect(agentCardLook("answered").line).toBe("Answered · agent picks up next");
  });

  it("queued, stale and failed are still edges", () => {
    expect(agentCardLook("queued", { clientVia: via })).toEqual({
      edge: "steady",
      line: "Queued for Claude Code",
    });
    expect(agentCardLook("stale", { clientVia: via })).toEqual({ edge: "breathing", line: null });
    expect(agentCardLook("stale", { clientVia: via, note: " ran the tests " })).toEqual({
      edge: "breathing",
      line: "ran the tests",
    });
    expect(agentCardLook("failed")).toEqual({ edge: "destructive", line: null });
  });

  it("awaiting names the question, or falls back to the waiting text", () => {
    expect(agentCardLook("awaiting", { note: "Which branch?" })).toEqual({
      edge: "warning",
      line: "Needs you: Which branch?",
    });
    expect(agentCardLook("awaiting").line).toBe("Waiting for an answer");
  });

  it("no state maps to a dashed or muted edge", () => {
    for (const st of ["working", "answered", "queued", "awaiting", "stale", "failed"]) {
      const edge = agentCardLook(st, { clientVia: via }).edge ?? "";
      expect(edge).not.toMatch(/dashed|muted/);
    }
  });

  it("stopping changes nothing", () => {
    expect(agentCardLook("stopping").edge).toBeNull();
  });
});

describe("the stylesheet", () => {
  const css = readFileSync(
    fileURLToPath(new URL("../../globals.css", import.meta.url)),
    "utf8",
  );
  it("has no dashed rule on an agent edge, and draws each edge it names", () => {
    const rules = css.match(/\[data-agent-edge[^{]*\{[^}]*\}/g) ?? [];
    expect(rules.length).toBeGreaterThan(0);
    for (const rule of rules) expect(rule).not.toMatch(/dashed/);
    for (const edge of ["live", "steady", "breathing", "warning", "destructive"]) {
      expect(css).toContain(`[data-agent-edge="${edge}"]`);
    }
    expect(css).not.toContain('data-agent-edge="muted"');
    expect(css).not.toContain('data-agent-edge="dashed-accent"');
  });
});

describe("the board applies the look", () => {
  const src = readFileSync(
    fileURLToPath(new URL("../components/TaskBoard.tsx", import.meta.url)),
    "utf8",
  );
  it("TaskBoard calls agentCardLook and sets data-agent-edge on the card", () => {
    expect(src).toMatch(/agentCardLook\(/);
    expect(src).toMatch(/data-agent-edge=\{/);
    expect(src).toMatch(/data-agent-line/);
  });
});
