/**
 * WS-55 EA-12c — the agent pill and the answer card (§12.1).
 *
 * Mutations caught, by test:
 * - a pill text leaves the table of §12.1, or the client name loses its
 *   title case (`mcp:claude-code` must read "Claude Code");
 * - a state takes a hue the spec does not name, or `working` goes through
 *   `resolveHue` and turns blue;
 * - the answer sends labels and not option ids, sends two ids for a single
 *   choice, or drops the typed text;
 * - Stop loses its `stop` signal;
 * - the collapsed card loses the answerer or the labels;
 * - "Waiting on me" shows a task that waits for somebody else.
 *
 * The click paths run in a real browser in `e2e/agent-runs.spec.ts`.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import ElicitationCard from "@/components/ElicitationCard";
import { RECOMMENDED_LABEL } from "@/components/RecommendedBadge";
import { AGENT_STATES, accentForHue, agentStateChip } from "@/lib/statusAccent";

import { AgentPill } from "../components/AgentPill";
import {
  AGENT_STATE_VALUES,
  type AgentActivity,
  type AgentRun,
  agentKindLabel,
  agentPillText,
  answerPayload,
  answerTo,
  answeredLine,
  cardQuestion,
  clientName,
  isWaitingOnMe,
  latestElicitation,
  mergeActivities,
  newestEphemeral,
  normalizeRunDetail,
  replyPayload,
  runToShow,
  stopPayload,
} from "./agentRuns";

const ELICIT: AgentActivity = {
  id: "a1",
  seq: 3,
  type: "elicitation",
  content: { body: "Which database should the migration target?" },
  signal: "select",
  signal_metadata: {
    options: [
      { id: "pg", label: "Postgres", recommended: true, description: "The one we run" },
      { id: "sqlite", label: "SQLite" },
      { id: "both", label: "Both" },
    ],
    allow_multiple: false,
  },
  actor: "priya@example.com",
  actor_via: "mcp:claude-code",
};

const MULTI: AgentActivity = {
  ...ELICIT,
  id: "a2",
  signal_metadata: { ...ELICIT.signal_metadata, allow_multiple: true },
};

describe("the pill: state → text, by the table in §12.1", () => {
  const facts = {
    clientVia: "mcp:claude-code",
    memberEmail: "priya.sharma@example.com",
    thought: "Reading the migration runner",
  };

  it("draws each row of the table", () => {
    expect(agentPillText("awaiting", facts).text).toBe("Waiting for Priya");
    expect(agentPillText("answered", facts).text).toBe("Answered · agent picks up next");
    expect(agentPillText("working", facts).text).toBe("Claude Code working");
    expect(agentPillText("queued", facts).text).toBe("Queued for Claude Code");
    expect(agentPillText("stale", facts).text).toBe("Claude Code idle");
    expect(agentPillText("stopping", facts).text).toBe("Stopping");
    expect(agentPillText("failed", facts).text).toBe("Agent failed");
  });

  it("shows the newest thought as the tooltip of working", () => {
    expect(agentPillText("working", facts).title).toBe("Reading the migration runner");
  });

  it("reads the directory name before the address", () => {
    const labels = new Map([["priya.sharma@example.com", "Priyanka Sharma"]]);
    expect(agentPillText("awaiting", { ...facts, labels }).text).toBe("Waiting for Priyanka");
  });

  it("has a text for a row that knows only the state", () => {
    expect(agentPillText("awaiting").text).toBe("Waiting for an answer");
    expect(agentPillText("working").text).toBe("Agent working");
  });

  it("names the client from its slug", () => {
    expect(clientName("mcp:claude-code")).toBe("Claude Code");
    expect(clientName("mcp:cursor")).toBe("Cursor");
    expect(clientName("mcp:chatgpt")).toBe("ChatGPT");
    expect(clientName("")).toBe("Agent");
  });
});

describe("the pill: state → accent, through statusAccent.ts", () => {
  it("maps each state onto the tone the spec names", () => {
    expect(AGENT_STATES.awaiting.hue).toBe("amber"); // attention
    expect(AGENT_STATES.answered.hue).toBe("blue"); // info
    expect(AGENT_STATES.working.hue).toBe("gray"); // neutral
    expect(AGENT_STATES.queued.hue).toBe("gray"); // neutral
    expect(AGENT_STATES.failed.hue).toBe("red"); // error
    expect(AGENT_STATES.stale).toMatchObject({ hue: "gray", muted: true });
    expect(AGENT_STATES.stopping).toMatchObject({ hue: "gray", muted: true });
  });

  it("draws neutral as the chip, and muted as the faint pair", () => {
    expect(agentStateChip("working")).toBe(accentForHue("gray").chip);
    expect(agentStateChip("awaiting")).toBe(accentForHue("amber").chip);
    expect(agentStateChip("stale")).toBe(
      `${accentForHue("gray").soft} ${accentForHue("gray").text}`,
    );
  });

  it("has a visual for every state, and each has a glyph", () => {
    for (const s of AGENT_STATE_VALUES) {
      expect(AGENT_STATES[s]?.icon, s).toBeTruthy();
    }
  });

  it("renders the pill with its state, and nothing without one", () => {
    const html = renderToStaticMarkup(
      createElement(AgentPill, { state: "awaiting", memberEmail: "priya@example.com" }),
    );
    expect(html).toContain('data-agent-pill="awaiting"');
    expect(html).toContain("Waiting for Priya");
    expect(html).toContain("bg-warning/10");
    expect(renderToStaticMarkup(createElement(AgentPill, { state: null }))).toBe("");
    expect(renderToStaticMarkup(createElement(AgentPill, { state: "bogus" }))).toBe("");
  });
});

describe("the answer card", () => {
  it("draws the options, with the recommended one marked", () => {
    const html = renderToStaticMarkup(
      createElement(ElicitationCard, {
        questions: [cardQuestion(ELICIT)],
        onSubmit: () => {},
      }),
    );
    expect(html).toContain("Which database should the migration target?");
    expect(html).toContain("Postgres");
    expect(html).toContain("SQLite");
    expect(html).toContain(RECOMMENDED_LABEL);
    expect(html.match(/data-elicitation-option/g)).toHaveLength(3);
  });

  it("draws circles for one choice and boxes for many", () => {
    expect(cardQuestion(ELICIT).multiSelect).toBe(false);
    expect(cardQuestion(MULTI).multiSelect).toBe(true);
    const many = renderToStaticMarkup(
      createElement(ElicitationCard, { questions: [cardQuestion(MULTI)], onSubmit: () => {} }),
    );
    expect(many).toContain("lucide-square");
    expect(many).not.toContain("lucide-circle ");
  });

  it("sends option ids, not labels, for a single choice", () => {
    const body = answerPayload(ELICIT, { Question: { selected: ["Postgres"] } }, "k1");
    expect(body).toEqual({ type: "prompt", selected: ["pg"], idempotency_key: "k1" });
  });

  it("sends each id for a multiple choice, with the typed text", () => {
    const body = answerPayload(
      MULTI,
      { Question: { selected: ["Postgres", "SQLite"], freeform: "  and keep a backup " } },
      "k2",
    );
    expect(body).toEqual({
      type: "prompt",
      selected: ["pg", "sqlite"],
      body: "and keep a backup",
      idempotency_key: "k2",
    });
  });

  it("sends typed text alone, with no selected key", () => {
    expect(answerPayload(ELICIT, { Question: { selected: [], freeform: "Neither" } }, "k3")).toEqual({
      type: "prompt",
      body: "Neither",
      idempotency_key: "k3",
    });
  });

  it("collapses to who answered and the labels", () => {
    const prompt: AgentActivity = {
      id: "p1",
      seq: 4,
      type: "prompt",
      content: { selected: ["pg", "sqlite"] },
      actor: "vj@example.com",
      in_reply_to: "a1",
    };
    expect(answeredLine(prompt, MULTI)).toBe("Answered by Vj: Postgres, SQLite");
    const labels = new Map([["vj@example.com", "Vijay Varada"]]);
    expect(answeredLine({ ...prompt, content: { body: "Go" } }, MULTI, labels)).toBe(
      "Answered by Vijay Varada: “Go”",
    );
    expect(answerTo([ELICIT, prompt], ELICIT)).toBe(prompt);
  });

  it("does not read a Stop as an answer", () => {
    const stop: AgentActivity = {
      id: "p2",
      seq: 5,
      type: "prompt",
      content: { body: "Stop requested." },
      signal: "stop",
      in_reply_to: "a1",
    };
    expect(answerTo([ELICIT, stop], ELICIT)).toBeNull();
  });

  it("sends Stop as a prompt with the stop signal", () => {
    expect(stopPayload("k4")).toMatchObject({ type: "prompt", signal: "stop", idempotency_key: "k4" });
    expect(replyPayload(" more tests ", "k5")).toEqual({
      type: "prompt",
      body: "more tests",
      idempotency_key: "k5",
    });
  });
});

describe("the run panel's reads", () => {
  const run = (over: Partial<AgentRun>): AgentRun => ({
    id: "r",
    task_id: "t",
    member_email: "vj@example.com",
    client_via: "mcp:claude-code",
    status: "in_progress",
    ...over,
  });

  it("shows the newest open run, else a newest failed run", () => {
    expect(runToShow([run({ id: "r2", status: "completed" }), run({ id: "r1" })])?.id).toBe("r1");
    expect(runToShow([run({ id: "r3", status: "failed" })])?.id).toBe("r3");
    expect(runToShow([run({ id: "r4", status: "completed" })])).toBeNull();
    expect(runToShow([])).toBeNull();
  });

  it("reads the detail in both shapes", () => {
    expect(normalizeRunDetail({ run: run({ id: "x" }), activities: [ELICIT] }).run.id).toBe("x");
    const flat = normalizeRunDetail({ ...run({ id: "y" }), activities: [ELICIT] });
    expect(flat.run.id).toBe("y");
    expect(flat.activities).toHaveLength(1);
  });

  it("merges by seq, with no duplicate", () => {
    const thought: AgentActivity = { id: "t1", seq: 1, type: "thought", content: { body: "Reading" } };
    const merged = mergeActivities([thought, ELICIT], [ELICIT, { ...thought, id: "t2", seq: 9 }]);
    expect(merged.map((a) => a.seq)).toEqual([1, 3, 9]);
    expect(newestEphemeral(merged)).toBe("Reading");
    expect(latestElicitation(merged)?.id).toBe("a1");
  });

  it("names an action by its tool", () => {
    expect(
      newestEphemeral([{ id: "x", seq: 1, type: "action", content: { action: "run_tests" } }]),
    ).toBe("Running run_tests");
  });
});

describe("Waiting on me, and the timeline mark", () => {
  it("shows a task that waits on me, as its member or its assignee", () => {
    const me = "vj@example.com";
    expect(isWaitingOnMe({ agent_state: "awaiting", agent_member_email: "VJ@example.com" }, me)).toBe(true);
    expect(isWaitingOnMe({ agent_state: "awaiting", assignees: ["vj@example.com"] }, me)).toBe(true);
    expect(isWaitingOnMe({ agent_state: "awaiting", assignees: ["priya@example.com"] }, me)).toBe(false);
    expect(isWaitingOnMe({ agent_state: "working", assignees: [me] }, me)).toBe(false);
    expect(isWaitingOnMe({ agent_state: "awaiting", assignees: [me] }, "")).toBe(false);
  });

  it("marks a comment that a run wrote", () => {
    expect(agentKindLabel("elicitation")).toBe("Agent question");
    expect(agentKindLabel("response")).toBe("Agent reply");
    expect(agentKindLabel(undefined)).toBeNull();
  });
});
