"use client";

/**
 * My Tasks · the way back to the board (my_tasks_cutover.md §4.8 point 4).
 *
 * A card names its project, and under the lens the name is a LINK to that
 * board with the task open — the same `?task=` deep link the Projects bell
 * and the copy-link affordance already emit (`taskDeepLink`), so there is one
 * spelling of "open this on the board" and not a third.
 *
 * With the lens off the label stays a label. The old store's `projectId` is a
 * `gtd_projects` row, and `/projects?task=<gtd id>` would open nothing.
 */

import Link from "next/link";

import Icon from "@/components/Icon";
import { taskDeepLink } from "@/app/projects/lib/card";

import { lensEnabled } from "../lib/lens";
import type { GtdItem } from "../lib/types";

export function ProjectLabel({
  item,
  name,
  className,
  nameClass,
}: {
  item: Pick<GtdItem, "id">;
  name: string;
  className: string;
  nameClass: string;
}) {
  const body = (
    <>
      <Icon name="FolderKanban" className="h-3 w-3" />
      <span className={nameClass}>{name}</span>
    </>
  );
  if (!lensEnabled()) {
    return <span className={className}>{body}</span>;
  }
  return (
    <Link
      href={taskDeepLink(item)}
      title={`Open ${name} on the board`}
      // The card around this opens the task on click. A link that also
      // bubbled would do both, and the board would open under a modal.
      onClick={(e) => e.stopPropagation()}
      className={`${className} hover:text-foreground hover:underline`}
    >
      {body}
    </Link>
  );
}
