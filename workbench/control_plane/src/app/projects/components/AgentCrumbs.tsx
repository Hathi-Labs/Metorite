/**
 * The Agents view · the breadcrumb, the counts and the question pager
 * (WS-MCP AMR-4, §3.12.9). "All agents", then a person, then an orchestrator.
 * The pager walks the questions that wait on the member, the oldest first: each
 * step opens that session's orchestrator and centres its card. It answers
 * nothing: the details hold the run panel. Names are untrusted text.
 */
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";

import { QUESTIONS_COPY } from "../lib/agentQuestions";
import { lensIcon } from "../lib/agentLenses";

export const ALL_AGENTS = "All agents";

export interface Crumb {
  label: string;
  /** Absent on the level that shows now. */
  onOpen?: () => void;
}

export function AgentCrumbs({
  crumbs,
  summary,
  question,
}: {
  crumbs: readonly Crumb[];
  /** "238 sessions · 799 subagents". */
  summary: string;
  question: { at: number; total: number; onGo: (by: number) => void } | null;
}) {
  return (
    // `shrink-0`: with a minimum height, the column would squeeze a wrapped bar onto the canvas.
    <div className="flex min-h-10 shrink-0 flex-wrap items-center gap-x-2 gap-y-1 border-b border-border px-3 py-1" data-agent-crumbs="">
      <nav aria-label="Agent levels" className="flex min-w-0 flex-wrap items-center gap-0.5">
        {crumbs.map((crumb, i) => (
          <span key={`${i}:${crumb.label}`} className="flex min-w-0 items-center gap-0.5">
            {i > 0 ? <Icon name="ChevronRight" size={13} className="shrink-0 text-muted-foreground" aria-hidden /> : null}
            {crumb.onOpen ? (
              <Button variant="text" size="sm" onClick={crumb.onOpen} data-agent-crumb="">
                <span className="max-w-40 truncate">{crumb.label}</span>
              </Button>
            ) : (
              <span aria-current="page" className="max-w-52 truncate px-1 text-sm font-semibold text-foreground">
                {crumb.label}
              </span>
            )}
          </span>
        ))}
      </nav>
      <span className="ml-auto text-xs text-muted-foreground" data-agent-fleet-count="">
        {summary}
      </span>
      {question && question.total > 0 ? (
        <div className="flex items-center gap-0.5" role="group" aria-label="Questions for you" data-agent-question-pager="">
          <Button
            variant="ghost"
            size="icon-sm"
            icon="ChevronLeft"
            aria-label={QUESTIONS_COPY.previous}
            onClick={() => question.onGo(-1)}
          />
          <Button variant="ghost" size="sm" onClick={() => question.onGo(0)}>
            <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
              <Icon name={lensIcon("you")} size={14} className="text-warning" aria-hidden />
              {QUESTIONS_COPY.pagerWord} {question.at + 1} of {question.total}
            </span>
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            icon="ChevronRight"
            aria-label={QUESTIONS_COPY.next}
            onClick={() => question.onGo(1)}
          />
        </div>
      ) : null}
    </div>
  );
}

export default AgentCrumbs;
