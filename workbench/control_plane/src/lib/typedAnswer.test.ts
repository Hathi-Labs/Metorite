import { describe, expect, it } from "vitest";
import { blockingAnswer, userInputAnswer } from "./elicitationAnswer";
import { typedAnswers, typedAnswerTarget } from "./typedAnswer";

const ask = { requestId: "r1", allowFreeform: true };
const q = (over: Record<string, unknown> = {}) => ({ header: "Email", question: "What is Venna's email?", ...over });

describe("typedAnswerTarget", () => {
  it("answers a waiting ask_user prompt that takes text", () => {
    expect(typedAnswerTarget({ userInput: ask, elicitation: null })).toBe("userInput");
  });

  it("steers when the prompt only takes picks", () => {
    expect(typedAnswerTarget({ userInput: { ...ask, allowFreeform: false }, elicitation: null })).toBeNull();
  });

  it("steers once the question is answered or declined", () => {
    expect(typedAnswerTarget({ userInput: { ...ask, answered: "x" }, elicitation: null })).toBeNull();
    expect(typedAnswerTarget({ userInput: { ...ask, declined: true }, elicitation: null })).toBeNull();
  });

  it("answers a parked card of one question", () => {
    expect(typedAnswerTarget({ userInput: null, elicitation: { requestId: "e1", questions: [q()] } })).toBe("elicitation");
  });

  it("steers for a card of several questions, a pick-only one, or one that parks no run", () => {
    expect(typedAnswerTarget({ userInput: null, elicitation: { requestId: "e1", questions: [q(), q({ header: "B" })] } })).toBeNull();
    expect(typedAnswerTarget({ userInput: null, elicitation: { requestId: "e1", questions: [q({ allowFreeformInput: false })] } })).toBeNull();
    expect(typedAnswerTarget({ userInput: null, elicitation: { questions: [q()] } })).toBeNull();
  });

  it("steers when two questions wait, because the text names neither", () => {
    expect(typedAnswerTarget({ userInput: ask, elicitation: { requestId: "e1", questions: [q()] } })).toBeNull();
  });
});

describe("typedAnswers", () => {
  it("makes the same wire answer as the card's own typed text", () => {
    expect(userInputAnswer(typedAnswers("userInput", " Veena is the name "))).toEqual({
      answer: "Veena is the name",
      wasFreeform: true,
    });
    const questions = [q()];
    expect(blockingAnswer(questions, typedAnswers("elicitation", "veena@fracktal.in", questions))).toEqual({
      answer: "veena@fracktal.in",
      wasFreeform: true,
    });
  });
});
