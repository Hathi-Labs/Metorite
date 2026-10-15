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
 * - "Waiting on me" shows a task that waits for somebody else;
 * - EA-6g: a free question with an `approve` option reads as an archive
 *   request, the card offers free text, the decision goes out as a label,
 *   or an expired request still offers a button.
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
  ARCHIVE_HINT,
  type AgentActivity,
  type AgentRun,
  agentKindLabel,
  agentPillText,
  answerPayload,
  answerTo,
  answeredLine,
  archiveApprovalPayload,
  archiveCardQuestion,
  archiveDecisionOf,
  archiveExpired,
  archiveRequestOf,
  archiveWaitingText,
  cardQuestion,
  clientName,
  isRunMember,
  isWaitingOnMe,
  latestElicitation,
  mergeActivities,
  newestEphemeral,
  normalizeRunDetail,
  replyPayload,
  runToShow,
  stopPayload,
  writeFailure,
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

  it("shows a question before a newer run of another member", () => {
    // Fix round 1, item 8: the pill's rule. Mutation: `runs.find(isOpenRun)`.
    const newer = run({ id: "b", member_email: "b@example.com", state: "working" });
    const asking = run({ id: "a", status: "awaiting", state: "awaiting" });
    const answered = run({ id: "c", state: "answered" });
    expect(runToShow([newer, answered, asking])?.id).toBe("a");
    expect(runToShow([newer, answered])?.id).toBe("c");
    expect(runToShow([newer, run({ id: "d", state: "working" })])?.id).toBe("b");
  });

  it("reads the detail in both shapes", () => {
    const shaped = normalizeRunDetail({ run: run({ id: "x" }), activities: [ELICIT], cursor: 3 });
    expect(shaped.run.id).toBe("x");
    expect(shaped.cursor).toBe(3);
    expect(normalizeRunDetail({ run: run({ id: "x" }), activities: [] }).cursor).toBeNull();
    const flat = normalizeRunDetail({ ...run({ id: "y" }), activities: [ELICIT] });
    expect(flat.run.id).toBe("y");
    expect(flat.activities).toHaveLength(1);
  });

  it("reads a 409 again and offers no retry", () => {
    // Fix round 1, item 9: another member answered first.
    const lost = writeFailure(409, "This question already has an answer.", "Could not send the answer.");
    expect(lost).toEqual({ text: "This question already has an answer.", reload: true });
    expect(lost.text).not.toContain("Try again");
    expect(writeFailure(500, "Boom.", "Could not send the answer.")).toEqual({
      text: "Could not send the answer. Boom. Try again.",
      reload: false,
    });
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

describe("the archive request (EA-6g, EA3-40)", () => {
  const TYPED: AgentActivity = {
    id: "q9",
    seq: 7,
    type: "elicitation",
    content: { body: "The agent asks to archive this task." },
    content_metadata: { request: { kind: "archive_task", expires_at: "2026-10-16T10:00:00Z" } },
    signal: "select",
    signal_metadata: {
      options: [
        { id: "approve", label: "Approve" },
        { id: "decline", label: "Decline" },
      ],
      allow_multiple: false,
    },
    actor_via: "mcp:claude-code",
  };
  const FREE: AgentActivity = { ...TYPED, id: "q8", content_metadata: null };
  const NOW = Date.parse("2026-10-15T10:00:00Z");

  it("reads only a request that the server typed", () => {
    // A free question with an option `approve` is never a request (G2).
    expect(archiveRequestOf(TYPED)?.kind).toBe("archive_task");
    expect(archiveRequestOf(FREE)).toBeNull();
    expect(archiveRequestOf({ ...TYPED, type: "prompt" })).toBeNull();
    expect(archiveRequestOf(null)).toBeNull();
  });

  it("expires at its stamp, and a bad stamp reads as expired", () => {
    const request = archiveRequestOf(TYPED)!;
    expect(archiveExpired(request, NOW)).toBe(false);
    expect(archiveExpired(request, Date.parse("2026-10-16T10:00:00Z"))).toBe(true);
    expect(archiveExpired({ kind: "archive_task", expires_at: "soon" }, NOW)).toBe(true);
    expect(archiveExpired({ kind: "archive_task" }, NOW)).toBe(true);
  });

  it("knows the run's member, whatever the case", () => {
    expect(isRunMember({ member_email: "Priya@Example.com" }, "priya@example.com")).toBe(true);
    expect(isRunMember({ member_email: "priya@example.com" }, "vj@example.com")).toBe(false);
    expect(isRunMember({ member_email: "priya@example.com" }, "")).toBe(false);
  });

  it("draws the two options with no free text", () => {
    const question = archiveCardQuestion(TYPED);
    expect(question.allowFreeformInput).toBe(false);
    expect(question.multiSelect).toBe(false);
    expect(question.options?.map((o) => o.label)).toEqual(["Approve", "Decline"]);
    const html = renderToStaticMarkup(
      createElement(ElicitationCard, { questions: [question], onSubmit: () => {}, hint: ARCHIVE_HINT }),
    );
    expect(html.match(/data-elicitation-option/g)).toHaveLength(2);
    expect(html).not.toContain("Or type your own answer");
    expect(html).toContain(ARCHIVE_HINT);
  });

  it("sends the decision, never a label", () => {
    expect(archiveDecisionOf(TYPED, { Question: { selected: ["Approve"] } })).toBe("approve");
    expect(archiveDecisionOf(TYPED, { Question: { selected: ["Decline"] } })).toBe("decline");
    expect(archiveDecisionOf(TYPED, { Question: { selected: [] } })).toBeNull();
    expect(archiveDecisionOf(TYPED, { Question: { selected: ["Archive it"] } })).toBeNull();
    expect(archiveApprovalPayload("approve", "k9")).toEqual({
      decision: "approve",
      idempotency_key: "k9",
    });
  });

  it("names who must answer for every other member", () => {
    expect(archiveWaitingText({ member_email: "priya.sharma@example.com" })).toBe(
      "Waiting for Priya",
    );
    const labels = new Map([["priya.sharma@example.com", "Priyanka Sharma"]]);
    expect(archiveWaitingText({ member_email: "priya.sharma@example.com" }, labels)).toBe(
      "Waiting for Priyanka",
    );
  });

  it("collapses to the decision once the member answers", () => {
    const prompt: AgentActivity = {
      id: "p9",
      seq: 8,
      type: "prompt",
      content: { selected: ["approve"], archived: true },
      actor: "vj@example.com",
      in_reply_to: "q9",
    };
    expect(answerTo([TYPED, prompt], TYPED)).toBe(prompt);
    expect(answeredLine(prompt, TYPED)).toBe("Answered by Vj: Approve");
  });
});
