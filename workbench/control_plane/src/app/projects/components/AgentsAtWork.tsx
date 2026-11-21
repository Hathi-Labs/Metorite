"use client";

/**
 * Projects · Agents at work (WS-AGA AGA-8, `agents_view.md` §6).
 *
 * Reading this as: a lens of the project board, not a canvas. It takes the
 * place of the board while it is on, under the same project header. One row
 * for each agent the member may see: its name, its state, its last run and
 * when it ran. Its neighbour is the board it replaces, and it reads like the
 * Needs attention list beside it.
 *
 * ⚠️ **Read only.** No control acts on an agent. The gateway decides which
 * agents a member sees (D12), and this file draws the rows it gets.
 *
 * ⚠️ **Dark means absent.** While the read answers 404, 401 or 403, the
 * toggle and the section draw nothing at all.
 *
 * It extends the Agents panel of SW-8 and SW-51 when they land. Until then it
 * is a minimal section of Projects. The chips come from `statusAccent.ts`.
 */
import { useEffect, useState } from "react";

import { relativeTime } from "@/lib/taskCard";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";

import { agentRecentRunsApi, agentsOverviewApi } from "../lib/api";
import {
  type AgentAtWork,
  type AgentRunsRead,
  type AgentsAtWorkRead,
  RUNS_EMPTY_COPY,
  RUNS_ERROR_COPY,
  RUNS_TITLE,
  RUNS_TRUNCATED_COPY,
  RUNS_UNAVAILABLE_COPY,
  runTimeWord,
  EMPTY_COPY,
  ERROR_COPY,
  SECTION_TITLE,
  TRUNCATED_COPY,
  lastRunLine,
  outcomeWord,
  outcomeChip,
  outcomeIcon,
  runsWord,
  stateChip,
  stateIcon,
  stateWord,
} from "../lib/agentsAtWork";

/** One read of the overview, when the page opens. */
export function useAgentsAtWork(): AgentsAtWorkRead {
  const [read, setRead] = useState<AgentsAtWorkRead>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    agentsOverviewApi
      .read()
      .then((overview) => {
        if (live) setRead(overview ? { kind: "ready", overview } : { kind: "dark" });
      })
      .catch(() => {
        if (live) setRead({ kind: "error" });
      });
    return () => {
      live = false;
    };
  }, []);
  return read;
}

/** One read of the last runs of one agent, when its list opens. */
export function useAgentRuns(agentKey: string): AgentRunsRead {
  const [read, setRead] = useState<AgentRunsRead>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    agentRecentRunsApi
      .read(agentKey)
      .then((runs) => {
        if (live) setRead(runs ? { kind: "ready", runs } : { kind: "unavailable" });
      })
      .catch(() => {
        if (live) setRead({ kind: "error" });
      });
    return () => {
      live = false;
    };
  }, [agentKey]);
  return read;
}

const CHIP = "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium";

/** The last runs of one agent. Lean rows: no trace, which stays executive-only. */
export function AgentRunsView({ read, nowMs = Date.now() }: { read: AgentRunsRead; nowMs?: number }) {
  return (
    <div data-agent-runs="" aria-label={RUNS_TITLE} className="basis-full pt-1">
      {read.kind === "loading" ? (
        <Skeleton className="h-6 w-full" />
      ) : read.kind === "error" ? (
        <p role="alert" className="text-xs text-foreground">
          {RUNS_ERROR_COPY}
        </p>
      ) : read.kind === "unavailable" ? (
        <p className="text-xs text-muted-foreground">{RUNS_UNAVAILABLE_COPY}</p>
      ) : read.runs.runs.length === 0 ? (
        <p className="text-xs text-muted-foreground">{RUNS_EMPTY_COPY}</p>
      ) : (
        <>
          <ul className="divide-y divide-border">
            {read.runs.runs.map((run) => (
              <li
                key={run.id}
                data-agent-run={run.id}
                className="flex flex-wrap items-center gap-x-3 gap-y-0.5 py-1 text-xs"
              >
                <span className={`${CHIP} ${outcomeChip(run.state)}`}>{outcomeWord(run.state)}</span>
                <span className="text-muted-foreground">
                  {run.started_at ? relativeTime(run.started_at, nowMs) : "Not started"}
                </span>
                <span className="tabular-nums text-muted-foreground">{runTimeWord(run.run_seconds)}</span>
                {run.task ? <span className="min-w-0 flex-1 truncate text-foreground">{run.task.title}</span> : null}
                {run.error ? (
                  <span data-agent-run-error="" className="basis-full text-foreground">
                    {run.error.what}
                    {run.error.next_step ? ` ${run.error.next_step}` : ""}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
          {read.runs.truncated ? (
            <p className="pt-1 text-xs text-muted-foreground">{RUNS_TRUNCATED_COPY}</p>
          ) : null}
        </>
      )}
    </div>
  );
}

function AgentRuns({ agentKey, nowMs }: { agentKey: string; nowMs: number }) {
  return <AgentRunsView read={useAgentRuns(agentKey)} nowMs={nowMs} />;
}

function AgentRow({ agent, nowMs }: { agent: AgentAtWork; nowMs: number }) {
  const [open, setOpen] = useState(false);
  const outcome = agent.last_run?.state ?? "";
  const glyph = outcomeIcon(outcome);
  return (
    <li
      data-agent-row={agent.key}
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-3 py-2"
    >
      <div className="min-w-0 flex-1 basis-48">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate text-sm font-medium text-foreground">{agent.name}</span>
          {agent.label ? (
            <span className="text-xs text-muted-foreground">{agent.label}</span>
          ) : null}
        </div>
        {agent.job ? (
          <p className="truncate text-xs text-muted-foreground">{agent.job}</p>
        ) : null}
        {agent.last_error ? (
          <p data-agent-error="" className="mt-1 text-xs text-foreground">
            {agent.last_error.what}
            {agent.last_error.next_step ? ` ${agent.last_error.next_step}` : ""}
          </p>
        ) : null}
      </div>
      <span
        data-agent-state={agent.state}
        className={`${CHIP} w-40 justify-center ${stateChip(agent.state)}`}
      >
        <Icon name={stateIcon(agent.state)} size={12} />
        {stateWord(agent.state)}
      </span>
      <div className="flex w-44 flex-col items-start gap-0.5 text-xs text-muted-foreground">
        {agent.last_run && outcome.trim().toLowerCase() === "failed" ? (
          <span data-agent-last-run="" className={`${CHIP} ${outcomeChip(outcome)}`}>
            {glyph ? <Icon name={glyph} size={12} /> : null}
            {lastRunLine(agent.last_run, nowMs)}
          </span>
        ) : (
          <span data-agent-last-run="">{lastRunLine(agent.last_run, nowMs)}</span>
        )}
        <span className="tabular-nums">{runsWord(agent.runs)}</span>
        <Button size="sm" variant="ghost" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? "Hide runs" : RUNS_TITLE}
        </Button>
      </div>
      {open ? <AgentRuns agentKey={agent.key} nowMs={nowMs} /> : null}
    </li>
  );
}

export interface AgentsAtWorkViewProps {
  read: AgentsAtWorkRead;
  onBack?: () => void;
  nowMs?: number;
}

/** The section. It draws nothing while the feature is dark. */
export function AgentsAtWorkView({ read, onBack, nowMs = Date.now() }: AgentsAtWorkViewProps) {
  if (read.kind === "dark") return null;
  return (
    <section
      aria-label={SECTION_TITLE}
      data-agents-at-work=""
      className="flex min-h-0 flex-1 flex-col overflow-y-auto"
    >
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <h2 className="text-sm font-semibold text-foreground">{SECTION_TITLE}</h2>
        {onBack ? (
          <Button className="ml-auto" size="sm" variant="ghost" onClick={onBack}>
            Back to the board
          </Button>
        ) : null}
      </div>
      {read.kind === "loading" ? (
        <div className="space-y-2 p-3">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : read.kind === "error" ? (
        <p role="alert" data-agents-error="" className="p-3 text-sm text-foreground">
          {ERROR_COPY}
        </p>
      ) : read.overview.agents.length === 0 ? (
        <p data-agents-empty="" className="p-3 text-sm text-muted-foreground">
          {EMPTY_COPY}
        </p>
      ) : (
        <>
          <ul>
            {read.overview.agents.map((agent) => (
              <AgentRow key={agent.key} agent={agent} nowMs={nowMs} />
            ))}
          </ul>
          {read.overview.truncated ? (
            <p className="p-3 text-xs text-muted-foreground">{TRUNCATED_COPY}</p>
          ) : null}
        </>
      )}
    </section>
  );
}

export default AgentsAtWorkView;
