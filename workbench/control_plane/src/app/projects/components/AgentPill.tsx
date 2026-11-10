"use client";

/**
 * The agent pill on a task (WS-55 EA-12c, `external_agent_access.md` §12.1).
 *
 * It says what the newest open agent run of the task does now: it waits for
 * a person, it works, it is idle, it failed. The List, the Board, the
 * Calendar and the task detail draw this one component.
 *
 * The shape is the shared badge's (`BADGE_SHAPE`), as `EntityPill` does it.
 * The colour comes from `agentStateChip` in `statusAccent.ts`, the one status
 * vocabulary. The words come from `agentPillText` in `lib/agentRuns.ts`.
 * Nothing here names a colour.
 *
 * Renders nothing for a task with no open run.
 */

import Icon from "@/components/Icon";
import { BADGE_SHAPE } from "@/components/ui/Badge";
import { agentStateChip, agentStateVisual } from "@/lib/statusAccent";

import { type AgentPillFacts, agentPillText, asAgentState } from "../lib/agentRuns";

export interface AgentPillProps extends AgentPillFacts {
  state?: string | null;
  /** `xs` in a dense row or card, `sm` in the task detail header. */
  size?: "xs" | "sm";
  className?: string;
}

const SIZE = {
  xs: { chip: "px-1 py-0.5 text-[10px]", icon: 10 },
  sm: { chip: "px-1.5 py-0.5 text-[10px]", icon: 11 },
} as const;

export function AgentPill({ state, size = "xs", className = "", ...facts }: AgentPillProps) {
  const known = asAgentState(state);
  if (!known) return null;
  const { text, title } = agentPillText(known, facts);
  const visual = agentStateVisual(known);
  const dims = SIZE[size];
  return (
    <span
      data-agent-pill={known}
      title={title}
      className={`${BADGE_SHAPE} max-w-full shrink-0 whitespace-nowrap ${dims.chip} ${agentStateChip(known)} ${className}`}
    >
      {visual ? <Icon name={visual.icon} size={dims.icon} className="shrink-0" aria-hidden /> : null}
      <span className="truncate">{text}</span>
    </span>
  );
}
