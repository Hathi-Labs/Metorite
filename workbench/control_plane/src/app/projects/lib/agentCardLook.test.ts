import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { agentCardLook, agentPlaceLine } from "./agentCardLook";

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
    // The pill says "Queued for Claude Code", so the line does not repeat it.
    expect(agentCardLook("queued", { clientVia: via })).toEqual({ edge: "steady", line: null });
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

  // Task 1045a (`agent_coordination.md` §3.2): a still accent edge, because
  // motion means working (EA-13g). The line says what the agent waits on.
  it("waiting is a still edge with what the agent waits on and for how long", () => {
    const now = Date.parse("2026-11-06T10:00:00Z");
    expect(
      agentCardLook("waiting", {
        clientVia: via,
        waiting: { on: "ci", until: "2026-11-06T10:25:00Z" },
        nowMs: now,
      }),
    ).toEqual({ edge: "steady", line: "Waiting on CI · about 25 min" });
    // Seconds round up, so the line never says "about 0 min".
    expect(
      agentCardLook("waiting", {
        waiting: { on: "agent", until: "2026-11-06T10:00:20Z" },
        nowMs: now,
      }).line,
    ).toBe("Waiting on another agent · about 1 min");
    // The agent's own sub-agents are work, not a wait (owner, 2026-11-07).
    // A row with the old value reads as a wait the client does not name.
    expect(
      agentCardLook("waiting", { waiting: { on: "subagents" as never } }).line,
    ).toBe("Waiting");
    // A row with no wait, or an `on` the client does not know, still reads.
    expect(agentCardLook("waiting", { clientVia: via }).line).toBe("Waiting");
    expect(
      agentCardLook("waiting", { clientVia: via, waiting: { on: "lunch" as never } }).line,
    ).toBe("Waiting");
    expect(agentCardLook("waiting").edge).not.toBe("live");
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

  // Task 1045a. The waiting edge is the steady edge. It draws from the
  // accent token only, and both looks define that token, so the edge shows
  // in light and in dark. It has no animation, so it does not move.
  it("draws the waiting edge from the accent token in light and in dark", () => {
    expect(agentCardLook("waiting").edge).toBe("steady");
    const steady = css.match(/\[data-agent-edge="steady"\]\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(steady).toMatch(/var\(--primary\)/);
    expect(steady).not.toMatch(/#[0-9a-f]{3,8}\b|rgb\(|hsl\(|animation/i);
    const block = (selector: string) =>
      css.match(new RegExp(`(?:^|\\n)${selector}\\s*\\{([\\s\\S]*?)\\n\\}`))?.[1] ?? "";
    expect(block(":root"), "dark").toMatch(/--primary:/);
    expect(block("\\.light"), "light").toMatch(/--primary:/);
  });

  // A change of state must not move the card: each edge takes 2px on each side.
  it("gives every edge the same 2px box", () => {
    const rule = (edge: string) =>
      css.match(new RegExp(`\\[data-agent-edge="${edge}"\\]\\s*\\{([^}]*)\\}`))?.[1] ?? "";
    for (const edge of ["steady", "breathing", "warning", "destructive"]) {
      expect(rule(edge), edge).toMatch(/border:\s*2px solid/);
    }
    expect(rule("live")).toMatch(/padding:\s*2px/);
  });

  // The band is even round each corner: the edge's corner is the card's plus 2px.
  it("rounds the edge to the card's corner plus 2px", () => {
    expect(css).toMatch(/\[data-agent-edge\]\s*\{[^}]*border-radius:\s*calc\(var\(--radius-lg\) \+ 2px\)/);
  });

  // DESIGN_SYSTEM §4: a loop moves transform or opacity only.
  it("breathes with opacity only", () => {
    const frames = css.match(/@keyframes agent-edge-breathe\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";
    expect(frames).toContain("opacity");
    expect(frames).not.toMatch(/box-shadow|border|width|height/);
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

describe("agentPlaceLine (SW-106)", () => {
  it("reads the machine with its kind, then the model", () => {
    expect(
      agentPlaceLine({ agent_host: "VJ laptop", agent_host_kind: "computer", agent_model: "Sonnet 5.5" }),
    ).toEqual({
      icon: "Laptop",
      machine: "VJ laptop (Computer)",
      model: "Sonnet 5.5",
      line: "Machine: VJ laptop (Computer) \u00b7 Model: Sonnet 5.5",
      none: false,
    });
  });
  it("picks the icon of each kind", () => {
    const icon = (kind: string) => agentPlaceLine({ agent_host: "x", agent_host_kind: kind }).icon;
    expect([icon("computer"), icon("server"), icon("cloud"), icon("laptop")]).toEqual([
      "Laptop",
      "Server",
      "Cloud",
      "CircleHelp",
    ]);
  });
  it("reads Not reported for each fact that the agent did not send", () => {
    expect(agentPlaceLine({}).line).toBe("Machine: Not reported \u00b7 Model: Not reported");
    expect(agentPlaceLine({}).none).toBe(true);
    expect(agentPlaceLine({ agent_host: null, agent_host_kind: null, agent_model: "" }).none).toBe(true);
    expect(agentPlaceLine({ agent_model: "Opus 5.5" }).line).toBe("Machine: Not reported \u00b7 Model: Opus 5.5");
    expect(agentPlaceLine({ agent_host: "box" }).line).toBe("Machine: box \u00b7 Model: Not reported");
  });
  it("drops a bad value, and cleans control characters", () => {
    const look = agentPlaceLine({ agent_host: "a\u0007  b", agent_host_kind: "tablet", agent_model: 5 });
    expect(look.line).toBe("Machine: a b \u00b7 Model: Not reported");
    expect(look.icon).toBe("CircleHelp");
  });
  it("makes no claim of a box check, because the run read has no proof", () => {
    expect(agentPlaceLine({ agent_host: "box", agent_host_kind: "server" }).line).not.toMatch(/checked/i);
  });
  it("shows no place line for a run with no place, and one line for a run with a value", () => {
    expect(agentPlaceLine({ agent_host: null, agent_host_kind: null, agent_model: null }).none).toBe(true);
    for (const facts of [{ agent_host: "box" }, { agent_host_kind: "cloud" }, { agent_model: "Opus 5.5" }]) {
      expect(agentPlaceLine(facts).none).toBe(false);
    }
  });
  it("draws the place line in the run panel only when a value exists", () => {
    const panel = readFileSync(fileURLToPath(new URL("../components/AgentRunPanel.tsx", import.meta.url)), "utf-8");
    const at = panel.indexOf("data-agent-place");
    expect(at).toBeGreaterThan(0);
    expect(panel.slice(Math.max(0, at - 400), at)).toContain("placeLook.none ? null");
  });
});
