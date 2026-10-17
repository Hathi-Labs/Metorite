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
  isAnswered,
  submitBlockedReason,
  unansweredQuestions,
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

// Owner report, 2026-10-17: "even after selecting, i cant submit." The card
// had two questions. The pick on the second one counted, and Submit stayed
// off because the first, a text question, had no answer. Nothing said so.
// Mutations caught: a pick that stops counting, typed text that stops
// counting, an optional question that still blocks, a reason that names
// the wrong question or none, and a recommended option sent unpicked.
const OWNER: ElicitationQuestion[] = [
  {
    header: "Target deadline",
    question: "What date should the system be live by? I'll schedule the plan tasks backwards from it.",
    multiSelect: false,
    allowFreeformInput: true,
    options: null,
  },
  {
    header: "Task owners",
    question: "Who should own the plan tasks?",
    multiSelect: false,
    allowFreeformInput: true,
    options: [
      { label: "Propose from the team", description: null, recommended: true },
      { label: "I'll give owners", description: null, recommended: false },
    ],
  },
];
const PICK = { "Task owners": { selected: ["Propose from the team"] } };
const DATE = { "Target deadline": "2026-12-01" };

describe("the owner's two-question card", () => {
  it("counts a pick alone as the answer to its question", () => {
    expect(isAnswered(OWNER[1], PICK, {})).toBe(true);
    expect(unansweredQuestions(OWNER, PICK, {})).toEqual([OWNER[0]]);
  });

  it("holds Submit back on the text question, and names it", () => {
    expect(canSubmitAnswers(OWNER, PICK, {})).toBe(false);
    expect(submitBlockedReason(OWNER, unansweredQuestions(OWNER, PICK, {}))).toBe(
      "Answer Target deadline to submit.",
    );
  });

  it("counts typed text alone as an answer, and names the question left", () => {
    expect(isAnswered(OWNER[0], {}, DATE)).toBe(true);
    expect(isAnswered(OWNER[0], {}, { "Target deadline": "   " })).toBe(false);
    expect(canSubmitAnswers(OWNER, {}, DATE)).toBe(false);
    expect(submitBlockedReason(OWNER, unansweredQuestions(OWNER, {}, DATE))).toBe(
      "Answer Task owners to submit.",
    );
  });

  it("turns Submit on when both questions have an answer", () => {
    expect(canSubmitAnswers(OWNER, PICK, DATE)).toBe(true);
    expect(submitBlockedReason(OWNER, unansweredQuestions(OWNER, PICK, DATE))).toBeNull();
  });

  it("names every question of a card with no answer yet", () => {
    expect(submitBlockedReason(OWNER, unansweredQuestions(OWNER, {}, {}))).toBe(
      "Answer Target deadline and Task owners to submit.",
    );
  });

  it("takes the own-answer text in place of a pick", () => {
    const own = { ...DATE, "Task owners": "Priya owns all of them" };
    expect(canSubmitAnswers(OWNER, {}, own)).toBe(true);
    const sent = blockingAnswer(OWNER, collectAnswers(OWNER, {}, own)).answer;
    expect(sent).toBe("[Target deadline]\nAnswer: 2026-12-01\n\n[Task owners]\nAnswer: Priya owns all of them");
    expect(sent).not.toContain("Selected:");
  });

  it("lets an optional question stay empty", () => {
    const optional = [{ ...OWNER[0], required: false }, OWNER[1]];
    expect(canSubmitAnswers(optional, PICK, {})).toBe(true);
    expect(submitBlockedReason(optional, unansweredQuestions(optional, PICK, {}))).toBeNull();
    // `required: true` is the same as no key.
    const marked = [{ ...OWNER[0], required: true }, OWNER[1]];
    expect(canSubmitAnswers(marked, PICK, {})).toBe(false);
  });

  it("sends both answers under their own headers, and no unpicked recommendation", () => {
    const answers = collectAnswers(OWNER, PICK, DATE);
    expect(answers).toEqual({
      "Target deadline": { selected: [], freeform: "2026-12-01" },
      "Task owners": { selected: ["Propose from the team"], freeform: undefined },
    });
    expect(blockingAnswer(OWNER, answers)).toEqual({
      answer: "[Target deadline]\nAnswer: 2026-12-01\n\n[Task owners]\nSelected: Propose from the team",
      wasFreeform: false,
    });
    // The member typed a date and picked nothing: the recommended option
    // does not go out.
    const unpicked = collectAnswers(OWNER, {}, { ...DATE, "Task owners": "Ask me later" });
    expect(unpicked["Task owners"].selected).toEqual([]);
    expect(blockingAnswer(OWNER, unpicked).answer).not.toContain("Propose from the team");
  });
});

describe("the reason beside a blocked Submit", () => {
  const q = (header: string): ElicitationQuestion => ({ header, question: `${header}?` });

  it("says 'the question' on a card of one question, which shows no header", () => {
    expect(submitBlockedReason(ONE, ONE)).toBe("Answer the question to submit.");
  });

  it("lists three names, and counts more than three", () => {
    const three = [q("A"), q("B"), q("C")];
    expect(submitBlockedReason(three, three)).toBe("Answer A, B and C to submit.");
    const four = [...three, q("D")];
    expect(submitBlockedReason(four, four)).toBe("Answer the 4 open questions to submit.");
  });

  it("names a header without its «marks»", () => {
    const fenced = [q("Owner of «Launch»"), q("B")];
    expect(submitBlockedReason(fenced, [fenced[0]])).toBe("Answer Owner of Launch to submit.");
  });

  it("keeps a question with no options and no box unanswered, as before", () => {
    const mute = { header: "Note", question: "Read this.", allowFreeformInput: false, options: null };
    expect(isAnswered(mute, {}, { Note: "typed" })).toBe(false);
  });
});
