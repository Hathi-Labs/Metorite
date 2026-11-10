"use client";

/**
 * The proposals of the members on one question (WS-53 CRM-Q32-8, spec
 * crm_platform.md §18.14.10): what each one would answer, "Proposed by
 * <name> · <when>", and a one-click Confirm. The name is the display name
 * from the server, or "A member", never an email.
 *
 * One component for each place that shows a question: the card of Open
 * questions, a part of "Your answers", and the picker of a sum. It takes
 * props only, so a test reads its markup.
 */

import Button from "@/components/ui/Button";

import {
  CONFIRM_LABEL,
  PROPOSALS_HEADING,
  confirmLabel,
  proposalLine,
  proposalValueLabel,
  proposalsOf,
  type Question,
  type QuestionProposal,
} from "../lib/questions";

export function ProposalList({
  q,
  disabled,
  onConfirm,
  now,
}: {
  q: Question;
  disabled: boolean;
  onConfirm: (p: QuestionProposal) => void;
  now?: Date;
}) {
  const list = proposalsOf(q);
  if (list.length === 0) return null;
  return (
    <div className="min-w-0 space-y-1.5 rounded-md border border-border p-2.5" data-proposals="">
      <p className="text-[11px] font-medium text-muted-foreground">{PROPOSALS_HEADING}</p>
      <ul className="space-y-2" aria-label={PROPOSALS_HEADING}>
        {list.map((p) => (
          <li key={`${p.name}:${p.at}`} className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <div className="min-w-0 flex-1">
              <p className="break-words text-xs font-medium text-foreground">{proposalValueLabel(q, p)}</p>
              <p className="break-words text-[11px] text-muted-foreground">{proposalLine(p, now)}</p>
            </div>
            <Button
              variant="secondary"
              size="sm"
              icon="Check"
              disabled={disabled}
              aria-label={confirmLabel(q, p)}
              onClick={() => onConfirm(p)}
            >
              {CONFIRM_LABEL}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
