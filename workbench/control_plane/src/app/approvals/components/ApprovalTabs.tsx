"use client";

/**
 * The tab bar of /approvals (WS-55 EA-18c). It draws two tabs and owns no
 * data: the page owns the count, the queue and the list. The words and the
 * rules are in `lib/approvalTabs.ts`.
 */

import Button from "@/components/ui/Button";

import { ACTIONS_TAB_LABEL, type ApprovalTab, questionsTabLabel } from "../lib/approvalTabs";

export interface ApprovalTabsProps {
  active: ApprovalTab;
  /** The "For me" count of the route, or undefined for none. */
  questionCount: number | undefined;
  onPick: (tab: ApprovalTab) => void;
}

export function ApprovalTabs({ active, questionCount, onPick }: ApprovalTabsProps) {
  return (
    <div role="tablist" aria-label="Approvals" className="mb-4 flex items-center gap-1" data-approval-tabs="">
      <Button
        role="tab"
        aria-selected={active === "actions"}
        size="sm"
        variant={active === "actions" ? "secondary" : "ghost"}
        data-approval-tab="actions"
        onClick={() => onPick("actions")}
      >
        {ACTIONS_TAB_LABEL}
      </Button>
      <Button
        role="tab"
        aria-selected={active === "questions"}
        size="sm"
        variant={active === "questions" ? "secondary" : "ghost"}
        data-approval-tab="questions"
        onClick={() => onPick("questions")}
      >
        {questionsTabLabel(questionCount)}
      </Button>
    </div>
  );
}
