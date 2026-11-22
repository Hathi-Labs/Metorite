"use client";

import Button from "@/components/ui/Button";
import { type AgentMapRead, MAP_ERROR_COPY, PROJECT_MAP_EMPTY_COPY } from "../lib/agentMap";
import { AgentMapView } from "./AgentMapView";
import { usePhone } from "./AgentsAtWork";

/**
 * The Agents canvas of a project (WS-MCP SW-112, `agent_coordination.md` §3.12.7).
 * It draws the SW-103 map for the subtree of the selected project. It only reads.
 * The page owns the read, so the tab row knows if the map is dark. This
 * component never draws for a dark read: the page drops the canvas first.
 */
export function AgentsCanvas({
  read,
  showQuiet,
  onShowQuiet,
  onOpenTask,
  onRetry,
}: {
  read: AgentMapRead;
  showQuiet: boolean;
  onShowQuiet: (next: boolean) => void;
  onOpenTask?: (taskId: string) => void;
  /** Reads the map again after an error. */
  onRetry?: () => void;
}) {
  const phone = usePhone();
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
