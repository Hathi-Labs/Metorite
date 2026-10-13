/**
 * The wire shape of a question card's answer (owner report, 2026-10-13).
 *
 * The card's look changed. Its answer must not. These are the strings the
 * run reads: the blocking post to `/api/agent/respond-input`, the message of
 * the non-blocking path, and the `ask_user` answer. Each `toEqual` below is
 * a snapshot of the shape `AgentChat` built inline before the move to
 * `lib/elicitationAnswer.ts`.
 *
 * Mutations caught: a changed `[header]` block, a changed "Selected: " or
 * "Answer: " prefix, a changed join, typed text that stops winning over a
 * pick, and `was_freeform` set for a pick.
 */
import { describe, expect, it } from "vitest";

import {
  ASK_USER_HEADER,
  answerSummary,
  blockingAnswer,
  canSubmitAnswers,
  collectAnswers,
  formattedAnswers,
  userInputAnswer,
  type ElicitationQuestion,
} from "@/lib/elicitationAnswer";

const ONE: ElicitationQuestion[] = [{
  header: "#23 with #22?",
  question: "Archive #22 with #23?",
  allowFreeformInput: false,
  options: [{ label: "Archive both #22 and #23", recommended: true }, { label: "Archive only #22" }],
}];

const TWO: ElicitationQuestion[] = [
  { header: "Scope", question: "Which tasks?", multiSelect: true, options: [{ label: "#22" }, { label: "#23" }] },
  { header: "Note", question: "Add a note?" },
];

describe("the blocking answer", () => {
  it("one question sends its pick", () => {
    const answers = collectAnswers(ONE, { [ONE[0].header]: { selected: ["Archive both #22 and #23"] } }, {});
    expect(answers).toEqual({ "#23 with #22?": { selected: ["Archive both #22 and #23"], freeform: undefined } });
    expect(blockingAnswer(ONE, answers)).toEqual({ answer: "Archive both #22 and #23", wasFreeform: false });
  });

  it("one question sends its typed text before its pick, as free text", () => {
    const q = [{ ...ONE[0], allowFreeformInput: true }];
    const answers = collectAnswers(q, {}, { [q[0].header]: "  Archive neither  " });
    expect(blockingAnswer(q, answers)).toEqual({ answer: "Archive neither", wasFreeform: true });
    const both = collectAnswers(q, { [q[0].header]: { selected: ["Archive only #22"] } }, { [q[0].header]: "and tag it" });
    expect(blockingAnswer(q, both)).toEqual({ answer: "and tag it", wasFreeform: false });
  });

  it("more than one question sends the formatted blocks", () => {
    const answers = collectAnswers(TWO, { Scope: { selected: ["#22", "#23"] } }, { Note: "Same request" });
    const shape = "[Scope]\nSelected: #22, #23\n\n[Note]\nAnswer: Same request";
    expect(formattedAnswers(answers)).toBe(shape);
    expect(blockingAnswer(TWO, answers)).toEqual({ answer: shape, wasFreeform: false });
  });
});

describe("the ask_user answer", () => {
  it("sends the typed text, else the pick", () => {
    expect(userInputAnswer({ [ASK_USER_HEADER]: { selected: ["production"] } })).toEqual({
      answer: "production",
      wasFreeform: false,
    });
    expect(userInputAnswer({ [ASK_USER_HEADER]: { selected: [], freeform: " staging-2 " } })).toEqual({
      answer: "staging-2",
      wasFreeform: true,
    });
    expect(userInputAnswer({})).toEqual({ answer: "", wasFreeform: false });
  });
});

describe("Submit waits for an answer to every question", () => {
  it("is off with no choice, and on with one", () => {
    expect(canSubmitAnswers(ONE, {}, {})).toBe(false);
    expect(canSubmitAnswers(ONE, { [ONE[0].header]: { selected: ["Archive only #22"] } }, {})).toBe(true);
  });

  it("takes typed text alone where a box is offered", () => {
    expect(canSubmitAnswers(TWO, { Scope: { selected: ["#22"] } }, {})).toBe(false);
    expect(canSubmitAnswers(TWO, { Scope: { selected: ["#22"] } }, { Note: "   " })).toBe(false);
    expect(canSubmitAnswers(TWO, { Scope: { selected: ["#22"] } }, { Note: "ok" })).toBe(true);
  });
});

describe("the answered card's line", () => {
  // Review of #945, P2: one question sends its typed text alone when the
  // member also picked. The line showed both, so it named a pick the run
  // never got.
  it("shows, for one question, the exact answer the run got", () => {
    const q = [{ ...ONE[0], allowFreeformInput: true }];
    const both = collectAnswers(q, { [q[0].header]: { selected: ["Archive only #22"] } }, { [q[0].header]: "and tag it" });
    expect(answerSummary(q, both)).toBe("and tag it");
    expect(answerSummary(q, both)).toBe(blockingAnswer(q, both).answer);
  });

  it("is the answer for one question, and each header's answer for more", () => {
    expect(answerSummary(ONE, { [ONE[0].header]: { selected: ["Archive both #22 and #23"] } })).toBe(
      "Archive both #22 and #23",
    );
    expect(answerSummary(TWO, { Scope: { selected: ["#22"] }, Note: { freeform: "ok" } })).toBe("Scope: #22 · Note: ok");
  });
});
