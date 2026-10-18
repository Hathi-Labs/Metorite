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
 *   question that is not optional has no answer.
 *
 * ## Never stuck without a reason (owner report, 2026-10-17)
 *
 * The owner picked an option on a card of two questions and could not
 * submit. The pick did count. The first question was a text question with
 * no answer, and nothing on the card said that it blocked Submit. Now:
 *
 * - The line beside Submit names each question that blocks it
 *   (`submitBlockedReason`), and Submit points at that line through
 *   `aria-describedby`.
 * - Once the member answers one question, each question that still blocks
 *   Submit shows "Needs an answer".
 * - A question with `required: false` never blocks Submit, and it shows
 *   "Optional".
 *
 * ## Not now (owner request, 2026-10-18)
 *
 * The member may decline the card. "Not now" beside Submit, and the X in
 * the title row, send the decline (`lib/declineAnswer.ts`), and the run goes
 * on without an answer. The card then locks, and its title row reads "Not
 * answered · You can ask again later." It never draws as an error. Submit
 * and Not now share one gate (`cardExit`), so only the first one fires. The
 * controls come from `components/NotNow.tsx`, the one control of every card
 * that parks a run. A card with no `onDecline` draws neither.
 *
 * A question, a header and an option draw through `GenUiText`, so a «mark»
 * never reaches the member (AGENTS.md rule 4).
 *
 * Fences: `src/components/elicitationCard.test.ts`,
 * `src/lib/elicitationAnswer.test.ts`, `src/lib/chatCardConformance.test.ts`
 * and `e2e/genui-option-picker.spec.ts`.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";

import GenUiText from "@/components/GenUiText";
import Icon from "@/components/Icon";
import { NotNowButton, NotNowClose } from "@/components/NotNow";
import RecommendedBadge from "@/components/RecommendedBadge";
import RollupCard, { RollupBody, RollupHeader } from "@/components/RollupCard";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { CARD_FRAME_CLASS } from "@/components/ui/Collapsible";
import { Textarea } from "@/components/ui/Input";
import { cardExit, DECLINED_NOTE, DECLINED_TITLE } from "@/lib/declineAnswer";
import { plainTitle } from "@/lib/fencedText";
import {
  collectAnswers,
  isAnswered as hasAnswer,
  submitBlockedReason,
  unansweredQuestions,
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
/** The icon of a declined card ("Not now"). */
export const DECLINED_ICON = "CircleMinus";
/** The line beside Submit. */
export const SUBMIT_HINT = "The assistant waits for your answer.";
/** The mark of a question that still blocks Submit. */
export const NEEDS_ANSWER = "Needs an answer";
/** The mark of a question the member may skip (`required: false`). */
export const OPTIONAL_MARK = "Optional";

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
  /**
   * The member's "Not now". With it, the card draws "Not now" beside
   * Submit and an X in its title row. A card with no handler has neither.
   */
  onDecline?: () => void;
  /** The member declined the card: it locks and reads "Not answered". */
  declined?: boolean;
  /** The roll-up's id: stable for the card's life (its request id). */
  id?: string;
  /**
   * The line beside Submit. The chat's default names "the assistant". The
   * agent run panel of a task (EA-12c) names the outside agent.
   */
  hint?: string;
  /**
   * The short title of an open card, in place of "Question". The agent run
   * panel names an archive request this way (WS-55 EA-6g). An answered card
   * still reads "Answered".
   */
  title?: string;
}

export default function ElicitationCard({
  questions,
  onSubmit,
  disabled = false,
  answered,
  onDecline,
  declined = false,
  id,
  hint = SUBMIT_HINT,
  title: openTitle,
}: ElicitationCardProps) {
  const [answers, setAnswers] = useState<ElicitationAnswers>({});
  const [freeformTexts, setFreeformTexts] = useState<Record<string, string>>({});
  const uid = useId();
  const isAnswered = answered !== undefined;
  // Answered or declined: the card waits no more.
  const closed = isAnswered || declined;
  const locked = disabled || closed;
  // Submit and Not now: only the first one fires (`cardExit`). A card the
  // chat opens again after a failed send (a retry) gets a fresh gate.
  const exit = useRef(cardExit());
  useEffect(() => {
    if (!closed) exit.current = cardExit();
  }, [closed]);

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

  const unanswered = unansweredQuestions(questions, answers, freeformTexts);
  const canSubmit = unanswered.length === 0;
  const blockedReason = submitBlockedReason(questions, unanswered);
  // The marks wait for the first answer, so a new card is not a wall of
  // warnings. The line beside Submit says it from the start.
  const touched = questions.some((q) => hasAnswer(q, answers, freeformTexts));
  const reasonId = `${uid}-blocked`;
  const handleSubmit = () => {
    if (locked || !canSubmit) return;
    exit.current.submit(() => onSubmit(collectAnswers(questions, answers, freeformTexts)));
  };
  const handleDecline = () => {
    if (locked || !onDecline) return;
    exit.current.decline(onDecline);
  };

  if (questions.length === 0) return null;
  const many = questions.length > 1;
  const title = declined
    ? DECLINED_TITLE
    : isAnswered ? ANSWERED_TITLE : (openTitle ?? questionsTitle(questions.length));
  const aside = declined ? DECLINED_NOTE : answered ? plainTitle(answered) : undefined;
  const canDecline = !!onDecline && !closed;

  return (
    <RollupCard
      id={id ?? "ask:questions"}
      title={title}
      icon={declined ? DECLINED_ICON : isAnswered ? ANSWERED_ICON : QUESTION_ICON}
      header="own"
      pending={!closed}
      summary={declined ? DECLINED_NOTE : (answered || undefined)}
    >
      <section
        aria-label={title}
        data-elicitation-card={declined ? "declined" : isAnswered ? "answered" : "open"}
        className={CARD_FRAME_CLASS}
      >
        {canDecline ? (
          <div className="flex min-w-0 items-center gap-1.5">
            <div className="min-w-0 flex-1">
              <RollupHeader aside={aside} />
            </div>
            <NotNowClose onClick={handleDecline} disabled={disabled} />
          </div>
        ) : (
          <RollupHeader aside={aside} />
        )}
        <RollupBody className="space-y-2.5 pt-1.5">
          {questions.map((q, qi) => {
            const hasOptions = !!q.options && q.options.length > 0;
            const multi = !!q.multiSelect;
            const selected = answers[q.header]?.selected ?? [];
            const ft = freeformTexts[q.header] ?? "";
            const qid = `${uid}-q${qi}`;
            const optional = q.required === false;
            const blocks = !closed && touched && unanswered.includes(q);
            return (
              <div
                key={qi}
                role="group"
                aria-labelledby={qid}
                data-elicitation-unanswered={blocks ? "" : undefined}
                className="space-y-1.5"
              >
                {many && (
                  <div className="flex flex-wrap items-center gap-1.5 text-[10px] font-medium text-muted-foreground">
                    <GenUiText text={q.header} />
                    {optional && !closed && <span className="font-normal">· {OPTIONAL_MARK}</span>}
                    {blocks && (
                      <Badge tone="warning" size="xs">
                        {NEEDS_ANSWER}
                      </Badge>
                    )}
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

                {q.allowFreeformInput !== false && !closed && (
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

          {!closed && (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                icon="Send"
                onClick={handleSubmit}
                disabled={locked || !canSubmit}
                aria-describedby={blockedReason ? reasonId : undefined}
                data-elicitation-submit=""
              >
                Submit
              </Button>
              {canDecline && <NotNowButton onClick={handleDecline} disabled={disabled} />}
              {/* Always mounted, so a screen reader hears it change. */}
              <span
                id={reasonId}
                aria-live="polite"
                data-elicitation-blocked=""
                className="text-[10px] font-medium text-foreground empty:hidden"
              >
                {blockedReason ?? ""}
              </span>
              <span className="text-[10px] text-muted-foreground">{hint}</span>
            </div>
          )}
        </RollupBody>
      </section>
    </RollupCard>
  );
}
