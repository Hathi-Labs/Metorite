/**
 * typedAnswer — which waiting question a message from the composer answers.
 *
 * Owner report, 2026-10-23. The assistant asked "What is Venna's email?",
 * and the owner typed "Veena is the name" in the message box. It went as a
 * steer, so the run still waited on the card, and the status read "Waiting
 * for your answer" for minutes. A person who types while one question waits
 * is answering it, as in a chat with a colleague.
 *
 * The rule: the text answers a question only when exactly one question
 * waits, the run is parked on it, and it takes typed text. A card of several
 * questions, or one that only takes picks, keeps the steer.
 *
 * Pure, so the node-env vitest holds it. Fence: `src/lib/typedAnswer.test.ts`.
 */

import type { ElicitationAnswers, ElicitationQuestion } from "./elicitationAnswer";
import { ASK_USER_HEADER } from "./elicitationAnswer";

/** The caption under the box when a send answers the waiting question. */
export const ANSWER_CAPTION = "Answers the question above";

interface Waits {
  answered?: string;
  declined?: boolean;
}

export interface TypedAnswerSources {
  /** An `ask_user` prompt. */
  userInput: (Waits & { requestId: string; allowFreeform: boolean }) | null;
  /** A question card. Only one with a request id parks a run. */
  elicitation: (Waits & { requestId?: string; questions: readonly ElicitationQuestion[] }) | null;
}

export type TypedAnswerTarget = "userInput" | "elicitation" | null;

const waits = (w: Waits | null): boolean => !!w && w.answered === undefined && !w.declined;

/** The question that typed text answers, or null for a steer. */
export function typedAnswerTarget(s: TypedAnswerSources): TypedAnswerTarget {
  const u = waits(s.userInput) ? s.userInput : null;
  const e = waits(s.elicitation) && s.elicitation?.requestId ? s.elicitation : null;
  // Two waiting cards: the text cannot say which one it answers.
  if (u && e) return null;
  if (u) return u.allowFreeform ? "userInput" : null;
  if (e && e.questions.length === 1 && e.questions[0].allowFreeformInput !== false) return "elicitation";
  return null;
}

/** The card answers that typed text makes, in the shape the card submits. */
export function typedAnswers(
  target: Exclude<TypedAnswerTarget, null>,
  text: string,
  questions: readonly ElicitationQuestion[] = [],
): ElicitationAnswers {
  const header = target === "userInput" ? ASK_USER_HEADER : (questions[0]?.header ?? ASK_USER_HEADER);
  return { [header]: { freeform: text.trim() } };
}
