"use client";

/**
 * My Tasks · where an Inbox row comes from (my_tasks_cutover.md §5 S6g).
 *
 * Every capture used to wear a "Local" `SourceBadge`, a D52 leftover that
 * said the same thing on every row. The Inbox now holds two kinds, and the
 * marker says which:
 *
 * - **Personal**: a lock badge. Only I can see it.
 * - **Board**: the project, linked to the board with the task open
 *   (`ProjectLabel`, `taskDeepLink`), then who put it on my plate.
 *
 * Both kinds also show the task's priority level, D78's `PriorityBadge`,
 * with the card face's own rule: the default low-priority cell draws
 * nothing. The Inbox is for triage, and Low is not news.
 */

import Badge from "@/components/ui/Badge";

import { type InboxKind, assignedByLabel } from "../lib/inbox";
import { useTaskStore } from "../lib/taskStore";
import type { MyTask } from "../lib/types";
import { PriorityBadge } from "./PriorityControls";
import { ProjectLabel } from "./ProjectLabel";

export function InboxOrigin({ item, kind }: { item: MyTask; kind: InboxKind }) {
  const projects = useTaskStore((s) => s.projects);
  const projectName =
    item.projectName ?? projects.find((p) => p.id === item.projectId)?.outcome ?? "a board";
  const from = assignedByLabel(item.assignedBy);
  return (
    <span className="inline-flex min-w-0 max-w-full flex-wrap items-center gap-x-1.5 gap-y-0.5">
      {kind === "personal" ? (
        <Badge tone="neutral" icon="Lock" size="xs" title="Only you can see this task">
          Personal
        </Badge>
      ) : (
        <>
          <ProjectLabel
            item={item}
            name={projectName}
            className="inline-flex min-w-0 max-w-[16rem] items-center gap-1 text-muted-foreground"
            nameClass="truncate"
          />
          {from ? <span className="whitespace-nowrap text-muted-foreground">{from}</span> : null}
        </>
      )}
      <PriorityBadge item={item} hideLowPriority />
    </span>
  );
}
