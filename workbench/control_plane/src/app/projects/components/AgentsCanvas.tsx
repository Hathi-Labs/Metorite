"use client";

import Button from "@/components/ui/Button";
import { useAgentLensesOn } from "@/lib/shell/shellPrefs";
import { type AgentMapRead, MAP_ERROR_COPY, PROJECT_MAP_EMPTY_COPY } from "../lib/agentMap";
import { AgentLenses } from "./AgentLenses";
import { AgentMapView } from "./AgentMapView";
import { usePhone } from "./AgentsAtWork";

/**
 * The Agents canvas of a project (WS-MCP SW-112, `agent_coordination.md` §3.12.7).
 * It draws the SW-103 map for the subtree of the selected project. It only reads.
 * The page owns the read, so the tab row knows if the map is dark. This
 * component never draws for a dark read: the page drops the canvas first.
 *
 * WS-MCP AMR-1: with `AGENT_LENSES_ORGS` on for the org (the shell read), the
 * canvas draws `AgentLenses` over the same read. Off, it draws the SW-103 map.
 */
export function AgentsCanvas({
  read,
  showQuiet,
  onShowQuiet,
  onOpenTask,
  onRetry,
  onOpenProject,
  projectId,
  personLabels,
  onPeopleSeen,
}: {
  read: AgentMapRead;
  showQuiet: boolean;
  onShowQuiet: (next: boolean) => void;
  onOpenTask?: (taskId: string) => void;
  /** Reads the map again after an error, and after an answer in the lenses. */
  onRetry?: () => void;
  onOpenProject?: (projectId: string) => void;
  projectId?: string;
  personLabels?: ReadonlyMap<string, string>;
  onPeopleSeen?: (emails: string[]) => void;
}) {
  const phone = usePhone();
  const lenses = useAgentLensesOn();
  if (read.kind === "error") {
    return (
      <section aria-label="Agents on this project" data-agents-canvas="" className="flex flex-col gap-2 p-3">
        <p role="alert" data-agent-map-error="" className="text-sm text-foreground">
          {MAP_ERROR_COPY}
        </p>
        {onRetry ? (
          <div>
            <Button size="sm" variant="ghost" onClick={onRetry}>
              Read again
            </Button>
          </div>
        ) : null}
      </section>
    );
  }
  if (lenses) {
    return (
      <section aria-label="Agents on this project" data-agents-canvas="" className="flex h-full min-h-0 flex-1 flex-col">
        <AgentLenses
          read={read}
          onOpenTask={(id) => (onOpenTask ? onOpenTask(id) : window.location.assign(`/projects?task=${encodeURIComponent(id)}`))}
          onOpenProject={onOpenProject}
          projectId={projectId}
          personLabels={personLabels}
          onPeopleSeen={onPeopleSeen}
          onRefresh={onRetry}
          emptyCopy={PROJECT_MAP_EMPTY_COPY}
        />
      </section>
    );
  }
  return (
    <section aria-label="Agents on this project" data-agents-canvas="" className="flex h-full min-h-0 flex-1 flex-col">
      <AgentMapView
        read={read}
        showQuiet={showQuiet}
        onShowQuiet={onShowQuiet}
        phone={phone}
        onOpenTask={onOpenTask}
        emptyCopy={PROJECT_MAP_EMPTY_COPY}
      />
    </section>
  );
}

export default AgentsCanvas;
