import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

import { agentCardLook, agentPlaceLine, splitPlanProgress } from "./agentCardLook";
import { stampStrands, strandedLine } from "./agentRuns";
import { agentRunsApi, projectsApi } from "./api";

const css = readFileSync(
  fileURLToPath(new URL("../../globals.css", import.meta.url)),
  "utf8",
);

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

  // Task 1045c. A stranded run draws the warning edge and says when the
  // last word came. It never reads as working or waiting.
  it("stranded is a still warning edge with the time of the last word", () => {
    const now = Date.parse("2026-11-12T15:00:00");
    const at = new Date(now - 29 * 60 * 1000);
    const hhmm = `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
    const stranded = { idle_s: 29 * 60 };
    stampStrands({ stranded }, now);
    for (const st of ["working", "answered", "waiting", "stale", "queued", "awaiting"]) {
      expect(agentCardLook(st, { clientVia: via, stranded, note: "busy" })).toEqual({
        edge: "warning",
        line: `No word since ${hhmm}`,
      });
    }
    // Without a stamp the time of the nudge is the fallback.
    const nudged = new Date("2026-11-12T14:31:00");
    expect(
      agentCardLook("working", { stranded: { nudged_at: nudged.toISOString() } }).line,
    ).toBe("No word since 14:31");
    expect(agentCardLook("working", { stranded: {} }).line).toBe("No word for a while");
    // SW-32: a plan never hides the strand, and it puts no count on the line.
    const plan = { done: 3, total: 7, step: "Run the tests" };
    expect(agentCardLook("working", { clientVia: via, stranded, plan })).toEqual({
      edge: "warning",
      line: `No word since ${hhmm}`,
    });
    // A failed or stopping run keeps its own look; no strand means no change.
    expect(agentCardLook("failed", { stranded }).edge).toBe("destructive");
    expect(agentCardLook("stopping", { stranded }).edge).toBeNull();
    expect(agentCardLook("working", { stranded: null }).edge).toBe("live");
  });

  it("draws the warning edge from the warning token in light and in dark", () => {
    const warn = css.match(/\[data-agent-edge="warning"\]\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(warn).toMatch(/var\(--warning\)/);
    expect(warn).not.toMatch(/#[0-9a-f]{3,8}\b|rgb\(|hsl\(|animation/i);
    const block = (selector: string) =>
      css.match(new RegExp(`(?:^|\\n)${selector}\\s*\\{([\\s\\S]*?)\\n\\}`))?.[1] ?? "";
    expect(block(":root"), "dark").toMatch(/--warning:/);
    expect(block("\\.light"), "light").toMatch(/--warning:/);
  });
});

describe("the run panel and the strand", () => {
  it("AgentRunPanel merges a change of the strand into the task row", () => {
    const src = readFileSync(
      fileURLToPath(new URL("../components/AgentRunPanel.tsx", import.meta.url)),
      "utf8",
    );
    // The polled run's strand wins over the older copy of the row.
    expect(src).toContain("agent_stranded: run?.stranded ?? null,");
    expect(src).not.toContain("task.agent_stranded ?? run.stranded");
    expect(src).toContain(
      "strandedLine(task.agent_stranded) === strandedLine(next.agent_stranded)",
    );
    expect(src).toContain("[enabled, state, run?.stranded,");
  });
});

describe("the board card and the strand", () => {
  it("TaskBoard gives the strand of the row to the card look", () => {
    const src = readFileSync(
      fileURLToPath(new URL("../components/TaskBoard.tsx", import.meta.url)),
      "utf8",
    );
    const call = src.match(/agentCardLook\(task\.agent_state, \{([\s\S]*?)\}\);/)?.[1] ?? "";
    expect(call).toContain("stranded: task.agent_stranded,");
    expect(src).toContain("agentLook.line");
  });
});

// Task 1045c. `idle_s` is true only at the read. The time of the last word is
// fixed when the reply arrives, so it holds while the row ages.
describe("the time of the last word", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const read = new Date("2026-11-12T15:00:00");
  const strand = () => ({ nudged_at: "2026-11-12T14:50:00", idle_s: 29 * 60 });
  const reply = (body: unknown) =>
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body))));

  it("a row and a run read the same line hours after the read", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(read);

    reply({ id: "t1", agent_stranded: strand() });
    const row = await projectsApi.task("t1");
    reply({ rows: [{ id: "r1", stranded: strand() }] });
    const runs = await agentRunsApi.list("t1");
    reply({ run: { id: "r1", stranded: strand() }, activities: [] });
    const detail = await agentRunsApi.get("r1");

    for (const hours of [0, 3, 27]) {
      vi.setSystemTime(new Date(read.getTime() + hours * 3_600_000));
      expect(
        agentCardLook("working", { stranded: row.agent_stranded }).line,
      ).toBe("No word since 14:31");
      expect(strandedLine(runs?.[0]?.stranded)).toBe("No word since 14:31");
      expect(strandedLine(detail.run.stranded)).toBe("No word since 14:31");
    }
  });

  it("stamps each nested strand, and leaves one with no idle_s alone", () => {
    const body = {
      groups: [{ rows: [{ agent_stranded: strand() }, { agent_stranded: null }] }],
      run: { stranded: { nudged_at: "2026-11-12T14:31:00", idle_s: null } },
    };
    stampStrands(body, read.getTime());
    expect(body.groups[0]!.rows[0]!.agent_stranded).toMatchObject({
      last_word_ms: read.getTime() - 29 * 60_000,
    });
    expect(body.run.stranded).not.toHaveProperty("last_word_ms");
    expect(strandedLine(body.run.stranded)).toBe("No word since 14:31");
  });
});

describe("the stylesheet", () => {
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

describe("a plan on the live line (SW-32)", () => {
  it("shows the step in progress and the count", () => {
    expect(
      agentCardLook("working", {
        clientVia: via,
        note: "old thought",
        plan: { done: 3, total: 7, step: "Write the tests" },
      }),
    ).toEqual({ edge: "live", line: "Claude Code · Write the tests · 3 of 7" });
  });

  it("falls back to the note when no step is in progress, and keeps the count", () => {
    expect(
      agentCardLook("working", {
        clientVia: via,
        note: "thinking",
        plan: { done: 0, total: 2, step: null },
      }).line,
    ).toBe("Claude Code · thinking · 0 of 2");
    expect(
      agentCardLook("answered", { clientVia: via, plan: { done: 2, total: 2, step: null } }).line,
    ).toBe("Claude Code · 2 of 2");
  });

  it("a plan of no step or no plan changes nothing", () => {
    for (const plan of [null, undefined, { done: 0, total: 0, step: null }]) {
      expect(agentCardLook("working", { clientVia: via, note: "n", plan }).line).toBe(
        "Claude Code · n",
      );
    }
  });

  it("only a live run shows the plan: a waiting run keeps its wait", () => {
    expect(
      agentCardLook("waiting", {
        waiting: { on: "ci", until: null },
        plan: { done: 1, total: 3, step: "x" },
      }).line,
    ).toBe("Waiting on CI");
  });
});

describe("the count of a plan stays apart from the step (SW-32)", () => {
  it("splits the head and the count, and leaves other lines whole", () => {
    expect(
      splitPlanProgress("Claude Code (lane-invites) · Pull the invite list · 0 of 2", { done: 0, total: 2 }),
    ).toEqual({ head: "Claude Code (lane-invites) · Pull the invite list", tail: "· 0 of 2" });
    expect(splitPlanProgress("Claude Code working", { done: 0, total: 2 })).toBeNull();
    expect(splitPlanProgress("Claude Code · n", null)).toBeNull();
    expect(splitPlanProgress(null, { done: 1, total: 3 })).toBeNull();
    expect(splitPlanProgress(" · 1 of 3", { done: 1, total: 3 })).toBeNull();
  });
});
