"use client";

/**
 * The agent summary strip of a project (WS-55 EA-13e, EA3-43,
 * `external_agent_access.md` §12.2 item 4).
 *
 * Five counts over the tasks of the project and its subprojects that the
 * member may see: what waits on them, what an agent works on, what waits for
 * an agent, what is in review, and what merged this week. The gateway counts
 * them (`GET /projects/{id}/agent-summary`), so a count never names a task
 * that the board hides. Each count is a button that sets the matching filter
 * of the board, and a second press clears it.
 *
 * It draws nothing while every count is zero, so a project with no agent work
 * shows no strip. While a filter is on it stays, so the member can turn the
 * filter off. The controls are the shared `Button`, and the words carry the
 * meaning, so nothing here depends on colour.
 */

import Button from "@/components/ui/Button";

import { visibleAgentItems } from "../lib/compactChrome";
import {
  AGENT_STRIP,
  type AgentFocus,
  type AgentSummary,
  stripVisible,
} from "../lib/agentRuns";

interface Props {
  summary: AgentSummary | null;
  /** The count button that is on, or null. */
  focus: AgentFocus | null;
  onFocus: (next: AgentFocus | null) => void;
  /**
   * WS-64 OP-1 (the compact chrome): a count of zero draws no button, except
   * the one that is on. Off, the strip draws all five as it always did.
   */
  hideZero?: boolean;
}

export function AgentWorkStrip({ summary, focus, onFocus, hideZero = false }: Props) {
  if (!summary || !(stripVisible(summary) || focus)) return null;
  return (
    <div
      role="group"
      aria-label="Agent work in this project"
      data-agent-work-strip=""
      className="flex flex-wrap items-center gap-1.5 border-b border-border px-3 py-1.5"
    >
      {(hideZero ? visibleAgentItems(summary, focus) : AGENT_STRIP).map((item) => {
        const count = summary[item.field] ?? 0;
        const on = focus === item.focus;
        return (
          <Button
            key={item.focus}
            size="sm"
            variant={on ? "primary" : "secondary"}
            icon={item.icon}
            aria-pressed={on}
            // A zero count has no rows to show. It stays pressable only while it
            // is the filter that is on, so the member can always turn it off.
            disabled={count === 0 && !on}
            title={item.title}
            data-agent-count={item.focus}
            onClick={() => onFocus(on ? null : item.focus)}
          >
            <span className="tabular-nums">{count}</span> {item.label}
          </Button>
        );
      })}
    </div>
  );
}

export default AgentWorkStrip;
