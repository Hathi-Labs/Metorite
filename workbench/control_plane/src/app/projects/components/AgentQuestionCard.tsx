"use client";

/**
 * The answer part of an agent question — ONE component (WS-55 EA-18b,
 * `external_agent_access.md` §12.8).
 *
 * It was the middle of `AgentRunPanel`. Now the run panel and the Questions
 * list both render it, and neither keeps a copy. It draws one of five things
 * for one open or answered question:
 *
 * - the chat's question card (`ElicitationCard`), with the agent's options in
 *   the order it sent them, a "recommended" mark, check boxes for
 *   `allow_multiple`, and a free-text field;
 * - for a typed archive request, the server's two options and no free text,
 *   for the run's member. Approve and Decline go to `onDecision`, and the host
 *   sends them to `archive-approval`, never to `/activities`;
 * - the same request as a read-only block, with "Waiting for <name>", for
 *   everybody else, and its expired state;
 * - a read-only question with "<name> or an admin can answer this", for a
 *   reader the server says may not answer (`canAnswer` false);
 * - after the answer, one line: "Answered by <name>: <labels>".
 *
 * ⚠️ **It decides no rule.** `canAnswer` is the server's (`can_answer`, from
 * `may_answer_run`). `kind` is the server's too. The host owns the writes, the
 * busy flag and the error line.
 *
 * Fence: `agentQuestionCard.test.ts`.
 */

import Icon from "@/components/Icon";
import ElicitationCard from "@/components/ElicitationCard";
import Button from "@/components/ui/Button";
import type { ElicitationAnswers } from "@/lib/elicitationAnswer";

import {
  ARCHIVE_CARD_TITLE,
  ARCHIVE_EXPIRED_HINT,
  ARCHIVE_EXPIRED_TEXT,
  ARCHIVE_HINT,
  type AgentActivity,
  type AgentRun,
  type ArchiveDecision,
  answeredLine,
  archiveCardQuestion,
  archiveDecisionOf,
  archiveExpired,
  archiveRequestOf,
  archiveWaitingText,
  cardQuestion,
  firstName,
} from "../lib/agentRuns";
import { readOnlyLine } from "../lib/agentQuestions";

/** The line beside the card's Submit. The chat's own line names "the assistant". */
export const AGENT_SUBMIT_HINT = "The agent reads your answer at its next step.";

export interface AgentQuestionCardProps {
  /** The question: an `elicitation` activity. */
  elicitation: AgentActivity;
  /** The run it belongs to. Only the member's address is read. */
  run: Pick<AgentRun, "member_email">;
  /** The client's name, for the fallback words of a request. */
  clientLabel: string;
  /** The prompt that answered it, if any. The card then draws one line. */
  answer?: AgentActivity | null;
  /** True while the question waits for an answer. */
  open: boolean;
  /** The server's `can_answer`. An absent flag means the old rule, so true. */
  canAnswer?: boolean;
  /** The reader is the run's member: the one person who approves a typed request. */
  archiveMine: boolean;
  /**
   * `question.kind` of a list row. The panel leaves it out, and the card
   * reads the request off the activity. The list sends the server's word.
   */
  kind?: "question" | "archive_request";
  /** A list row has no expiry stamp. The server refuses a late decision. */
  expired?: boolean;
  busy?: boolean;
  personLabels?: ReadonlyMap<string, string>;
  /** An option or free-text answer to a question. */
  onAnswer: (answers: ElicitationAnswers) => void;
  /** Approve or Decline of a typed archive request. */
  onDecision: (decision: ArchiveDecision) => void;
}

export function AgentQuestionCard({
  elicitation,
  run,
  clientLabel,
  answer = null,
  open,
  canAnswer = true,
  archiveMine,
  kind,
  expired,
  busy = false,
  personLabels,
  onAnswer,
  onDecision,
}: AgentQuestionCardProps) {
  const request = archiveRequestOf(elicitation);
  const archive = kind ? kind === "archive_request" : request !== null;
  const stale = archive && (expired ?? (request ? archiveExpired(request) : false));

  if (answer) {
    return (
      <p
        data-agent-answered=""
        className="flex items-start gap-1.5 text-xs text-muted-foreground"
      >
        <Icon name="CircleCheck" size={12} className="mt-0.5 shrink-0" aria-hidden />
        <span className="min-w-0 break-words">
          {answeredLine(answer, elicitation, personLabels)}
        </span>
      </p>
    );
  }
  if (!open) return null;

  if (!archive) {
    if (!canAnswer) {
      const who = firstName(run.member_email, personLabels) || "The member of this run";
      return (
        <div
          data-agent-question="readonly"
          className="space-y-1 rounded-md border border-border p-2 text-xs"
        >
          <p className="min-w-0 whitespace-pre-wrap break-words text-foreground">
            {elicitation.content.body?.trim() || "The agent asks a question."}
          </p>
          <p className="text-muted-foreground" data-agent-question-readonly="">
            {readOnlyLine(who)}
          </p>
        </div>
      );
    }
    return (
      <ElicitationCard
        id={`agent-run:${elicitation.id}`}
        questions={[cardQuestion(elicitation)]}
        onSubmit={onAnswer}
        disabled={busy}
        hint={AGENT_SUBMIT_HINT}
      />
    );
  }

  if (stale) {
    return (
      <div data-archive-request="expired" className="space-y-1.5 text-xs">
        <p className="flex items-start gap-1.5 text-muted-foreground">
          <Icon name="Clock" size={12} className="mt-0.5 shrink-0" aria-hidden />
          <span className="min-w-0 break-words">{ARCHIVE_EXPIRED_TEXT}</span>
        </p>
        {archiveMine ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              icon="X"
              disabled={busy}
              data-archive-decline=""
              onClick={() => onDecision("decline")}
            >
              Decline
            </Button>
            <span className="text-[10px] text-muted-foreground">{ARCHIVE_EXPIRED_HINT}</span>
          </div>
        ) : null}
      </div>
    );
  }

  if (archiveMine) {
    return (
      <div data-archive-request="mine">
        <ElicitationCard
          id={`agent-run:${elicitation.id}`}
          questions={[archiveCardQuestion(elicitation)]}
          onSubmit={(answers) => {
            const decision = archiveDecisionOf(elicitation, answers);
            if (decision) onDecision(decision);
          }}
          disabled={busy}
          hint={ARCHIVE_HINT}
          title={ARCHIVE_CARD_TITLE}
        />
      </div>
    );
  }

  // Every other member reads the request and who must answer it.
  return (
    <div
      data-archive-request="waiting"
      className="space-y-1 rounded-md border border-border p-2 text-xs"
    >
      <p className="flex items-start gap-1.5 text-foreground">
        <Icon name="Archive" size={12} className="mt-0.5 shrink-0" aria-hidden />
        <span className="min-w-0 whitespace-pre-wrap break-words">
          {elicitation.content.body?.trim() || `${clientLabel} asks to archive this task.`}
        </span>
      </p>
      <p className="text-muted-foreground" data-archive-waiting="">
        {archiveWaitingText(run, personLabels)}
      </p>
    </div>
  );
}
