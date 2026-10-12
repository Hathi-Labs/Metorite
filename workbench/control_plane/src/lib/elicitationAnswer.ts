/**
 * The answer of a question card (`ask_questions` and `ask_user`), as the
 * run reads it. Pure, so the node-env vitest holds the wire shape.
 *
 * ⚠️ **These strings are a contract with the run, not display text.** The
 * blocking path posts `{request_id, answer, was_freeform}` to
 * `/api/agent/respond-input`, and the tool hands `answer` to the model as
 * "User response: …". The other path sends the formatted text as the next
 * chat message. The shapes were written inline in `AgentChat` until
 * 2026-10-13, and they moved here word for word, so a test can hold them.
 * Fence: `src/lib/elicitationAnswer.test.ts`, which snapshots each one.
 *
 * The card's look is `components/ElicitationCard.tsx`. Only
 * {@link answerSummary} is for the member: the one line an answered card
 * shows.
 */

export interface ElicitationOption {
  label: string;
  description?: string | null;
  recommended?: boolean;
}

export interface ElicitationQuestion {
  header: string;
  question: string;
  multiSelect?: boolean;
  allowFreeformInput?: boolean;
  options?: ElicitationOption[] | null;
}

export interface ElicitationAnswers {
  [header: string]: {
    selected?: string[];
    freeform?: string;
  };
}

/** The key of the one question an `ask_user` prompt becomes. */
export const ASK_USER_HEADER = "Question";

/** Each question's picks and typed text, in the card's order. */
export function collectAnswers(
  questions: readonly ElicitationQuestion[],
  picks: ElicitationAnswers,
  typed: Readonly<Record<string, string>>,
): ElicitationAnswers {
  const final: ElicitationAnswers = {};
  for (const q of questions) {
    final[q.header] = {
      selected: picks[q.header]?.selected ?? [],
      freeform: typed[q.header] || undefined,
    };
  }
  return final;
}

/**
 * May the member submit? Every question needs an answer. When a question
 * offers both options and a box, typed text alone is an answer.
 */
export function canSubmitAnswers(
  questions: readonly ElicitationQuestion[],
  picks: ElicitationAnswers,
  typed: Readonly<Record<string, string>>,
): boolean {
  return questions.every((q) => {
    const hasOptions = !!q.options && q.options.length > 0;
    const hasFreeform = q.allowFreeformInput !== false;
    const selected = picks[q.header]?.selected?.length ?? 0;
    const freeformFilled = (typed[q.header] ?? "").trim().length > 0;
    if (hasOptions && hasFreeform) return selected > 0 || freeformFilled;
    if (hasOptions) return selected > 0;
    if (hasFreeform) return freeformFilled;
    return false;
  });
}

/** Every answer as `[header]` blocks: the message the non-blocking path sends. */
export function formattedAnswers(answers: ElicitationAnswers): string {
  return Object.entries(answers)
    .map(([header, ans]) => {
      const parts: string[] = [`[${header}]`];
      if (ans.selected && ans.selected.length > 0) {
        parts.push(`Selected: ${ans.selected.join(", ")}`);
      }
      if (ans.freeform) {
        parts.push(`Answer: ${ans.freeform}`);
      }
      return parts.join("\n");
    })
    .join("\n\n");
}

/**
 * The answer a BLOCKING `ask_questions` card posts. One question sends its
 * typed text, else its picks; more than one sends the formatted blocks.
 */
export function blockingAnswer(
  questions: readonly ElicitationQuestion[],
  answers: ElicitationAnswers,
): { answer: string; wasFreeform: boolean } {
  const formatted = formattedAnswers(answers);
  const single = questions.length === 1;
  const firstHeader = questions[0]?.header ?? "Question";
  const a = answers[firstHeader] ?? {};
  const freeform = a.freeform?.trim();
  const selectedJoined = (a.selected ?? []).join(", ");
  const answer = single ? (freeform || selectedJoined || formatted) : formatted;
  const wasFreeform = single && !!freeform && !selectedJoined;
  return { answer, wasFreeform };
}

/** The answer an `ask_user` prompt posts: the typed text, else the pick. */
export function userInputAnswer(answers: ElicitationAnswers): { answer: string; wasFreeform: boolean } {
  const a = answers[ASK_USER_HEADER] ?? {};
  const selected = a.selected?.[0];
  const freeform = a.freeform?.trim();
  const answer = freeform || selected || "";
  const wasFreeform = !!freeform && !selected;
  return { answer, wasFreeform };
}

/**
 * The one line an answered card shows: "Archive both #22 and #23". More
 * than one question puts each answer after its header. Display only.
 */
export function answerSummary(
  questions: readonly ElicitationQuestion[],
  answers: ElicitationAnswers,
): string {
  const one = (h: string) => {
    const a = answers[h] ?? {};
    return [...(a.selected ?? []), a.freeform?.trim() ?? ""].filter(Boolean).join(", ");
  };
  if (questions.length === 1) return one(questions[0].header);
  return questions
    .map((q) => {
      const said = one(q.header);
      return said ? `${q.header}: ${said}` : "";
    })
    .filter(Boolean)
    .join(" · ");
}
