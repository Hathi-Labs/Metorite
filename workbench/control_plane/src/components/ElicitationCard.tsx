"use client";

/**
 * ElicitationCard — the question card of `ask_questions` and `ask_user`.
 *
 * The agent calls `ask_questions` (or the Copilot SDK's `ask_user`), and the
 * run parks on the member's answer. This card draws the questions in the
 * chat, and Submit sends the answer (`lib/elicitationAnswer.ts` holds its
 * wire shape).
 *
 * ## One look with the other chat cards (owner report, 2026-10-13)
 *
 * The owner said the card looked "out of place compared to the other AGUI
 * cards". It had a tall tinted header with a 💬 emoji, a title that was a
 * fragment of the question, large rounded option pills, a yellow star badge
 * and a large footer. Now it is a chat card like the receipts:
 *
 * - It draws through `RollupCard` with `header="own"`. Its title row is
 *   `RollupHeader`, with an icon from the app set and a short label
 *   ("Question", "2 questions"), never a part of the question.
 * - It waits on the member, so it is `pending`: no toggle, no fold. Once it
 *   is answered (`answered`), it draws "Answered" and the answer on its
 *   title row, and it may fold like any older card.
 * - The question is in the body, whole. The server cuts a long text at a
 *   word, with an ellipsis (`acb_skills.ask_tools.clip_words`), and this
 *   card shows what came. It never cuts the text again.
 * - Each option is one dense row: the shared `Button`, its `selected` pair,
 *   and a circle (one choice) or a box (many) from the icon set.
 * - `RecommendedBadge` is the one mark of a recommended option.
 * - Submit is the shared primary `Button`. It is disabled only while a
 *   question has no answer.
 *
 * A question, a header and an option draw through `GenUiText`, so a «mark»
 * never reaches the member (AGENTS.md rule 4).
 *
 * Fences: `src/components/elicitationCard.test.ts`,
 * `src/lib/elicitationAnswer.test.ts`, `src/lib/chatCardConformance.test.ts`
 * and `e2e/genui-option-picker.spec.ts`.
 */

import { useCallback, useId, useState } from "react";

import GenUiText from "@/components/GenUiText";
import Icon from "@/components/Icon";
import RecommendedBadge from "@/components/RecommendedBadge";
import RollupCard, { RollupBody, RollupHeader } from "@/components/RollupCard";
import Button from "@/components/ui/Button";
import { CARD_FRAME_CLASS } from "@/components/ui/Collapsible";
import { Textarea } from "@/components/ui/Input";
import {
  canSubmitAnswers,
  collectAnswers,
  type ElicitationAnswers,
  type ElicitationQuestion,
} from "@/lib/elicitationAnswer";

export type { ElicitationAnswers, ElicitationOption, ElicitationQuestion } from "@/lib/elicitationAnswer";

/** The icon of a question card, from the app's one icon set. */
export const QUESTION_ICON = "MessageCircleQuestion";
/** The icon of an answered question card. */
export const ANSWERED_ICON = "CircleCheck";
/** The title of an answered card. The answer follows it on the row. */
export const ANSWERED_TITLE = "Answered";
/** The line beside Submit. */
export const SUBMIT_HINT = "The assistant waits for your answer.";

/** The card's short title. Never a part of the question. */
export function questionsTitle(count: number): string {
  return count === 1 ? "Question" : `${count} questions`;
}

interface ElicitationCardProps {
  questions: ElicitationQuestion[];
  onSubmit: (answers: ElicitationAnswers) => void;
  disabled?: boolean;
  /**
   * The card got its answer: the one line to show (`answerSummary`). The
   * card then takes no click, draws no Submit, and may fold.
   */
  answered?: string;
  /** The roll-up's id: stable for the card's life (its request id). */
  id?: string;
}

export default function ElicitationCard({
  questions,
  onSubmit,
  disabled = false,
  answered,
  id,
}: ElicitationCardProps) {
  const [answers, setAnswers] = useState<ElicitationAnswers>({});
  const [freeformTexts, setFreeformTexts] = useState<Record<string, string>>({});
  const uid = useId();
  const isAnswered = answered !== undefined;
  const locked = disabled || isAnswered;

  const toggleOption = useCallback(
    (header: string, label: string, multi: boolean) => {
      if (locked) return;
      setAnswers((prev) => {
        const cur = prev[header]?.selected ?? [];
        let next: string[];
        if (multi) {
          next = cur.includes(label) ? cur.filter((l) => l !== label) : [...cur, label];
        } else {
          next = cur.includes(label) ? [] : [label];
        }
        return { ...prev, [header]: { ...prev[header], selected: next } };
      });
    },
    [locked],
  );

  const canSubmit = canSubmitAnswers(questions, answers, freeformTexts);
  const handleSubmit = () => {
    if (locked || !canSubmit) return;
    onSubmit(collectAnswers(questions, answers, freeformTexts));
  };

  if (questions.length === 0) return null;
  const many = questions.length > 1;
  const title = isAnswered ? ANSWERED_TITLE : questionsTitle(questions.length);

  return (
    <RollupCard
      id={id ?? "ask:questions"}
      title={title}
      icon={isAnswered ? ANSWERED_ICON : QUESTION_ICON}
      header="own"
      pending={!isAnswered}
      summary={answered || undefined}
    >
      <section
        aria-label={title}
        data-elicitation-card={isAnswered ? "answered" : "open"}
        className={CARD_FRAME_CLASS}
      >
        <RollupHeader aside={answered ? <GenUiText text={answered} /> : undefined} />
        <RollupBody className="space-y-2.5 pt-1.5">
          {questions.map((q, qi) => {
            const hasOptions = !!q.options && q.options.length > 0;
            const multi = !!q.multiSelect;
            const selected = answers[q.header]?.selected ?? [];
            const ft = freeformTexts[q.header] ?? "";
            const qid = `${uid}-q${qi}`;
            return (
              <div key={qi} role="group" aria-labelledby={qid} className="space-y-1.5">
                {many && (
                  <div className="text-[10px] font-medium text-muted-foreground">
                    <GenUiText text={q.header} />
                  </div>
                )}
                <p
                  id={qid}
                  data-elicitation-question=""
                  className="whitespace-pre-wrap break-words text-xs leading-relaxed text-foreground"
                >
                  <GenUiText text={q.question} />
                </p>

                {hasOptions && (
                  <div className="flex flex-col gap-0.5">
                    {q.options!.map((opt, oi) => {
                      const on = selected.includes(opt.label);
                      const mark = multi ? (on ? "SquareCheck" : "Square") : on ? "CircleDot" : "Circle";
                      // ON is the house pair through `Button`'s `selected`
                      // (DESIGN_SYSTEM.md §3), never a hand-made fill.
                      return (
                        <Button
                          key={oi}
                          variant="ghost"
                          size="none"
                          layout="flex items-start justify-start"
                          selected={on}
                          onClick={() => toggleOption(q.header, opt.label, multi)}
                          disabled={locked}
                          data-elicitation-option=""
                          className="-mx-1.5 gap-2 px-1.5 py-1 text-left text-xs"
                        >
                          <Icon name={mark} size={14} className="mt-px shrink-0" />
                          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                            <span className={`flex flex-wrap items-center gap-1.5 break-words ${on ? "" : "text-foreground"}`}>
                              <span className="min-w-0">
                                <GenUiText text={opt.label} />
                              </span>
                              {opt.recommended && <RecommendedBadge />}
                            </span>
                            {opt.description && (
                              <span className="break-words text-[11px] text-muted-foreground">
                                <GenUiText text={opt.description} />
                              </span>
                            )}
                          </span>
                        </Button>
                      );
                    })}
                  </div>
                )}

                {q.allowFreeformInput !== false && !isAnswered && (
                  <Textarea
                    value={ft}
                    onChange={(e) => setFreeformTexts((prev) => ({ ...prev, [q.header]: e.target.value }))}
                    disabled={locked}
                    aria-label={hasOptions ? "Your own answer" : "Your answer"}
                    placeholder={hasOptions ? "Or type your own answer" : "Type your answer"}
                    rows={2}
                    inputSize="sm"
                    className="resize-none"
                  />
                )}
              </div>
            );
          })}

          {!isAnswered && (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                icon="Send"
                onClick={handleSubmit}
                disabled={locked || !canSubmit}
                data-elicitation-submit=""
              >
                Submit
              </Button>
              <span className="text-[10px] text-muted-foreground">{SUBMIT_HINT}</span>
            </div>
          )}
        </RollupBody>
      </section>
    </RollupCard>
  );
}
