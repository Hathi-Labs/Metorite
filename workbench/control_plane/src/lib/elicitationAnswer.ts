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
 * {@link answerSummary} and {@link submitBlockedReason} are for the member:
 * the one line an answered card shows, and the line that says why Submit
 * is off.
 */

import { plainTitle } from "@/lib/fencedText";

export interface ElicitationOption {
  label: string;
  description?: string | null;
  recommended?: boolean;
  /** The key of an option of a CRM question card (`ask_tools`). The
   *  "Use the default" option has the key {@link CRM_DEFAULT_KEY}. */
  key?: string;
}

export interface ElicitationQuestion {
  header: string;
  question: string;
  multiSelect?: boolean;
  allowFreeformInput?: boolean;
  options?: ElicitationOption[] | null;
  /**
   * `false` lets the member skip this question: it never holds Submit back.
   * Absent or `true`, the question needs an answer. The server sends the
   * key only when the model set it to `false` (`acb_skills.ask_tools`).
   */
  required?: boolean;
  /**
   * What the run uses when the member skips the question. The server sends
   * the key only when the model set it (`ask_tools.card_default`). The card
   * draws it as "If you skip: <default>", because a "Not now" may name only
   * a default that the member saw (review of #1152).
   */
  default?: string;
}

/** The key of the "Use the default" option of a CRM card (`ask_tools`). */
export const CRM_DEFAULT_KEY = "@default";

/** The words before a question's default on the card. */
export const IF_YOU_SKIP = "If you skip";

/**
 * The default a question STATES, or `""`: the "Use the default" option of a
 * CRM card, in its description's words, else the question's own `default`.
 * A recommended option is never a default. The same rule as
 * `ask_tools.stated_default`, so the card shows each default the model may
 * name after a "Not now". Fence: `elicitationAnswer.test.ts`.
 */
export function statedDefault(q: ElicitationQuestion): string {
  const kept = (q.options ?? []).find((o) => o?.key === CRM_DEFAULT_KEY);
  if (kept) return String(kept.description || kept.label || "").trim();
  return typeof q.default === "string" ? q.default.trim() : "";
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
 * Has the member answered this question? A pick is an answer, and typed
 * text in the question's box is an answer too. A question with no options
 * and no box can take no answer, so it stays unanswered, as before.
 */
export function isAnswered(
  q: ElicitationQuestion,
  picks: ElicitationAnswers,
  typed: Readonly<Record<string, string>>,
): boolean {
  const hasOptions = !!q.options && q.options.length > 0;
  const hasFreeform = q.allowFreeformInput !== false;
  const selected = picks[q.header]?.selected?.length ?? 0;
  const freeformFilled = (typed[q.header] ?? "").trim().length > 0;
  return (hasOptions && selected > 0) || (hasFreeform && freeformFilled);
}

/**
 * The questions that hold Submit back, in the card's order: each one with
 * no answer that is not optional (`required: false`).
 */
export function unansweredQuestions(
  questions: readonly ElicitationQuestion[],
  picks: ElicitationAnswers,
  typed: Readonly<Record<string, string>>,
): ElicitationQuestion[] {
  return questions.filter((q) => q.required !== false && !isAnswered(q, picks, typed));
}

/**
 * May the member submit? Every question that is not optional needs an
 * answer. When a question offers both options and a box, typed text alone
 * is an answer.
 */
export function canSubmitAnswers(
  questions: readonly ElicitationQuestion[],
  picks: ElicitationAnswers,
  typed: Readonly<Record<string, string>>,
): boolean {
  return unansweredQuestions(questions, picks, typed).length === 0;
}

/** The most question names a blocked Submit lists. More reads as a count. */
const MAX_NAMED = 3;

/**
 * Why Submit is off, in words for the member, or `null` when it is on.
 *
 * Owner report, 2026-10-17: the member picked an option and could not
 * submit, because a text question above it had no answer, and nothing said
 * so. Now the line beside Submit names each question that blocks it:
 * "Answer Target deadline to submit." A card of one question shows no
 * header, so it says "Answer the question to submit."
 */
export function submitBlockedReason(
  questions: readonly ElicitationQuestion[],
  unanswered: readonly ElicitationQuestion[],
): string | null {
  if (unanswered.length === 0) return null;
  if (questions.length === 1) return "Answer the question to submit.";
  if (unanswered.length > MAX_NAMED) return `Answer the ${unanswered.length} open questions to submit.`;
  const names = unanswered.map((q) => plainTitle(q.header) || "the question");
  const listed = names.length === 1
    ? names[0]
    : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return `Answer ${listed} to submit.`;
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
 * The one line an answered card shows: "Archive both #22 and #23". Display
 * only, and never a word the run did not get (review of #945, P2): one
 * question shows the exact answer that {@link blockingAnswer} sends, so
 * typed text alone when the member also picked. More than one question
 * sends every block, so each answer follows its header.
 */
export function answerSummary(
  questions: readonly ElicitationQuestion[],
  answers: ElicitationAnswers,
): string {
  if (questions.length === 1) return blockingAnswer(questions, answers).answer;
  const one = (h: string) => {
    const a = answers[h] ?? {};
    return [...(a.selected ?? []), a.freeform?.trim() ?? ""].filter(Boolean).join(", ");
  };
  return questions
    .map((q) => {
      const said = one(q.header);
      return said ? `${q.header}: ${said}` : "";
    })
    .filter(Boolean)
    .join(" · ");
}
