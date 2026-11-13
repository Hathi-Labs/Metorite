/**
 * The plan of an agent run (SW-32, task 1049, `agent_coordination.md`): the
 * checklist that the agent keeps with `set_run_plan`, drawn in the run panel.
 *
 * The step text is the agent's own, so it is drawn as text and nothing else.
 * Each status has its own icon AND its own word for a screen reader, so the
 * colour is never the only mark. The component decides nothing: the count
 * comes from `planProgressText`.
 */

import Icon from "@/components/Icon";

import {
  type AgentPlanStatus,
  type AgentPlanStep,
  planProgressText,
  planSummary,
} from "../lib/agentRuns";

const STATUS_ICON: Record<AgentPlanStatus, string> = {
  pending: "Circle",
  inProgress: "CircleDot",
  completed: "CircleCheck",
  canceled: "CircleX",
};

const STATUS_WORD: Record<AgentPlanStatus, string> = {
  pending: "Not started",
  inProgress: "In progress",
  completed: "Done",
  canceled: "Canceled",
};

const STATUS_CLASS: Record<AgentPlanStatus, string> = {
  pending: "text-muted-foreground",
  inProgress: "text-primary",
  completed: "text-success",
  canceled: "text-muted-foreground line-through",
};

/** True for a status that this client knows, so a new one never breaks the list. */
function known(status: string): status is AgentPlanStatus {
  return Object.prototype.hasOwnProperty.call(STATUS_ICON, status);
}

export default function AgentRunPlan({ plan }: { plan?: AgentPlanStep[] | null }) {
  const steps = (plan ?? []).filter((s) => known(s.status));
  if (steps.length === 0) return null;
  const progress = planProgressText(planSummary(steps));
  return (
    <div data-agent-plan="" className="space-y-1 text-xs">
      <p className="font-medium text-foreground" data-agent-plan-count="">
        {progress ? `Plan · ${progress}` : "Plan"}
      </p>
      <ul className="space-y-0.5">
        {steps.map((step, i) => (
          <li
            key={i}
            data-plan-step={step.status}
            className={`flex min-w-0 items-start gap-1.5 ${STATUS_CLASS[step.status]}`}
          >
            <Icon name={STATUS_ICON[step.status]} size={12} className="mt-0.5 shrink-0" aria-hidden />
            <span className="sr-only">{`${STATUS_WORD[step.status]}: `}</span>
            <span className="min-w-0 break-words">{step.content}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
