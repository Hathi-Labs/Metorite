/**
 * The redraw of a run's cards after a remount (review of #1171, P1).
 *
 * The defect: the member answered a question, the run went on, and the
 * member closed the pane and came back before the run ended. The log drew
 * the question again as WAITING, and a second answer reached the agent as a
 * steer. A card may redraw as waiting only when nothing says it is settled.
 */

import { describe, expect, it } from "vitest";

import {
  clearCardLog,
  getCardLog,
  noteCardAnswered,
  noteCardEvent,
  noteRunMoved,
} from "@/lib/chatStore";
import { CARD_ANSWERED, planCardRedraw, type CardLogEntry } from "./cardReplay";
import { cardsToSettle, type PendingAsk } from "./pendingAsks";

const ask = (id: string): CardLogEntry => ({ name: "user_input_requested", value: { request_id: id, question: "Which?" } });
const quest = (id: string): CardLogEntry => ({ name: "elicitation_requested", value: { request_id: id, questions: [] } });
const confirm = (id: string): CardLogEntry => ({ name: "confirmation_requested", value: { request_id: id, title: "Do it?" } });

describe("planCardRedraw", () => {
  it("a question with no answer and no later event waits", () => {
    expect(planCardRedraw([ask("q1")])).toEqual([
      { kind: "user_input", requestId: "q1", value: ask("q1").value, waiting: true },
    ]);
  });

  it("a question answered here draws as answered, with the answer", () => {
    const [c] = planCardRedraw([ask("q1"), { name: CARD_ANSWERED, value: { request_id: "q1", answer: "Apollo" } }]);
    expect(c.waiting).toBe(false);
    expect(c.answer).toBe("Apollo");
  });

  it("a question the run moved past is settled, even with no answer here", () => {
    const [c] = planCardRedraw([{ ...quest("q2"), moved: true }]);
    expect(c.waiting).toBe(false);
    expect(c.answer).toBeUndefined();
  });

  it("a confirmation settles on its resolution or on an answer here, and the move of the run does not settle it", () => {
    expect(planCardRedraw([confirm("c1")])[0].waiting).toBe(true);
    expect(planCardRedraw([{ ...confirm("c1"), moved: true }])[0].waiting).toBe(true);
    expect(planCardRedraw([confirm("c1"), { name: "confirmation_resolved", value: { request_id: "c1" } }])[0].waiting).toBe(false);
    const [c] = planCardRedraw([confirm("c1"), { name: CARD_ANSWERED, value: { request_id: "c1", answer: "APPROVE" } }]);
    expect(c).toMatchObject({ waiting: false, answer: "APPROVE" });
  });

  it("a decline draws as declined, and each card once", () => {
    const plan = planCardRedraw([
      ask("q1"), ask("q1"), { name: CARD_ANSWERED, value: { request_id: "q1", answer: "", declined: true } }, confirm("c1"),
    ]);
    expect(plan.map((c) => c.requestId)).toEqual(["q1", "c1"]);
    expect(plan[0]).toMatchObject({ waiting: false, declined: true });
  });
});

describe("the log marks a question as moved by a real event only", () => {
  it("model output moves it, a heartbeat or a progress line does not", () => {
    const t = "t-moved";
    clearCardLog(t);
    noteCardEvent(t, "user_input_requested", { request_id: "q1" });
    noteRunMoved(t, "progress");
    noteRunMoved(t, "tool_args");
    expect(getCardLog(t)[0].moved).toBeFalsy();
    noteRunMoved(t, "delta");
    expect(getCardLog(t)[0].moved).toBe(true);
    // An answer goes into the log too.
    noteCardAnswered(t, "q1", "Apollo");
    expect(planCardRedraw(getCardLog(t))[0]).toMatchObject({ waiting: false, answer: "Apollo" });
    clearCardLog(t);
  });
});

describe("parallel tools never settle a waiting question (review of #1171)", () => {
  // The tools of one model turn run concurrently. Agent A asks and waits;
  // agent B, and a sibling top-level tool, go on streaming meanwhile.
  const SIBLING_EVENTS = [
    "sub_agent_delta", "sub_agent_tool_start", "sub_agent_tool_end", "sub_agent_error",
    "tool_start", "tool_args", "tool_partial", "tool_end", "progress", "todos", "state", "state_delta",
    "custom",
  ];

  it("a question that waits while siblings stream still redraws as waiting", () => {
    const t = "t-parallel";
    clearCardLog(t);
    noteCardEvent(t, "user_input_requested", { request_id: "q1" });
    noteCardEvent(t, "elicitation_requested", { request_id: "q2", questions: [] });
    for (const type of SIBLING_EVENTS) noteRunMoved(t, type);
    const plan = planCardRedraw(getCardLog(t));
    expect(plan.map((c) => [c.requestId, c.waiting])).toEqual([["q1", true], ["q2", true]]);
    clearCardLog(t);
  });

  it("only model output, which comes after the gather, moves it", () => {
    for (const type of ["delta", "reasoning", "message_start", "done"]) {
      const t = `t-out-${type}`;
      noteCardEvent(t, "user_input_requested", { request_id: "q1" });
      noteRunMoved(t, type);
      expect(planCardRedraw(getCardLog(t))[0].waiting, type).toBe(false);
      clearCardLog(t);
    }
  });
});

describe("the server's pending asks are the last word", () => {
  const listed = (id: string): PendingAsk => ({
    requestId: id, kind: "questions", event: "user_input_requested", payload: { request_id: id }, askedAt: null, answerBy: "run",
  });
  it("a redrawn waiting card that the real list does not hold settles", () => {
    expect(cardsToSettle([listed("q1")], new Set(["q1", "q2"]))).toEqual(["q2"]);
    expect(cardsToSettle([], new Set(["q1"]))).toEqual(["q1"]);
  });
  it("no list (the server did not answer) settles nothing", () => {
    expect(cardsToSettle(null, new Set(["q1"]))).toEqual([]);
  });
});
