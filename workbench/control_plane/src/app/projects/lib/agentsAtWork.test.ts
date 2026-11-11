import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { agentStateChip } from "@/lib/statusAccent";

import { AgentsAtWorkView } from "../components/AgentsAtWork";
import {
  EMPTY_COPY,
  ERROR_COPY,
  type AgentsAtWorkRead,
  lastRunLine,
  outcomeWord,
  parseOverview,
  runsWord,
  stateChip,
} from "./agentsAtWork";

const NOW = Date.parse("2026-11-11T12:00:00Z");

const row = (over: Record<string, unknown> = {}) => ({
  key: "external:claude",
  name: "Claude Code",
  agent: "claude",
  job: "Works on tasks through a connection.",
  source: "external",
  label: null,
  state: "working",
  runs: 3,
  last_run: { at: "2026-11-11T09:00:00Z", state: "completed" },
  last_error: null,
  cost: 12,
  ...over,
});

describe("parseOverview", () => {
  it("keeps the fields it knows and drops the rest", () => {
    const out = parseOverview({ agents: [row()], count: 1, viewer: "admin", truncated: false });
    expect(out?.agents).toHaveLength(1);
    expect(out?.agents[0]).not.toHaveProperty("cost");
    expect(out?.agents[0]).not.toHaveProperty("agent");
    expect(out?.agents[0].name).toBe("Claude Code");
  });
  it("drops a row with an unknown state or source", () => {
    const out = parseOverview({
      agents: [row({ state: "sleeping" }), row({ source: "alien" }), row({ key: "k2" })],
    });
    expect(out?.agents.map((a) => a.key)).toEqual(["k2"]);
  });
  it("refuses a body that is not an overview", () => {
    expect(parseOverview(null)).toBeNull();
    expect(parseOverview("x")).toBeNull();
    expect(parseOverview({ agents: "no" })).toBeNull();
    expect(parseOverview({})).toBeNull();
  });
  it("reads an error and a truncated flag", () => {
    const out = parseOverview({
      agents: [row({ last_error: { at: null, what: "It failed.", next_step: "Start it again." } })],
      truncated: true,
    });
    expect(out?.truncated).toBe(true);
    expect(out?.agents[0].last_error?.what).toBe("It failed.");
  });
});

describe("the words", () => {
  it("names the last run with a relative time", () => {
    expect(lastRunLine({ at: "2026-11-11T09:00:00Z", state: "failed" }, NOW)).toBe("Failed, 3h ago");
    expect(lastRunLine(null, NOW)).toBe("Has not run yet");
    expect(outcomeWord("in_review")).toBe("In review");
  });
  it("counts runs", () => {
    expect(runsWord(1)).toBe("1 run");
    expect(runsWord(0)).toBe("0 runs");
  });
});

describe("the accent", () => {
  it("comes from statusAccent and nowhere else", () => {
    expect(stateChip("waiting_on_person")).toBe(agentStateChip("awaiting"));
    expect(stateChip("working")).toBe(agentStateChip("working"));
    expect(stateChip("idle")).toBe(agentStateChip("stale"));
    const src = [
      readFileSync(new URL("./agentsAtWork.ts", import.meta.url), "utf-8"),
      readFileSync(new URL("../components/AgentsAtWork.tsx", import.meta.url), "utf-8"),
    ].join("\n");
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b|\b(?:bg|text|border)-(?:red|amber|blue|green|violet|yellow|orange|emerald)-/);
  });
});

const view = (read: AgentsAtWorkRead) =>
  renderToStaticMarkup(createElement(AgentsAtWorkView, { read, nowMs: NOW }));

describe("the section", () => {
  it("draws nothing while the feature is dark", () => {
    expect(view({ kind: "dark" })).toBe("");
  });
  it("draws one row for each agent, with its state and last run", () => {
    const overview = parseOverview({
      agents: [
        row({ key: "a", name: "Alpha", state: "waiting_on_person" }),
        row({
          key: "b",
          name: "Beta",
          state: "idle",
          last_run: { at: "2026-11-11T11:00:00Z", state: "failed" },
          last_error: { at: null, what: "The last run failed.", next_step: "Open the task." },
        }),
      ],
    })!;
    const out = view({ kind: "ready", overview });
    expect(out.match(/data-agent-row=/g)).toHaveLength(2);
    expect(out).toContain("Waiting for a person");
    expect(out).toContain("Failed, 1h ago");
    expect(out).toContain("The last run failed. Open the task.");
    expect(out).not.toMatch(/<button/);
  });
  it("says in one sentence that no agent exists", () => {
    const out = view({ kind: "ready", overview: parseOverview({ agents: [] })! });
    expect(out).toContain(EMPTY_COPY);
  });
  it("explains a failed read, with no raw error", () => {
    const out = view({ kind: "error" });
    expect(out).toContain(ERROR_COPY);
    expect(out).not.toMatch(/Error:|SyntaxError|JSON/);
  });
});
