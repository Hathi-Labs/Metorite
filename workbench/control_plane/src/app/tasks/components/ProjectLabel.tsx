"use client";

/**
 * My Tasks · the way back to the board (my_tasks_cutover.md §4.8 point 4).
 *
 * A card names its project, and the name is a LINK to that board with the
 * task open — the same `?task=` deep link the Projects bell and the
 * copy-link affordance already emit (`taskDeepLink`), so there is one
 * spelling of "open this on the board" and not a third.
 */

import Link from "next/link";

import Icon from "@/components/Icon";
import { taskDeepLink } from "@/app/projects/lib/card";

import type { MyTask } from "../lib/types";

export function ProjectLabel({
  item,
  name,
  className,
  nameClass,
}: {
  item: Pick<MyTask, "id">;
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
