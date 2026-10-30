"use client";

/**
 * Projects · the project a row comes from (WS-64 OP-11).
 *
 * My tasks draws rows from many projects, so each row and card names its
 * project. The path keeps the space and the project in sight and cuts the
 * middle (`myTasks.projectChipText`). The whole path is the tooltip. It wears
 * the muted meta tone of the other card chips, so it adds no colour.
 */

import Icon from "@/components/Icon";

import { projectChipText } from "../lib/myTasks";
import { PRIVATE_TASK_LABEL } from "../lib/personalTree";

export function ProjectPathChip({
  path,
  isPrivate = false,
}: {
  path: readonly string[] | null | undefined;
  /** OP-12b — the task sits in the private space: a small lock, labelled "Private". */
  isPrivate?: boolean;
}) {
  const text = projectChipText(path ?? []);
  if (!text) return null;
  return (
    <span
      data-project-chip=""
      title={text.title}
      className="inline-flex min-w-0 max-w-full items-center gap-1 text-[10px] text-muted-foreground"
    >
      <Icon name="FolderKanban" className="h-3 w-3 shrink-0" aria-hidden />
      <span className="truncate">{text.label}</span>
      {isPrivate ? (
        <Icon name="Lock" className="h-3 w-3 shrink-0" role="img" aria-label={PRIVATE_TASK_LABEL} />
      ) : null}
    </span>
  );
}

export default ProjectPathChip;
