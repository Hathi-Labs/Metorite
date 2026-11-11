"use client";

/**
 * WS-MCP SW-23 — the Check of a task, read only, in the task body.
 *
 * A Check is a read-only command that proves the work is still open. An agent
 * runs it and reports the result over MCP (`report_check`). This block shows
 * the command, the last result and the day of the last run. It writes
 * nothing, so it adds no UI action and no chat tool (chat parity, rule 15).
 *
 * It draws NOTHING while the gateway's Check flag is off (the row has no
 * `check` key) and for a task with no Check (`check` is null). The look is
 * the one of `DecisionPanel`: a bordered card section with one heading row.
 */
import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";

import type { TaskCheck } from "../lib/api";
import { checkSummary } from "../lib/taskCheck";

export function TaskCheckBlock({ check }: { check?: TaskCheck | null }) {
  if (!check) return null;
  const { result, when } = checkSummary(check);
  return (
    <section
      aria-label="Check"
      data-task-check=""
      className="mb-3 space-y-2 rounded-lg border border-border bg-card p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Icon name="SquareTerminal" size={14} className="shrink-0 text-muted-foreground" aria-hidden />
        <span className="text-sm font-medium text-foreground">Check</span>
        {result ? (
          <Badge tone={result.tone} icon={result.icon} size="sm">
            {result.label}
          </Badge>
        ) : null}
        <span className="text-xs text-muted-foreground">{when}</span>
      </div>
      <p className="whitespace-pre-wrap break-words rounded-md bg-muted px-2 py-1 font-mono text-xs text-foreground">
        {check.text}
      </p>
      {check.last_evidence ? (
        <p className="line-clamp-4 whitespace-pre-wrap break-words text-xs text-muted-foreground">
          {check.last_evidence}
        </p>
      ) : null}
    </section>
  );
}
