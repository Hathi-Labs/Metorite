"use client";

/**
 * CardAnswerContent — the inside of a member's bubble whose turn answered a
 * card that went as a new message (owner report, 2026-10-13,
 * `lib/cardAnswer.ts`).
 *
 * It draws inside the member's normal bubble, so it adds no surface of its
 * own: one quiet line that says what the member did, the question in the
 * card's own words, and then the rows they approved or their own answer. A
 * fenced question draws through `FencedText`, as the confirmation card
 * draws it. Tokens only.
 */

import FencedText from "@/components/FencedText";
import Icon from "@/components/Icon";
import { cardAnswerHeadline, type CardAnswer } from "@/lib/cardAnswer";

function Words({ text, fenced }: { text: string; fenced?: boolean }) {
  return fenced ? <FencedText text={text} pills={false} /> : <>{text}</>;
}

export default function CardAnswerContent({ answer }: { answer: CardAnswer }) {
  const rejected = answer.decision === "reject";
  return (
    <div className="min-w-0 space-y-1" data-card-answer={answer.decision ?? "answer"}>
      <p className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
        <Icon
          name={rejected ? "X" : "Check"}
          size={12}
          className={`shrink-0 ${rejected ? "text-muted-foreground" : "text-success"}`}
        />
        <span>{cardAnswerHeadline(answer)}</span>
      </p>
      {answer.question && (
        <p className="break-words">
          <Words text={answer.question} fenced={answer.fenced} />
        </p>
      )}
      {answer.rows && answer.rows.length > 0 && (
        <ul className="space-y-0.5 text-xs" aria-label="Rows you approved">
          {answer.rows.map((label, i) => (
            <li key={i} className="flex min-w-0 items-start gap-1.5">
              <Icon name="Check" size={10} className="mt-[3px] shrink-0 text-muted-foreground" />
              <span className="min-w-0 break-words">
                <Words text={label} fenced={answer.fenced} />
              </span>
            </li>
          ))}
        </ul>
      )}
      {!answer.rows && answer.rowCount ? (
        <p className="text-xs text-muted-foreground">
          {answer.rowCount === 1 ? "1 row" : `${answer.rowCount} rows`}
        </p>
      ) : null}
      {answer.answer && (
        <p className="whitespace-pre-wrap break-words">{answer.answer}</p>
      )}
    </div>
  );
}
